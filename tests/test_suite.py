import datetime as dt
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import discover
import report
import run
import runtime
import provenance

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
    def test_workspace_installs_cover_members_but_not_independent_nested_apps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifests = {
                "package.json": {"workspaces": ["packages/*", "!packages/standalone"]},
                "packages/lib/package.json": {"workspaces": {"packages": ["children/*"]}},
                "packages/lib/children/nested/package.json": {},
                "packages/standalone/package.json": {},
                "templates/app/package.json": {},
            }
            for path, content in manifests.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(content))
            scopes = [{"directory": Path(path).parent.as_posix()} for path in manifests]
            self.assertEqual(run.dependency_roots(root, scopes), [".", "packages/standalone", "templates/app"])

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
    def test_scoped_package_bin_uses_installed_manifest_and_keeps_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "node_modules" / "@fixture" / "tools"
            (package / "dist").mkdir(parents=True)
            entry = package / "dist" / "different-name.mjs"
            entry.write_text("throw new Error('routing-only fixture')")
            (package / "package.json").write_text(json.dumps({"name": "@fixture/tools", "bin": {"actual-command": "./dist/different-name.mjs"}}))
            fake_home = root / "fake-home"
            fake_home.write_text(f"#!{sys.executable}\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\nsys.exit(19)\n")
            fake_home.chmod(0o755)
            run.make_shims(root / "shims")
            env = {**os.environ, "HOME_TOOL": str(fake_home)}
            result = subprocess.run([str(root / "shims" / "bunx"), "--bun", "@fixture/tools", "build", "--flag"], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 19, result.stderr)
            self.assertEqual(json.loads(result.stdout), ["run", str(entry.resolve()), "build", "--flag"])

    def test_conditions_reach_home_without_consuming_entry_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entry = root / "entry.mjs"
            entry.write_text("throw new Error('orchestration-only fixture')")
            fake_home = root / "fake-home"
            fake_home.write_text(f"#!{sys.executable}\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\nsys.exit(19)\n")
            fake_home.chmod(0o755)
            run.make_shims(root / "shims")
            env = {**os.environ, "HOME_TOOL": str(fake_home), "ECOSYSTEM_EVENTS": str(root / "events")}
            for program in ("bun", "node"):
                command = [str(root / "shims" / program), "--conditions", "development", "--conditions=first,second",
                           "--cwd", str(root), str(entry), "--conditions", "entry-argument"]
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 19, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["--conditions=development", "--conditions=first,second",
                                                            "run", str(entry.resolve()), "--conditions", "entry-argument"])
            events = [json.loads(line) for line in (root / "events").read_text().splitlines()]
            self.assertTrue(all(event["conditions"] == ["development", "first,second"] for event in events))

    def test_conditions_never_disappear_on_unsupported_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(json.dumps({"scripts": {"build": "echo never"}}))
            with patch.object(runtime.Path, "cwd", return_value=root), patch.object(runtime, "event"), patch.object(runtime.subprocess, "call") as call:
                self.assertEqual(runtime.main(["--conditions", "development", "run", "build"], "bun"), 78)
                self.assertEqual(runtime.main(["--conditions"], "bun"), 78)
                self.assertEqual(runtime.main(["--conditions=development", "test"], "bun"), 78)
                self.assertEqual(runtime.main(["--conditions=development", "install"], "bun"), 78)
                self.assertEqual(runtime.main(["--conditions=development", "x", "missing-tool"], "npm"), 78)
                call.assert_not_called()

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
                mock.assert_called_once_with(binary.resolve(), ["--noEmit"], [])


class ResultTests(unittest.TestCase):
    def test_invalid_repository_never_enters_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(work=Path(temp) / "work", output=Path(temp) / "results", engine_sha=SHA)
            with patch.object(run.shutil, "rmtree") as cleanup:
                with self.assertRaises(ValueError):
                    run.run_and_save({"full_name": "..", "sha": SHA}, args, {})
                cleanup.assert_not_called()

    def test_disk_failure_preserves_prior_evidence_and_cleans_before_saving(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(work=root / "work", output=root / "results", engine_sha=SHA)
            checkout = args.work / "stacksjs--lib"
            checkout.mkdir(parents=True)
            repo = {"full_name": "stacksjs/lib", "sha": SHA}
            def fail_late(repo, args, config, result):
                result["scopes"].append({"directory": "."})
                result["checks"].append({"label": "candidate build", "status": "failed"})
                raise OSError(28, "No space left on device")
            original_write = Path.write_text
            def write_after_cleanup(path, *values, **options):
                self.assertFalse(checkout.exists(), "JSON was written before releasing disk space")
                return original_write(path, *values, **options)
            with patch.object(run, "run_repository", side_effect=fail_late), patch.object(Path, "write_text", new=write_after_cleanup):
                result = run.run_and_save(repo, args, {})
            saved = json.loads((args.output / "stacksjs--lib.json").read_text())
            self.assertTrue(result["failed"])
            self.assertEqual(saved["scopes"], [{"directory": "."}])
            self.assertEqual(saved["checks"][0]["label"], "candidate build")
            self.assertIn("No space left", saved["checks"][1]["reason"])

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

    def test_memory_guard_counts_child_and_reaps_its_process_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = ("import os,time; from pathlib import Path; "
                     "Path('child.pid').write_text(str(os.getpid())); "
                     "payload=bytearray(64*1024*1024); time.sleep(60)")
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(60)"
            result = run.execute([sys.executable, "-c", parent, child], root, os.environ.copy(),
                                 root / "memory.log", 10, memory_limit_mb=48)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["exit_code"], -signal.SIGKILL)
            self.assertGreater(result["peak_sampled_rss_kib"], 48 * 1024)
            self.assertEqual(result["memory_limit_mb"], 48)
            self.assertIn("RSS exceeded 48 MiB", result["reason"])
            self.assertIn("ECOSYSTEM LIMIT:", (root / "memory.log").read_text())
            child_pid = int((root / "child.pid").read_text())
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)],
                                   capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)

    def test_memory_guard_retains_actual_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for code in (0, 7):
                result = run.execute([sys.executable, "-c", f"raise SystemExit({code})"], root,
                                     os.environ.copy(), root / f"exit-{code}.log", 10, memory_limit_mb=128)
                self.assertEqual(result["exit_code"], code)
                self.assertEqual(result["status"], "passed" if code == 0 else "failed")
                self.assertNotIn("reason", result)

    def test_memory_guard_waits_for_delayed_group_member_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = ("import os,time; from pathlib import Path; "
                     "Path('child.pid').write_text(str(os.getpid())); "
                     "payload=bytearray(64*1024*1024); time.sleep(60)")
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(60)"
            pending = []
            def delayed_group_signal(group, signum):
                self.assertEqual(signum, signal.SIGKILL)
                child_pid = int((root / "child.pid").read_text())
                self.assertEqual(os.getpgid(child_pid), group)
                # Model the root's termination being observed before a child
                # has finished handling the group termination. Both are real
                # owned processes; the test always joins the child's cleanup.
                os.kill(group, signum)
                def stop_child():
                    try:
                        os.kill(child_pid, signum)
                    except ProcessLookupError:
                        pass
                timer = threading.Timer(0.5, stop_child)
                pending.append(timer)
                timer.start()
            try:
                with patch.object(run.os, "killpg", side_effect=delayed_group_signal):
                    result = run.execute([sys.executable, "-c", parent, child], root, os.environ.copy(),
                                         root / "delayed-exit.log", 10, memory_limit_mb=48)
                self.assertEqual(result["status"], "failed")
                child_pid = int((root / "child.pid").read_text())
                state = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)],
                                       capture_output=True, text=True).stdout.strip()
                self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                for timer in pending:
                    timer.join()

    def test_monitoring_error_kills_and_reaps_active_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real_popen = subprocess.Popen
            processes = []
            def capture(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                return process
            with patch.object(run.subprocess, "Popen", side_effect=capture), \
                    patch.object(run, "process_tree_rss_kib", side_effect=OSError("ps unavailable")):
                with self.assertRaisesRegex(OSError, "ps unavailable"):
                    run.execute([sys.executable, "-c", "import time; time.sleep(60)"], root,
                                os.environ.copy(), root / "monitor-error.log", 10)
            self.assertEqual(processes[0].poll(), -signal.SIGKILL)

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

    def test_shared_runtime_builds_and_smokes_use_baseline_cpu(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/ecosystem.yml").read_text()
        for line in workflow.splitlines():
            if "zig build home-tool" in line:
                self.assertIn("-Dcpu=baseline", line)
        self.assertIn("--disassemble=memcpy", workflow)
        self.assertIn('scripts/provenance.py runtime "$ENGINE_SHA" "$SUITE_REF"', workflow)
        self.assertIn("zig build home-tool-smoke home-tool-rejections-smoke home-tool-async-context-smoke", workflow)
        self.assertIn('"home-tool-rejections-smoke"', (root / "scripts/run.py").read_text())
        self.assertIn('"home-tool-async-context-smoke"', (root / "scripts/run.py").read_text())


class ProvenanceTests(unittest.TestCase):
    def test_real_artifact_hashes_and_source_cpu_profile_mismatches(self):
        # These file bytes test orchestration only, never engine compatibility.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "engine/zig-out/lib/libzig-js.a"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"archive fixture")
            (root / "home-tool").write_bytes(b"host fixture")
            config = {"home_repository": "home-lang/home", "home_ref": SHA,
                      "zig_version": "0.18.0-dev.fixture", "runtime_optimize": "ReleaseSafe"}
            data = {"engine_sha": SHA, "suite_ref": OTHER_SHA,
                    "home_repository": config["home_repository"], "home_ref": SHA,
                    "zig_version": config["zig_version"], "build_cpu": "baseline",
                    "build_optimize": "ReleaseSafe", "sha256": {
                        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in ("home-tool", "engine/zig-out/lib/libzig-js.a")}}

            def verify():
                (root / "provenance.json").write_text(json.dumps(data))
                provenance.verify_runtime(root, SHA, OTHER_SHA, config)

            verify()
            for key, wrong in (("engine_sha", OTHER_SHA), ("suite_ref", SHA),
                               ("home_ref", OTHER_SHA), ("home_repository", "stacksjs/stacks"),
                               ("zig_version", "wrong"), ("build_cpu", "native"),
                               ("build_optimize", "Debug")):
                original = data[key]
                data[key] = wrong
                with self.subTest(key=key), self.assertRaises(ValueError):
                    verify()
                data[key] = original
            digest = data["sha256"].pop("home-tool")
            with self.assertRaises(ValueError):
                verify()
            data["sha256"]["home-tool"] = digest
            archive.write_bytes(b"corrupted archive")
            with self.assertRaises(ValueError):
                verify()

    def test_unsafe_or_missing_build_profiles_fail_closed(self):
        for value in (None, "ReleaseFast", "ReleaseSmall", "release-safe", ["ReleaseSafe"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                provenance.runtime_optimize({"runtime_optimize": value})
        self.assertEqual(provenance.runtime_optimize({"runtime_optimize": "Debug"}), "Debug")
        self.assertEqual(provenance.runtime_optimize({"runtime_optimize": "ReleaseSafe"}), "ReleaseSafe")


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
            fake_host.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                                 "if Path.cwd().name == 'reference':\n"
                                 "    assert not (Path.cwd().parent / 'candidate').exists(), 'candidate retained during reference'\n"
                                 "print('orchestration-only host fixture')\n")
            fake_host.chmod(0o755)
            reference_bin = root / "reference-bin"
            reference_bin.mkdir()
            (reference_bin / "bun").symlink_to(fake_host)
            env = {**os.environ, "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": f"url.file://{source}.insteadOf",
                   "GIT_CONFIG_VALUE_0": "https://github.com/test/fixture.git",
                   "PATH": str(reference_bin) + os.pathsep + os.environ["PATH"]}
            process = subprocess.run([sys.executable, str(run.ROOT / "scripts/run.py"),
                                      "--repositories", json.dumps([{"full_name": "test/fixture", "sha": sha}]),
                                      "--engine-sha", SHA, "--home-tool", str(fake_host),
                                      "--bun", str(fake_host), "--engine-root", str(root / "engine"),
                                      "--work", str(root / "work"), "--output", str(root / "results"), "--reference"],
                                     env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            result = json.loads((root / "results/test--fixture.json").read_text())
            build = next(c for c in result["checks"] if c["label"] == ".: build")
            tests = next(c for c in result["checks"] if c["label"] == ".: test")
            self.assertEqual(build["status"], "passed", result)
            self.assertEqual(build["engine_invocations"], 1)
            self.assertEqual(tests["status"], "unsupported")
            reference = next(c for c in result["checks"] if c["lane"] == "reference" and c["label"] == ".: build")
            self.assertEqual(reference["status"], "passed", result)
            self.assertTrue(result["failed"])
            self.assertTrue((root / "results/test--fixture" / build["log"]).is_file())


if __name__ == "__main__":
    unittest.main()
