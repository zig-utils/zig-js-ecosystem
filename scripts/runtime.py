#!/usr/bin/env python3
"""Route package JS commands to Home+zig-js; unsupported host commands fail closed.

Installed as bun/node/npm/pnpm/yarn/npx/bunx in a private PATH directory.
Dependency installation uses a separately captured Bun path before these shims.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

UNSUPPORTED = 78
JS_EXTENSIONS = {".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".mts", ".cts"}


def event(kind, **details):
    if path := os.environ.get("ECOSYSTEM_EVENTS"):
        with open(path, "a") as stream:
            stream.write(json.dumps({"kind": kind, **details}) + "\n")


def blocked(message):
    event("unsupported", reason=message)
    print("ECOSYSTEM UNSUPPORTED: " + message, file=sys.stderr)
    return UNSUPPORTED


def package_root(cwd):
    for directory in [cwd, *cwd.parents]:
        if (directory / "package.json").is_file():
            return directory
    return cwd


def find_binary(name, cwd):
    if "/" in name or Path(name).suffix in JS_EXTENSIONS:
        path = cwd / name
        if path.is_file():
            return path.resolve()
    for directory in [cwd, *cwd.parents]:
        path = directory / "node_modules" / ".bin" / name
        if path.is_file():
            return path.resolve()
    return None


def run_file(path, arguments, conditions=()):
    if path.suffix not in JS_EXTENSIONS:
        first = path.open(errors="replace").readline()
        if "node" not in first and "bun" not in first:
            return blocked(f"Cannot execute non-JS package binary through zig-js: {path}")
    event("zig-js", file=str(path), arguments=arguments, conditions=list(conditions))
    flags = ["--conditions=" + condition for condition in conditions]
    os.execv(os.environ["HOME_TOOL"], [os.environ["HOME_TOOL"], *flags, "run", str(path), *arguments])


def run_script(name, arguments, cwd, conditions=()):
    root = package_root(cwd)
    path = root / "package.json"
    manifest = json.loads(path.read_text()) if path.is_file() else {}
    command = manifest.get("scripts", {}).get(name)
    if not command:
        binary = find_binary(name, cwd)
        return run_file(binary, arguments, conditions) if binary else blocked(f"No script or installed binary: {name}")
    if conditions:
        return blocked("Runtime --conditions on shell package scripts is unsupported; conditions were not discarded")
    depth = int(os.environ.get("ECOSYSTEM_SCRIPT_DEPTH", "0"))
    if depth >= 24:
        return blocked("Package script recursion exceeded 24 calls")
    env = {**os.environ, "ECOSYSTEM_SCRIPT_DEPTH": str(depth + 1)}
    # Arguments are positional shell parameters; never concatenate untrusted text.
    return subprocess.call(["/bin/sh", "-c", command + ' "$@"', "ecosystem-script", *arguments], cwd=root, env=env)


def main(argv=None, program=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    program = program or Path(sys.argv[0]).name
    cwd = Path.cwd()
    conditions = []
    while argv:
        flag = argv[0]
        if flag in ("--bun", "--silent"):
            argv.pop(0)
        elif flag == "--conditions" or flag.startswith("--conditions="):
            if program not in ("bun", "node"):
                return blocked(f"Unsupported runtime flag: {program} --conditions")
            argv.pop(0)
            if flag == "--conditions":
                if not argv:
                    return blocked("Missing value for --conditions")
                conditions.append(argv.pop(0))
            else:
                conditions.append(flag.split("=", 1)[1])
        elif flag == "--cwd":
            if len(argv) < 3:
                return blocked("Missing directory or command for --cwd")
            os.chdir(argv[1])
            cwd = Path.cwd()
            del argv[:2]
        else:
            break
    if not argv:
        return blocked(f"Interactive {program} is unavailable")
    command, *rest = argv
    if program in ("npm", "pnpm", "yarn"):
        if command in ("run", "run-script") and rest:
            return run_script(rest[0], rest[1:], cwd, conditions)
        if command in ("test", "build", "typecheck", "smoke"):
            return run_script(command, rest, cwd, conditions)
        return blocked(f"{program} {command} requires a package-manager host adapter")
    if program in ("bunx", "npx") or command == "x":
        args = argv if program in ("bunx", "npx") else rest
        args = [arg for arg in args if arg not in ("--bun", "--no-install")]
        if not args:
            return blocked("No installed package binary specified")
        binary = find_binary(args[0], cwd)
        return run_file(binary, args[1:], conditions) if binary else blocked(f"Package binary not installed: {args[0]}")
    if command in ("-e", "--eval") and rest:
        event("zig-js", eval=True)
        flags = ["--conditions=" + condition for condition in conditions]
        os.execv(os.environ["HOME_TOOL"], [os.environ["HOME_TOOL"], *flags, "eval", rest[0]])
    if command == "run" and rest:
        return run_script(rest[0], rest[1:], cwd, conditions)
    if command in ("test", "--test"):
        return blocked("Home's repository-tool runtime has no Bun/Node test scheduler; upstream tests were not executed")
    if command in ("build", "install", "i", "add", "update", "exec", "create"):
        return blocked(f"{program} {command} requires a host API absent from Home's repository-tool runtime")
    if command.startswith("-"):
        return blocked(f"Unsupported runtime flag: {program} {command}")
    binary = find_binary(command, cwd)
    if binary:
        return run_file(binary, rest, conditions)
    if program == "bun":
        return run_script(command, rest, cwd, conditions)
    return blocked(f"JS entry point not found: {command}")


if __name__ == "__main__":
    sys.exit(main())
