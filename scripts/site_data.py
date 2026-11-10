#!/usr/bin/env python3
"""Publish measured consumer evidence independently of current CI job state.

Only read JSON from digest-verified Actions artifacts. Never execute their files.
Focused runs overlay only the repositories they actually report. Missing results
remain unknown; native checks cannot establish JavaScript runtime support.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOS = ("zig-utils/zig-js", "zig-utils/zig-js-ecosystem")
NAME = re.compile(r"^[\w.-]+/[\w.-]+$")
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_JSON = 64 * 1024 * 1024
REFRESH_ERRORS = (RuntimeError, ValueError, KeyError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile)
CONSUMER_JSON = re.compile(r"^[\w.-]+--[\w.-]+\.json$")
LABELS = {"passed": "Passed", "failed": "Failing", "unsupported": "Unsupported",
          "not-run": "Not executed", "unknown": "Awaiting evidence",
          "native-only": "Native only", "blocked": "Setup blocked", "not-applicable": "Not applicable"}


def outcome(checks):
    candidate = [c for c in checks if c.get("lane") == "candidate"]
    states = {c.get("status", "unknown") for c in candidate}
    if states - {"passed", "not-applicable", "unsupported", "not-run"}:
        return "failed"
    if "unsupported" in states:
        return "unsupported"
    if "not-run" in states:
        return "not-run"
    if candidate and states == {"passed"} and any(c.get("engine_invocations", 0) for c in candidate):
        return "passed"
    if any(c.get("lane") == "setup" and c.get("status") != "passed" for c in checks):
        return "blocked"
    if candidate and states == {"not-applicable"}:
        return "not-applicable"
    if any(c.get("lane") in ("native", "native-zig") for c in checks):
        return "native-only"
    if any(c.get("lane") == "coverage" and c.get("status") == "not-applicable" for c in checks):
        return "not-applicable"
    if any(c.get("lane") == "coverage" and c.get("status") == "unsupported" for c in checks):
        return "unsupported"
    return "unknown"


def lane_counts(checks, lane):
    return dict(Counter(c.get("status", "unknown") for c in checks if c.get("lane") == lane))


def compact_record(record, source, expected):
    name = record.get("repository", "")
    if not NAME.fullmatch(name) or name not in expected:
        raise ValueError("Unexpected consumer in report")
    if record.get("sha") != expected[name] or not SHA.fullmatch(record.get("engine_sha", "")):
        raise ValueError("Consumer identity/revision mismatch")
    checks = record.get("checks", [])
    if not checks:
        raise ValueError("Empty consumer checks")
    provenance = record.get("runtime_provenance", {})
    if provenance and provenance.get("engine_sha") != record["engine_sha"]:
        raise ValueError("Runtime provenance engine mismatch")
    return {
        "name": name, "sha": record["sha"], "engine_sha": record["engine_sha"],
        "provenance": provenance, "source": source, "status": outcome(checks),
        "scopes": len(record.get("scopes", [])),
        "lanes": {lane: lane_counts(checks, lane) for lane in sorted({c.get("lane", "unknown") for c in checks})},
        "checks": [{k: c[k] for k in ("label", "lane", "status", "reason", "log", "seconds", "engine_invocations") if k in c}
                   for c in checks],
    }


def read_json_members(raw, artifact, select):
    if len(raw) > MAX_ARCHIVE:
        raise ValueError("Artifact exceeds download limit")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if artifact.get("digest") != digest:
        raise ValueError("Artifact digest mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = archive.infolist()
        # Read specific JSON members in memory; no extraction or symlink traversal.
        selected = [p for p in files if select(p.filename)]
        if sum(p.file_size for p in selected) > MAX_JSON:
            raise ValueError("Report JSON exceeds read limit")
        if len({p.filename for p in selected}) != len(selected):
            raise ValueError("Duplicate JSON member")
        return {p.filename: json.loads(archive.read(p)) for p in selected}


def read_artifact(raw, artifact):
    members = read_json_members(raw, artifact,
                                lambda name: name in ("report/report.json", "report.json", "inventory.json"))
    reports = [value for name, value in members.items() if name.endswith("report.json")]
    if len(reports) != 1 or "inventory.json" not in members:
        raise ValueError("Artifact must contain exactly one report and inventory")
    return reports[0], members["inventory.json"]


def read_consumer_artifact(raw, artifact):
    members = read_json_members(raw, artifact, lambda name: bool(CONSUMER_JSON.fullmatch(name)))
    if not members:
        raise ValueError("Empty consumer artifact")
    for name, record in members.items():
        if not isinstance(record, dict) or name != record.get("repository", "").replace("/", "--") + ".json":
            raise ValueError("Consumer filename mismatch")
    results = list(members.values())
    return {"engine_sha": results[0].get("engine_sha"), "results": results}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path, binary=False):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        # Local development uses the existing authenticated CLI, never persists credentials.
        raw = subprocess.check_output([os.environ.get("GH", "gh"), "api", path], stderr=subprocess.PIPE)
    else:
        req = urllib.request.Request("https://api.github.com/" + path,
                                     headers={"Authorization": "Bearer " + token,
                                              "Accept": "application/vnd.github+json",
                                              "User-Agent": "zig-js-ecosystem-dashboard"})
        try:
            response = urllib.request.build_opener(NoRedirect).open(req, timeout=60)
        except urllib.error.HTTPError as error:
            if not binary or error.code != 302:
                raise RuntimeError(f"GitHub API {error.code} for {path}") from None
            # A fresh request prevents forwarding the API token to artifact storage.
            url = error.headers["Location"]
            if not url.startswith("https://"):
                raise ValueError("Unsafe artifact redirect")
            response = urllib.request.urlopen(url, timeout=60)
        with response:
            raw = response.read(MAX_ARCHIVE + 1)
        if len(raw) > MAX_ARCHIVE:
            raise ValueError("API response exceeds limit")
    return raw if binary else json.loads(raw)


def paginate(path, key):
    result = []
    for page in range(1, 101):
        data = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")[key]
        result.extend(data)
        if len(data) < 100:
            return result
    raise ValueError("Pagination exceeds safety limit")


def run_info(run, jobs):
    return {k: run.get(k) for k in ("id", "html_url", "head_sha", "status", "conclusion", "created_at", "updated_at")} | {
        "jobs": [{k: job.get(k) for k in ("id", "name", "status", "conclusion", "html_url")} for job in jobs]}


def measurement_order(source):
    # A large older matrix can publish its aggregate after a newer consumer
    # finishes. Artifact upload time only breaks ties within the same run age.
    return (source.get("run_created_at") or source["measured_at"], source["measured_at"])


def merge_report(records, report, inventory, source):
    expected = {r["full_name"]: r["sha"] for r in inventory["selected"]}
    incoming = {}
    for record in report.get("results", []):
        if record.get("engine_sha") != report.get("engine_sha"):
            raise ValueError("Report engine mismatch")
        compact = compact_record(record, source, expected)
        if compact["name"] in incoming:
            raise ValueError("Duplicate consumer result")
        incoming[compact["name"]] = compact
    for name, compact in incoming.items():
        if name not in records or measurement_order(records[name]["source"]) < measurement_order(source):
            records[name] = compact


def collect(bootstrap):
    data = json.loads(json.dumps(bootstrap))
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["warnings"] = []
    records = {r["name"]: r for r in data.get("records", [])}
    for record in records.values():
        record["status"] = outcome(record["checks"])
        record["lanes"] = {lane: lane_counts(record["checks"], lane)
                           for lane in sorted({c.get("lane", "unknown") for c in record["checks"]})}
    live, inventories = {}, []
    for repo in REPOS:
        try:
            runs = api(f"repos/{repo}/actions/workflows/ecosystem.yml/runs?per_page=8")["workflow_runs"]
            if not runs:
                raise ValueError("No ecosystem runs returned")
            jobs = paginate(f"repos/{repo}/actions/runs/{runs[0]['id']}/jobs", "jobs")
            live[repo] = run_info(runs[0], jobs)
            # A completed full report provides the baseline while newer runs execute.
            completed = api(f"repos/{repo}/actions/workflows/ecosystem.yml/runs?per_page=2&status=completed")["workflow_runs"]
            candidates = {r["id"]: r for r in [*runs, *completed]}
            # Older checked-in snapshots predate run_created_at. Resolve each
            # distinct source once, so a late legacy aggregate has no advantage.
            run_dates = {r["id"]: r["created_at"] for r in candidates.values()}
            for record in records.values():
                source = record["source"]
                run_id = source.get("run_id")
                if source.get("repository") != repo or not run_id or source.get("run_created_at"):
                    continue
                if run_id not in run_dates:
                    try:
                        run_dates[run_id] = api(f"repos/{repo}/actions/runs/{run_id}")["created_at"]
                    except REFRESH_ERRORS as error:
                        run_dates[run_id] = None
                        data["warnings"].append(f"{repo} run {run_id}: source date unavailable ({type(error).__name__}); dated evidence retained")
                if run_dates[run_id]:
                    source["run_created_at"] = run_dates[run_id]
            for run in candidates.values():
                try:
                    artifacts = paginate(f"repos/{repo}/actions/runs/{run['id']}/artifacts", "artifacts")
                    artifact = next((a for a in artifacts if a["name"] == "ecosystem-report" and not a["expired"]), None)
                    inventory_artifact = next((a for a in artifacts if a["name"] == "ecosystem-inventory" and not a["expired"]), None)
                    partials = [a for a in artifacts if re.fullmatch(r"ecosystem-results-\d+", a["name"]) and not a["expired"]]
                    # Consumer jobs publish their completed JSON before the entire
                    # matrix can produce its aggregate. Keep their frozen inventory
                    # separate from the latest live job state and from other runs.
                    if not artifact and partials and not inventory_artifact:
                        raise ValueError("Frozen inventory artifact missing")
                    if not artifact and not partials:
                        continue
                    chosen = [artifact] if artifact else [inventory_artifact, *partials]
                    if any(a["size_in_bytes"] > MAX_ARCHIVE for a in chosen):
                        data["warnings"].append(f"{repo} run {run['id']}: report too large; prior evidence retained")
                        continue
                    if artifact:
                        raw = api(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip", binary=True)
                        report, inventory = read_artifact(raw, artifact)
                        reports = [(artifact, report)]
                    else:
                        raw = api(f"repos/{repo}/actions/artifacts/{inventory_artifact['id']}/zip", binary=True)
                        inventory = read_json_members(raw, inventory_artifact, lambda name: name == "inventory.json")["inventory.json"]
                        reports = []
                        for partial in partials:
                            try:
                                raw = api(f"repos/{repo}/actions/artifacts/{partial['id']}/zip", binary=True)
                                reports.append((partial, read_consumer_artifact(raw, partial)))
                            except REFRESH_ERRORS as error:
                                data["warnings"].append(f"{repo} run {run['id']} artifact {partial['id']}: unavailable ({type(error).__name__}); prior evidence retained")
                    for measured, report in reports:
                        source = {"repository": repo, "run_id": run["id"], "url": run["html_url"],
                                  "run_created_at": run["created_at"],
                                  "measured_at": measured.get("updated_at") or measured.get("created_at") or run["updated_at"],
                                  "artifact_id": measured["id"], "digest": measured["digest"],
                                  "focused": bool(inventory.get("focus")),
                                  "artifact_url": f"{run['html_url']}/artifacts/{measured['id']}"}
                        if not artifact:
                            source.update({"partial": True, "inventory_artifact_id": inventory_artifact["id"],
                                           "inventory_digest": inventory_artifact["digest"]})
                        merge_report(records, report, inventory, source)
                    if not inventory.get("focus"):
                        inventories.append((run["created_at"], inventory))
                except REFRESH_ERRORS as error:
                    data["warnings"].append(f"{repo} run {run['id']}: refresh unavailable ({type(error).__name__}); prior evidence retained")
        except REFRESH_ERRORS as error:
            # Never log response bodies, tokens or signed artifact URLs.
            data["warnings"].append(f"{repo}: refresh unavailable ({type(error).__name__}); dated prior evidence retained")
    data["live"] = live
    if inventories:
        data["inventory"] = max(inventories, key=lambda x: x[0])[1]
    data["records"] = sorted(records.values(), key=lambda r: r["name"])
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "site/data/snapshot.json")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    bootstrap = json.loads((ROOT / "site/data/bootstrap.json").read_text())
    data = bootstrap if args.offline else collect(bootstrap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print(f"Dashboard: {len(data['inventory']['selected'])} selected, {len(data['records'])} measured, {len(data.get('warnings', []))} refresh warnings")


if __name__ == "__main__":
    main()
