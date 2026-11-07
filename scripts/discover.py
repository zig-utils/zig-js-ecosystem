#!/usr/bin/env python3
"""Inventory public profile/org repositories and freeze their default-branch SHAs."""
import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[a-f0-9]{40}$")


def api(path):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        # Local development uses the credential helper without printing its token.
        return json.loads(subprocess.check_output(["gh", "api", path], text=True))
    request = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(2 ** attempt)


def pages(path):
    items = []
    for page in range(1, 1000):
        batch = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError(f"Expected an array from {path}")
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise ValueError(f"Pagination did not terminate: {path}")


def cutoff_date(today, years):
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def exclusion(repo, config, cutoff):
    if repo["owner"]["login"].lower() in {x.lower() for x in config["exclude_owners"]}:
        return "excluded owner"
    if repo["full_name"] in config["exclude_repositories"]:
        return config["exclude_repositories"][repo["full_name"]]
    if repo.get("private"):
        return "private repository"
    if repo.get("archived"):
        return "archived repository"
    if repo.get("disabled"):
        return "disabled repository"
    if not repo.get("pushed_at") or repo["pushed_at"][:10] < cutoff.isoformat():
        return "no push within activity window"
    if not repo.get("size"):
        return "empty repository"
    return None


def matrix_for(repositories, priorities=()):
    if not repositories:
        raise ValueError("Zero selected repositories; refusing an empty green run")
    by_name = {repo["full_name"]: repo for repo in repositories}
    if len(by_name) != len(repositories):
        raise ValueError("Duplicate selected repositories")
    priority = [by_name[name] for name in dict.fromkeys(priorities) if name in by_name]
    if len(priority) > 100:
        raise ValueError("At most 100 priority repositories are supported")
    priority_names = {repo["full_name"] for repo in priority}
    remaining = [repo for repo in repositories if repo["full_name"] not in priority_names]
    # Critical consumers get independent first legs, so a slow Stacks build cannot
    # delay Home. Batch the remainder below GitHub's 256-leg matrix limit.
    slots = 200 - len(priority)
    size = max(1, (len(remaining) + slots - 1) // slots)
    groups = [[repo] for repo in priority] + [remaining[i:i + size] for i in range(0, len(remaining), size)]
    return {"include": [{"id": str(i), "label": ", ".join(repo["full_name"] for repo in group),
                         "repositories": group} for i, group in enumerate(groups)]}


def focus_inventory(inventory, names):
    if not names:
        return inventory
    selected = {repo["full_name"]: repo for repo in inventory["selected"]}
    unknown = set(names) - selected.keys()
    if unknown:
        raise ValueError("Focused repositories are not in the active public inventory: " + ", ".join(sorted(unknown)))
    return {**inventory, "focus": list(dict.fromkeys(names)), "full_selected_count": len(selected),
            "selected": [repo for repo in inventory["selected"] if repo["full_name"] in names],
            "excluded": [*inventory["excluded"], *[
                {**repo, "reason": "outside this manual focused reproduction"}
                for repo in inventory["selected"] if repo["full_name"] not in names]]}


def discover(config, today):
    cutoff = cutoff_date(today, config["activity_years"])
    public_orgs = [org["login"] for org in pages(f"users/{config['profile']}/orgs")]
    owners = sorted(set([config["profile"], *public_orgs, *config["additional_owners"]]))
    excluded_owners = set(x.lower() for x in config["exclude_owners"])
    queried = [owner for owner in owners if owner.lower() not in excluded_owners]
    repositories = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for batch in pool.map(lambda owner: pages(f"users/{owner}/repos?type=owner"), queried):
            repositories.extend(batch)
    selected, excluded = [], []
    for repo in sorted(repositories, key=lambda r: r["full_name"].lower()):
        reason = exclusion(repo, config, cutoff)
        record = {key: repo.get(key) for key in (
            "full_name", "default_branch", "pushed_at", "language", "fork", "html_url")}
        if reason:
            excluded.append({**record, "reason": reason})
        else:
            if not REPOSITORY.fullmatch(record["full_name"]):
                raise ValueError("Invalid repository name")
            selected.append(record)

    def freeze(repo):
        from urllib.parse import quote
        ref = api(f"repos/{repo['full_name']}/git/ref/heads/{quote(repo['default_branch'], safe='')}")
        sha = ref["object"]["sha"]
        if not SHA.fullmatch(sha):
            raise ValueError(f"Invalid commit SHA: {repo['full_name']}")
        return {**repo, "sha": sha}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        selected = list(pool.map(freeze, selected))
    matrix_for(selected)
    return {
        "schema_version": 1, "generated_at": today.isoformat(),
        "activity_cutoff": cutoff.isoformat(), "profile": config["profile"],
        "public_organizations": public_orgs, "queried_owners": queried,
        "excluded_owners": config["exclude_owners"],
        "selected": selected, "excluded": excluded,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "inventory.json")
    parser.add_argument("--date", type=dt.date.fromisoformat, default=dt.datetime.now(dt.timezone.utc).date())
    parser.add_argument("--from-inventory", type=Path)
    parser.add_argument("--only", default="", help="Comma-separated repositories for a manual focused reproduction")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    inventory = (json.loads(args.from_inventory.read_text()) if args.from_inventory
                 else discover(config, args.date))
    names = [name.strip() for name in args.only.split(",") if name.strip()]
    if names and os.environ.get("GITHUB_EVENT_NAME") == "push":
        raise ValueError("Push-triggered runs must cover the complete inventory")
    inventory = focus_inventory(inventory, names)
    matrix = matrix_for(inventory["selected"], config.get("priority_repositories", []))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2) + "\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
    print(f"Selected {len(inventory['selected'])} repositories; excluded {len(inventory['excluded'])}; "
          f"{len(matrix['include'])} matrix legs; cutoff {inventory['activity_cutoff']}")


if __name__ == "__main__":
    main()
