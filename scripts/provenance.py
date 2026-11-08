"""Verify the exact shared runtime and its declared safe build profile."""
import argparse
import hashlib
import json
from pathlib import Path


def runtime_optimize(config):
    mode = config.get("runtime_optimize")
    if mode not in ("Debug", "ReleaseSafe"):
        raise ValueError("runtime_optimize must be Debug or ReleaseSafe")
    return mode


def verify_runtime(root, engine_sha, suite_ref, config):
    root = Path(root)
    provenance = json.loads((root / "provenance.json").read_text())
    expected = {"engine_sha": engine_sha, "suite_ref": suite_ref,
                "home_repository": config["home_repository"], "home_ref": config["home_ref"],
                "zig_version": config["zig_version"], "build_cpu": "baseline",
                "build_optimize": runtime_optimize(config)}
    for name, value in expected.items():
        if provenance.get(name) != value:
            raise ValueError(f"Runtime provenance mismatch: {name}")
    hashes = provenance.get("sha256")
    if not isinstance(hashes, dict) or set(hashes) != {"home-tool", "engine/zig-out/lib/libzig-js.a"}:
        raise ValueError("Runtime hashes must cover the host and exact engine archive")
    for name, digest in hashes.items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Runtime artifact hash mismatch: {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("engine_sha")
    parser.add_argument("suite_ref")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()
    verify_runtime(args.root, args.engine_sha, args.suite_ref, json.loads(args.config.read_text()))
