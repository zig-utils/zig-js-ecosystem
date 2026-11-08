#!/usr/bin/env python3
"""Run each discovered consumer, retaining command logs and honest coverage."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

from discover import REPOSITORY, ROOT, SHA

SCRIPT_NAMES = ("build", "test", "smoke", "typecheck")
EXCLUDED_PARTS = {"node_modules", ".git", "vendor", "vendored", "fixtures", "__fixtures__", "test-fixtures"}
TEST_FILE = re.compile(r"(?:^|/)(?:[^/]+\.(?:test|spec)\.[cm]?[jt]sx?)$")


def execute(argv, cwd, env, log, timeout):
    start = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as stream:
        stream.write("$ " + json.dumps(argv) + "\n")
        stream.flush()
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as error:
            stream.write(str(error) + "\n")
            return {"status": "failed", "exit_code": 127, "seconds": 0, "log": log.name}
        try:
            code = process.wait(timeout=max(0.01, timeout))
            status = "passed" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            # Kill the complete command tree; an orphan must never contaminate another repo.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            code, status = 124, "timed-out"
        stream.write(f"\nECOSYSTEM RESULT: {status}, exit {code}\n")
    return {"status": status, "exit_code": code,
            "seconds": round(time.monotonic() - start, 3), "log": log.name}


def tracked_files(checkout):
    return subprocess.check_output(["git", "ls-files", "-z"], cwd=checkout).decode().split("\0")[:-1]


def package_plan(checkout, files):
    scopes, omitted = [], []
    for filename in files:
        path = Path(filename)
        if path.name != "package.json":
            continue
        if EXCLUDED_PARTS.intersection(path.parts) or "test" in path.parts or "tests" in path.parts:
            omitted.append({"path": filename, "reason": "fixture/test/vendor manifest; tested through its owning package"})
            continue
        manifest = json.loads((checkout / filename).read_text())
        scripts = {name: manifest.get("scripts", {})[name] for name in SCRIPT_NAMES
                   if manifest.get("scripts", {}).get(name)}
        directory = path.parent.as_posix()
        prefix = "" if directory == "." else directory + "/"
        tests = [p for p in files if p.startswith(prefix) and TEST_FILE.search(p)
                 and not EXCLUDED_PARTS.intersection(Path(p).parts)]
        # A test script that only echoes a placeholder does not substitute for actual tests.
        if not scripts.get("test") and tests:
            scripts["test"] = "bun test"
        entries = []
        # An existing source module is useful even if dist/ has not yet been built.
        for entry in ("src/index.ts", "src/index.js", "src/main.ts", "index.ts", "index.js"):
            if (checkout / directory / entry).is_file():
                entries.append(entry)
                break
        def export_targets(value):
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [target for item in value for target in export_targets(item)]
            if isinstance(value, dict):
                return [target for key, item in value.items() if key != "types" for target in export_targets(item)]
            return []
        declared = [manifest.get(key) for key in ("source", "module", "main")]
        declared += export_targets(manifest.get("exports"))
        for entry in declared:
            if (isinstance(entry, str) and "*" not in entry and not entry.endswith(".d.ts")
                    and Path(entry).suffix in (".js", ".ts", ".mjs", ".cjs", ".mts", ".cts")
                    and entry not in entries):
                entries.append(entry)
        bins = manifest.get("bin", {})
        if isinstance(bins, str):
            bins = {manifest.get("name", "cli"): bins}
        if not scripts and not entries and not bins:
            omitted.append({"path": filename, "reason": "no build/test/smoke/typecheck scripts, source entry or CLI"})
            continue
        scopes.append({"directory": directory, "name": manifest.get("name", directory),
                       "scripts": scripts, "entries": entries, "bins": bins,
                       "test_file_count": len(tests)})
    return scopes, omitted


def make_shims(directory):
    directory.mkdir(parents=True, exist_ok=True)
    script = ROOT / "scripts" / "runtime.py"
    for name in ("bun", "bunx", "node", "nodejs", "npm", "npx", "pnpm", "yarn"):
        target = directory / name
        target.write_text(f"#!{sys.executable}\nimport runpy, sys\n"
                          f"module = runpy.run_path({str(script)!r})\n"
                          f"sys.exit(module['main'](program={name!r}))\n")
        target.chmod(0o755)


def dependency_roots(checkout, scopes):
    """Install real workspaces once and independent nested apps separately."""
    candidates = {scope["directory"] for scope in scopes}
    if (checkout / "package.json").is_file():
        candidates.add(".")
    covered, roots = set(), []
    for directory in sorted(candidates, key=lambda p: (len(Path(p).parts), p)):
        if directory in covered:
            continue
        roots.append(directory)
        pending = [directory]
        while pending:
            current = pending.pop()
            if current in covered:
                continue
            covered.add(current)
            manifest = json.loads((checkout / current / "package.json").read_text())
            patterns = manifest.get("workspaces", [])
            if isinstance(patterns, dict):
                patterns = patterns.get("packages", [])
            members = set()
            for pattern in [p for p in patterns if not p.startswith("!")]:
                for child in (checkout / current).glob(pattern):
                    if (child / "package.json").is_file() and child.resolve().is_relative_to(checkout.resolve()):
                        members.add(child.relative_to(checkout).as_posix())
            for pattern in [p[1:] for p in patterns if p.startswith("!")]:
                for child in (checkout / current).glob(pattern):
                    members.discard(child.relative_to(checkout).as_posix())
            pending.extend(sorted(members))
    return roots


def script_environment(base, location, checkout, shims=None):
    bins = []
    directory = location
    while directory.is_relative_to(checkout):
        bins.append(str(directory / "node_modules" / ".bin"))
        if directory == checkout:
            break
        directory = directory.parent
    # Runtime names must resolve to our router even when a dependency ships bun/node.
    prefix = [str(shims)] if shims else []
    return {**base, "PATH": os.pathsep.join([*prefix, *bins, base["PATH"]])}


def is_failure(result):
    return any(check["status"] not in ("passed", "not-applicable") for check in result["checks"])


def validate_repository(repo):
    if (not isinstance(repo.get("full_name"), str) or not REPOSITORY.fullmatch(repo["full_name"])
            or not isinstance(repo.get("sha"), str) or not SHA.fullmatch(repo["sha"])):
        raise ValueError("Only inventoried owner/repo names and immutable commit SHAs are accepted")


def run_repository(repo, args, config, result=None):
    name = repo["full_name"]
    validate_repository(repo)
    slug = name.replace("/", "--")
    out = args.output / slug
    out.mkdir(parents=True, exist_ok=True)
    work = args.work / slug
    work.mkdir(parents=True, exist_ok=True)
    if result is None:
        result = {"repository": name, "sha": repo["sha"], "engine_sha": args.engine_sha,
                  "checks": [], "scopes": [], "omitted_manifests": []}
    provenance = args.home_tool.parent / "provenance.json"
    result["runtime_provenance"] = json.loads(provenance.read_text()) if provenance.is_file() else None
    deadline = time.monotonic() + config["repository_timeout_seconds"]
    bun_cache = work / "bun-cache"
    env = {**os.environ, "CI": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1",
           "BUN_INSTALL_CACHE_DIR": str(bun_cache)}
    # Consumer scripts get no API credential, including on workflow_dispatch runs.
    for key in list(env):
        if key in {"GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"}:
            env.pop(key)
    counter = 0

    def check(label, argv, cwd, check_env=None, lane="candidate", requires_engine=False):
        nonlocal counter
        counter += 1
        event_path = out / f"{counter:04d}.events.jsonl"
        run_env = {**(check_env or env), "ECOSYSTEM_EVENTS": str(event_path)}
        budget = min(config["command_timeout_seconds"], deadline - time.monotonic())
        if budget <= 0:
            record = {"status": "not-run", "reason": "repository time budget exhausted"}
        else:
            record = execute(argv, cwd, run_env, out / f"{counter:04d}.log", budget)
        events = [json.loads(line) for line in event_path.read_text().splitlines()] if event_path.exists() else []
        record.update({"label": label, "lane": lane, "command": argv,
                       "engine_invocations": sum(e["kind"] == "zig-js" for e in events)})
        unsupported = [event["reason"] for event in events if event["kind"] == "unsupported"]
        if unsupported:
            record.update(status="unsupported", reason="; ".join(sorted(set(unsupported))))
        elif requires_engine and record["status"] == "passed" and not record["engine_invocations"]:
            record.update(status="unsupported", reason="Command exited zero without executing JavaScript on zig-js")
        result["checks"].append(record)
        print(f"{name}: {lane}: {label}: {record['status']}", flush=True)
        return record["status"] == "passed"

    def clone(path):
        path.mkdir(exist_ok=True)
        return (check("git init", ["git", "init", "--quiet"], path, lane="setup")
                and check("fetch exact consumer SHA", ["git", "-c", "credential.helper=", "fetch", "--depth=1",
                          "https://github.com/" + name + ".git", repo["sha"]], path, lane="setup")
                and check("checkout", ["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], path, lane="setup"))

    candidate = work / "candidate"
    if not clone(candidate):
        return result
    files = tracked_files(candidate)
    scopes, omitted = package_plan(candidate, files)
    result.update(scopes=scopes, omitted_manifests=omitted,
                  native_build_files=[p for p in files if Path(p).name == "build.zig"])
    has_native_build = (candidate / "build.zig").is_file()
    if name == config["home_repository"] and has_native_build:
        for step in ("home-tool", "home-tool-smoke", "home-tool-rejections-smoke"):
            check("Home integration: " + step, ["zig", "build", step, "-Dtool-js-engine=zig-js",
                  "-Dzig-js-root=" + str(args.engine_root)], candidate, lane="direct-consumer")

    def native_checks():
        if has_native_build and name != config["home_repository"]:
            check("native Zig build", ["zig", "build"], candidate, lane="native-zig")
            if check("discover native Zig test steps", ["zig", "build", "--help"], candidate, lane="native-zig"):
                help_text = (out / result["checks"][-1]["log"]).read_text()
                if re.search(r"^\s+test(?:-parallel)?\s", help_text, re.MULTILINE):
                    step = "test-parallel" if re.search(r"^\s+test-parallel\s", help_text, re.MULTILINE) else "test"
                    check("native Zig tests", ["zig", "build", step], candidate, lane="native-zig")
                else:
                    result["checks"].append({"label": "native Zig tests", "lane": "native-zig",
                                              "status": "not-applicable", "reason": "No advertised test or test-parallel step"})
    shims = work / "shims"
    make_shims(shims)
    candidate_env = {**env, "PATH": str(shims) + os.pathsep + env["PATH"],
                     "HOME_TOOL": str(args.home_tool), "BUN": str(shims / "bun"),
                     "NODE": str(shims / "node")}
    if scopes:
        if not check("verify Home.engine", [str(args.home_tool), "eval",
                     'if (Home.engine !== "zig-js") throw new Error("wrong engine"); console.log("ECOSYSTEM ENGINE zig-js");'],
                     candidate, lane="engine"):
            return result
        # Frozen installs when a lockfile exists; no registry-driven lifecycle hooks.
        install_roots = dependency_roots(candidate, scopes)
        result["dependency_roots"] = install_roots
        for directory in sorted(install_roots):
            location = candidate / directory
            frozen = any((location / filename).exists() for filename in ("bun.lock", "bun.lockb", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"))
            check(f"{directory}: dependencies", [str(args.bun), str(ROOT / "scripts" / "dependencies.mjs"), str(candidate), *(["--frozen-lockfile"] if frozen else [])],
                  location, lane="setup")

        for scope in scopes:
            location = candidate / scope["directory"]
            scope_env = script_environment(candidate_env, location, candidate, shims)
            for label, command in scope["scripts"].items():
                check(f"{scope['directory']}: {label}", ["/bin/sh", "-c", command], location,
                      scope_env, requires_engine=True)
            for entry in scope["entries"]:
                check(f"{scope['directory']}: module initialization {entry}",
                      [str(shims / "bun"), entry], location, scope_env, requires_engine=True)
            # CLI entry loading with --help must exit, rather than leave a server running.
            for binary, entry in scope["bins"].items():
                check(f"{scope['directory']}: CLI {binary} --help",
                      [str(shims / "bun"), entry, "--help"], location, scope_env, requires_engine=True)

        native_checks()
        if args.reference:
            # Candidate artifacts are no longer needed. Avoid keeping two Home
            # trees, build caches and dependency installations on a small runner.
            shutil.rmtree(candidate)
            shutil.rmtree(bun_cache, ignore_errors=True)
            reference = work / "reference"
            if clone(reference):
                for directory in sorted(install_roots):
                    location = reference / directory
                    frozen = any((location / filename).exists() for filename in ("bun.lock", "bun.lockb", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"))
                    check(f"{directory}: reference dependencies", [str(args.bun), str(ROOT / "scripts" / "dependencies.mjs"), str(reference), *(["--frozen-lockfile"] if frozen else [])],
                          location, lane="reference-setup")
                for scope in scopes:
                    location = reference / scope["directory"]
                    scope_env = script_environment(env, location, reference)
                    for label, command in scope["scripts"].items():
                        check(f"{scope['directory']}: {label}", ["/bin/sh", "-c", command],
                              location, scope_env, lane="reference")
    else:
        native_checks()
    if not scopes and not has_native_build:
        # Non-executable repos are retained in the coverage report, outside runtime passes.
        executable = any(Path(p).suffix in (".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs", ".zig")
                         and not EXCLUDED_PARTS.intersection(Path(p).parts) for p in files)
        result["checks"].append({"label": "coverage", "lane": "coverage",
                                  "status": "unsupported" if executable else "not-applicable",
                                  "reason": "No recognized package or native Zig build entry point" if executable
                                  else "No tracked JavaScript/TypeScript/Zig executable consumer"})
    return result


def run_and_save(repo, args, config):
    validate_repository(repo)
    result = {"repository": repo["full_name"], "sha": repo["sha"], "engine_sha": args.engine_sha,
              "checks": [], "scopes": [], "omitted_manifests": []}
    try:
        run_repository(repo, args, config, result)
    except Exception as error:
        # Preserve completed command evidence when a later command exhausts disk
        # space or fails in orchestration. The exception remains a red check.
        result["checks"].append({"label": "orchestrator", "status": "failed", "reason": repr(error)})
    finally:
        # Free this consumer's files BEFORE writing JSON, including on ENOSPC.
        shutil.rmtree(args.work / repo["full_name"].replace("/", "--"), ignore_errors=True)
    result["failed"] = is_failure(result)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / (repo["full_name"].replace("/", "--") + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repositories", required=True, help="JSON array from the frozen inventory")
    parser.add_argument("--engine-sha", required=True)
    parser.add_argument("--home-tool", type=Path, required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--bun", type=Path, default=Path(shutil.which("bun") or "/missing-bun"))
    parser.add_argument("--work", type=Path, default=ROOT / ".work")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    parser.add_argument("--reference", action="store_true")
    args = parser.parse_args()
    if not SHA.fullmatch(args.engine_sha):
        parser.error("--engine-sha must be an immutable 40-character commit SHA")
    for key in ("work", "output", "home_tool", "engine_root", "bun"):
        setattr(args, key, getattr(args, key).resolve())
    config = json.loads((ROOT / "config.json").read_text())
    repositories = json.loads(args.repositories)
    if not repositories:
        parser.error("Refusing to run zero repositories")
    failed = False
    for repo in repositories:
        result = run_and_save(repo, args, config)
        failed |= result["failed"]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
