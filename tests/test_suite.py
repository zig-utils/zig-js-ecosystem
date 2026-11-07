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

    def test_priority_consumers_have_first_independent_jobs_without_lost_coverage(self):
        repos = [{"full_name": f"org/repo{i}", "sha": SHA} for i in range(500)]
        priority = ["org/repo48", "org/repo307"]
        legs = discover.matrix_for(repos, priority)["include"]
        self.assertEqual([leg["repositories"][0]["full_name"] for leg in legs[:2]], priority)
        self.assertTrue(all(len(leg["repositories"]) == 1 for leg in legs[:2]))
        flattened = [repo["full_name"] for leg in legs for repo in leg["repositories"]]
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), {repo["full_name"] for repo in repos})
        self.assertLessEqual(len(legs), 200)

    def test_manual_subset_is_explicit_and_rejects_unknown_consumers(self):
        inventory = {"selected": [{"full_name": "stacksjs/stacks"}, {"full_name": "home-lang/home"}], "excluded": []}
        focused = discover.focus_inventory(inventory, ["home-lang/home"])
        self.assertEqual(focused["full_selected_count"], 2)
        self.assertEqual(focused["selected"], [{"full_name": "home-lang/home"}])
        self.assertEqual(focused["excluded"][0]["reason"], "outside this manual focused reproduction")
        with self.assertRaisesRegex(ValueError, "not in the active"):
            discover.focus_inventory(inventory, ["unknown/repo"])

    def test_push_trigger_cannot_filter_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "inventory.json"
            process = subprocess.run([sys.executable, str(discover.ROOT / "scripts/discover.py"),
                                      "--from-inventory", str(discover.ROOT / "inventory.json"),
                                      "--only", "home-lang/home", "--output", str(output)],
                                     env={**os.environ, "GITHUB_EVENT_NAME": "push"}, capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("complete inventory", process.stderr)
            self.assertFalse(output.exists())


class PlanTests(unittest.TestCase):
    def test_package_binary_path_cannot_shadow_candidate_runtime(self):
        root = Path('/fixture')
        env = run.script_environment({"PATH": "/system"}, root / "packages/lib", root, root / "shims")
        paths = env["PATH"].split(os.pathsep)
        self.assertEqual(paths[0], "/fixture/shims")
        self.assertIn("/fixture/packages/lib/node_modules/.bin", paths)
        self.assertIn("/fixture/node_modules/.bin", paths)
        self.assertNotIn("/node_modules/.bin", paths)

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
        self.assertTrue(any(r["full_name"] == "pantry-pm/pantry" for r in inventory["selected"]))
        self.assertTrue(any(r["full_name"] == "den-shell/den" for r in inventory["selected"]))
        self.assertTrue(any(r["full_name"] == "craft-native/craft" for r in inventory["selected"]))
        self.assertFalse(any(r["full_name"].startswith("meemalabs/") for r in inventory["selected"]))


class EndToEndTests(unittest.TestCase):
    def test_real_git_checkout_router_logs_and_missing_scheduler_report(self):
        # The fake host verifies orchestration only; this is never engine evidence.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", source], check=True)
            (source / "package.json").write_text(json.dumps({"scripts": {"build": "bun build.ts", "test": "bun test"}}))
            (source / "build.ts").write_text("const answer: number = 42;")
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "-qm", "fixture"], cwd=source, check=True)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
            fake_host = root / "fake-host"
            fake_host.write_text(f"#!{sys.executable}\nprint('orchestration-only host fixture')\n")
            fake_host.chmod(0o755)
            env = {**os.environ, "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": f"url.file://{source}.insteadOf",
                   "GIT_CONFIG_VALUE_0": "https://github.com/test/fixture.git"}
            process = subprocess.run([sys.executable, str(run.ROOT / "scripts/run.py"),
                                      "--repositories", json.dumps([{"full_name": "test/fixture", "sha": sha}]),
                                      "--engine-sha", SHA, "--home-tool", str(fake_host),
                                      "--bun", str(fake_host), "--engine-root", str(root / "engine"),
                                      "--work", str(root / "work"), "--output", str(root / "results")],
                                     env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            result = json.loads((root / "results/test--fixture.json").read_text())
            build = next(c for c in result["checks"] if c["label"] == ".: build")
            tests = next(c for c in result["checks"] if c["label"] == ".: test")
            self.assertEqual(build["status"], "passed", result)
            self.assertEqual(build["engine_invocations"], 1)
            self.assertEqual(tests["status"], "unsupported")
            self.assertTrue(result["failed"])
            self.assertTrue((root / "results/test--fixture" / build["log"]).is_file())


if __name__ == "__main__":
    unittest.main()
