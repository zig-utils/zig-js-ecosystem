import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import discover
import report
import run
import runtime

SHA = "a" * 40
OTHER_SHA = "b" * 40


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = {"exclude_owners": ["meemalabs"], "exclude_repositories": {}}
        self.repo = {"owner": {"login": "stacksjs"}, "full_name": "stacksjs/lib", "size": 1,
                     "pushed_at": "2026-11-07T00:00:00Z"}

    def test_activity_window_and_leap_day(self):
        self.assertEqual(discover.cutoff_date(dt.date(2024, 2, 29), 2), dt.date(2022, 2, 28))
        self.assertIsNone(discover.exclusion(self.repo, self.config, dt.date(2024, 11, 7)))
        self.repo["pushed_at"] = "2024-11-06T23:59:59Z"
        self.assertEqual(discover.exclusion(self.repo, self.config, dt.date(2024, 11, 7)), "no push within activity window")

    def test_explicit_exclusions_and_forks(self):
        self.repo["fork"] = True
        self.assertIsNone(discover.exclusion(self.repo, self.config, dt.date(2024, 11, 7)))
        self.repo["owner"]["login"] = "Meemalabs"
        self.assertEqual(discover.exclusion(self.repo, self.config, dt.date(2024, 11, 7)), "excluded owner")
        self.repo["owner"]["login"] = "stacksjs"
        self.repo["private"] = True
        self.assertEqual(discover.exclusion(self.repo, self.config, dt.date(2024, 11, 7)), "private repository")

    def test_all_matrix_members_exactly_once_beyond_github_limit(self):
        for count in (1, 200, 207, 256, 257, 1201):
            repos = [{"full_name": f"org/repo{i}", "sha": SHA} for i in range(count)]
            matrix = discover.matrix_for(repos)["include"]
            self.assertLessEqual(len(matrix), 200)
            self.assertEqual([repo for leg in matrix for repo in leg["repositories"]], repos)
        with self.assertRaisesRegex(ValueError, "Zero selected"):
            discover.matrix_for([])

    def test_pagination_does_not_truncate_organizations(self):
        with patch.object(discover, "api", side_effect=[list(range(100)), [100]]) as mock:
            self.assertEqual(len(discover.pages("users/account/orgs")), 101)
            self.assertEqual(mock.call_count, 2)


class PlanTests(unittest.TestCase):
    def test_monorepo_private_packages_and_fixture_exclusions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifests = {
                "package.json": {"private": True, "scripts": {"build": "bun build.ts", "release": "never"}},
                "packages/lib/package.json": {"name": "lib", "scripts": {"test": "bun test"}},
                "storage/framework/core/cli/package.json": {"bin": {"buddy": "src/cli.ts"}},
                "tests/fixtures/package.json": {"scripts": {"test": "never"}},
            }
            for path, content in manifests.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(json.dumps(content))
            (root / "packages/lib/src").mkdir()
            (root / "packages/lib/src/index.ts").write_text("export const answer = 42")
            files = [*manifests, "packages/lib/test/math.test.ts"]
            scopes, omitted = run.package_plan(root, files)
            self.assertEqual(len(scopes), 3)
            self.assertNotIn("release", scopes[0]["scripts"])
            self.assertEqual(scopes[1]["entries"], ["src/index.ts"])
            self.assertEqual(scopes[1]["test_file_count"], 1)
            self.assertEqual(len(omitted), 1)

    def test_test_files_without_test_script_are_not_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{}')
            scopes, _ = run.package_plan(root, ["package.json", "math.test.ts"])
            self.assertEqual(scopes[0]["scripts"]["test"], "bun test")


class RuntimeRoutingTests(unittest.TestCase):
    def test_test_scheduler_and_native_build_cannot_be_false_passes(self):
        with patch.object(runtime, "event"):
            self.assertEqual(runtime.main(["test"], "bun"), 78)
            self.assertEqual(runtime.main(["--test"], "node"), 78)
            self.assertEqual(runtime.main(["build", "index.ts"], "bun"), 78)
            self.assertEqual(runtime.main(["install"], "bun"), 78)

    def test_nested_script_routes_to_engine_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(json.dumps({"scripts": {"build": "bun --bun run inner", "inner": "node src.js"}}))
            (root / "src.js").write_text('throw new Error("candidate witness")')
            fake_home = root / "fake-home"
            fake_home.write_text(f"#!{sys.executable}\nimport sys\nprint('home argv:', sys.argv[1:])\nsys.exit(19)\n")
            fake_home.chmod(0o755)
            run.make_shims(root / "shims")
            env = {**os.environ, "HOME_TOOL": str(fake_home), "PATH": str(root / "shims") + os.pathsep + os.environ["PATH"],
                   "ECOSYSTEM_EVENTS": str(root / "events")}
            result = subprocess.run([str(root / "shims/bun"), "run", "build"], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 19, result.stderr)
            events = [json.loads(line) for line in (root / "events").read_text().splitlines()]
            self.assertEqual(events[0]["kind"], "zig-js")
            self.assertIn("src.js", result.stdout)

    def test_installed_package_binary_routes_to_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "node_modules/.bin/tsc"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/usr/bin/env node\n")
            with patch.object(runtime.Path, "cwd", return_value=root), patch.object(runtime, "run_file", return_value=17) as mock:
                self.assertEqual(runtime.main(["--bun", "tsc", "--noEmit"], "bunx"), 17)
                mock.assert_called_once_with(binary.resolve(), ["--noEmit"])


class ResultTests(unittest.TestCase):
    def inventory(self):
        return {"selected": [{"full_name": "stacksjs/lib", "sha": SHA}]}

    def result(self, status="passed"):
        return {"repository": "stacksjs/lib", "sha": SHA, "engine_sha": SHA,
                "checks": [{"label": "test", "status": status, "lane": "candidate"}]}

    def test_missing_and_wrong_commit_results_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertFalse(report.summarize(self.inventory(), root, SHA)["success"])
            path = root / "stacksjs--lib.json"
            result = self.result()
            result["engine_sha"] = OTHER_SHA
            path.write_text(json.dumps(result))
            self.assertEqual(report.summarize(self.inventory(), root, SHA)["mismatched"], ["stacksjs/lib"])

    def test_unsupported_not_run_timeout_and_reference_failure_are_red(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for status in ("unsupported", "not-run", "timed-out", "failed"):
                result = self.result(status)
                (root / "stacksjs--lib.json").write_text(json.dumps(result))
                self.assertFalse(report.summarize(self.inventory(), root, SHA)["success"])
            (root / "stacksjs--lib.json").write_text(json.dumps(self.result()))
            self.assertTrue(report.summarize(self.inventory(), root, SHA)["success"])
            self.assertFalse(report.summarize({"selected": []}, root, SHA)["success"])

    def test_timeout_is_recorded_and_subprocess_group_is_reaped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = [sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"]
            result = run.execute(command, root, os.environ.copy(), root / "timeout.log", 0.1)
            self.assertEqual(result["status"], "timed-out")
            self.assertEqual(result["exit_code"], 124)

    def test_workflow_and_checked_inventory_are_consistent(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "config.json").read_text())
        self.assertRegex(config["home_ref"], r"^[a-f0-9]{40}$")
        inventory = json.loads((root / "inventory.json").read_text())
        self.assertGreater(len(inventory["selected"]), 0)
        self.assertTrue(any(r["full_name"] == "stacksjs/stacks" for r in inventory["selected"]))
        self.assertTrue(any(r["full_name"] == "home-lang/home" for r in inventory["selected"]))
        self.assertFalse(any(r["full_name"].startswith("meemalabs/") for r in inventory["selected"]))


if __name__ == "__main__":
    unittest.main()
