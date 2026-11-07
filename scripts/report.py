#!/usr/bin/env python3
"""Require every selected consumer result and publish an auditable coverage report."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys


def summarize(inventory, directory, engine_sha):
    records, missing, mismatched = [], [], []
    expected = {r["full_name"]: r["sha"] for r in inventory["selected"]}
    for repo, sha in expected.items():
        path = directory / (repo.replace("/", "--") + ".json")
        if not path.is_file():
            missing.append(repo)
            continue
        result = json.loads(path.read_text())
        if result.get("repository") != repo or result.get("sha") != sha or result.get("engine_sha") != engine_sha:
            mismatched.append(repo)
            continue
        if not result.get("checks"):
            mismatched.append(repo)
            continue
        records.append(result)
    statuses = Counter(check["status"] for record in records for check in record["checks"])
    failing = [record["repository"] for record in records
               if any(check["status"] not in ("passed", "not-applicable") for check in record["checks"])]
    return {"engine_sha": engine_sha, "selected_repositories": len(expected),
            "reported_repositories": len(records), "missing": missing, "mismatched": mismatched,
            "failing_repositories": failing, "check_statuses": dict(statuses), "results": records,
            "success": bool(expected) and not missing and not mismatched and not failing}


def markdown(report, inventory):
    def safe(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("`", "'")
    lines = ["# zig-js ecosystem", "", f"Engine: `{report['engine_sha']}`", "",
             f"{report['reported_repositories']}/{report['selected_repositories']} selected repositories reported; "
             f"{len(report['failing_repositories'])} have failed, unsupported, timed-out or unexecuted checks.", "",
             "Native Zig checks and reference Bun runs are labeled separately from zig-js execution. "
             "Source-module initialization is a smoke check, not an upstream test-suite pass. "
             "Not-applicable repositories do not count as runtime passes.", "",
             f"Activity cutoff: {inventory['activity_cutoff']}. Excluded owners: " + ", ".join(inventory["excluded_owners"]) + ".", "",
             "| Repository | Consumer SHA | Failed checks | zig-js invocations |", "| --- | --- | ---: | ---: |"]
    if inventory.get("focus"):
        lines[0] = "# zig-js ecosystem focused reproduction"
        lines.insert(4, "Manual subset: " + ", ".join(inventory["focus"]) +
                     f". The complete inventory contains {inventory['full_selected_count']} repositories.\n")
    for result in report["results"]:
        failed = sum(c["status"] not in ("passed", "not-applicable") for c in result["checks"])
        invoked = sum(c.get("engine_invocations", 0) for c in result["checks"])
        repo = result["repository"]
        lines.append(f"| [{repo}](https://github.com/{repo}) | `{result['sha'][:12]}` | {failed} | {invoked} |")
    for key in ("missing", "mismatched"):
        if report[key]:
            lines += ["", f"**{key.title()} results:** " + ", ".join(report[key])]
    failures = [(r["repository"], c) for r in report["results"] for c in r["checks"]
                if c["status"] not in ("passed", "not-applicable")]
    if failures:
        lines += ["", "## Failing / unsupported checks", "",
                  "The full report.json and command logs are in the ecosystem-report artifact. "
                  "Reference-pass/candidate-fail is a compatibility gap; it does not by itself prove an engine regression.", ""]
        # Keep GitHub's step summary below its size limit; JSON/logs retain every check.
        for repo, check in failures[:200]:
            lines.append(f"- **{repo}** / {safe(check['label'])}: {safe(check['status'])}" +
                         (" — " + safe(check["reason"])[:400] if check.get("reason") else ""))
        if len(failures) > 200:
            lines.append(f"- {len(failures)-200} more checks in report.json.")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--engine-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    report = summarize(inventory, args.results, args.engine_sha)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    rendered = markdown(report, inventory)
    (args.output / "report.md").write_text(rendered)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as stream:
            stream.write(rendered)
    print(f"{report['reported_repositories']}/{report['selected_repositories']} consumers reported; "
          f"{len(report['failing_repositories'])} failing, {len(report['missing'])} missing")
    sys.exit(0 if report["success"] else 1)


if __name__ == "__main__":
    main()
