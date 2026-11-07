# zig-js ecosystem

Downstream compatibility CI for [zig-js](https://github.com/zig-utils/zig-js), covering Chris Breuer's active public repositories and public GitHub organizations. Every new `zig-js` push calls the pinned reusable workflow in this repository with the **exact pushed engine commit**.

The initial [inventory.json](inventory.json), captured on November 7, 2026, selects **223 repositories**. It includes Stacks, Home, Pantry, Den, Craft, Pickier, Mail OS, personal projects, active owned forks, and the Zig libraries. `meemalabs` is explicitly excluded. Public organization discovery is paginated; additional organizations administered by Chris were verified with authenticated membership discovery and recorded in `config.json` to include their public repos even when membership is hidden on the profile. Private, archived, disabled, empty and inactive repositories have recorded exclusion reasons. The engine and this orchestration repository are excluded to avoid recursion.

## What runs

1. Rediscover the profile's public organizations and public repositories with a push in the last two years. Freeze every selected default branch to its commit SHA. New eligible repos are included automatically.
2. Build the pushed zig-js public C archive and a pinned Home `home-tool` linked explicitly against it. Check `Home.engine === "zig-js"` and run Home's TypeScript, module-loader, filesystem and subprocess integration smokes. Hash the executable and archive and verify them on consumer runners.
3. Inspect tracked package manifests, including private packages inside public monorepos and Stacks' `storage/framework` packages. Attempt declared `build`, `test`, `smoke` and `typecheck` scripts, source/export module initialization, and CLI `--help` checks. Discovered test files without a test script still create a test obligation. Fixtures and vendored manifests are listed separately and belong to their enclosing package.
4. Route JavaScript commands (`bun`, `node`, `bunx`, `npx`, `npm`, `pnpm`, `yarn`) through Home + zig-js. Package binaries are resolved from installed dependencies. Bun is used separately to install dependencies with lifecycle scripts disabled. A command that exits zero without invoking the candidate engine cannot count as a zig-js pass.
5. In a separate clean checkout, run the same declared scripts with the normal Bun runtime as reference evidence. Candidate failures are retained even if the reference passes. A reference difference alone does not establish an engine regression.
6. Build native Zig repos and their advertised test step, reporting these as **native Zig checks**, separately from JavaScript compatibility. Build the current Home revision against the candidate engine as a direct consumer, separately from the pinned host runtime.
7. Publish each command's log, engine-invocation events, package plan, excluded manifests, consumer SHA and engine SHA. The final report requires a result for every selected repository at the expected revisions. Missing, failed, unsupported, timed-out or unexecuted checks fail the run.

Every push gets a run; there is no superseding-run cancellation. The matrix runs at most eight consumer jobs concurrently per push, with per-command and per-repository deadlines. A command timeout kills its process group. Inventory growth beyond GitHub's matrix limit is handled by batching consumers without dropping any.

Dependency setup follows declared workspace membership; independent nested apps get their own installs. Candidate execution finishes before its checkout, build outputs and task-owned Bun cache are removed for the clean reference checkout. Command logs stay outside those trees. A late disk-space or orchestration error retains earlier check evidence and remains a failure; consumer files are released before the final JSON is written.

Stacks (`stacksjs/stacks`) and Home (`home-lang/home`) get the first two independent consumer jobs. They cannot delay each other inside a batch. Remaining consumers retain complete coverage.

## Current runtime boundary

This repository creates a compatibility gate; it does **not** assert that all selected projects currently work on zig-js. Home's repository-tool runtime has a limited host API. It is not the complete Bun/Node application runtime and has no upstream Bun/Node test scheduler. `bun test`, `node --test`, `Bun.build`, missing built-ins, browser-only APIs and other absent host features are reported as unsupported or failed. They are not replaced with fake implementations or successful Bun-only runs. Upstream test suites needing those features remain unexecuted and make the gate red until real adapters exist.

Module initialization is only a smoke check. CLI `--help` is only a CLI smoke check. Native Zig checks do not establish zig-js runtime compatibility. Repositories without executable JS/TS/Zig consumers are reported as not applicable, outside runtime passes. Linux is the initial platform; macOS-only tools, external-service apps and repositories requiring additional toolchains can fail or remain unsupported. Dependency installation is frozen when a lockfile exists and never silently retries with a rewritten lockfile. Disabled install hooks can expose missing native build prerequisites, which remain failures.

## Run and inspect

The automatic check appears in [zig-js Actions](https://github.com/zig-utils/zig-js/actions/workflows/ecosystem.yml). Its jobs call the reusable workflow here, so they are attached to the engine push without a cross-repository token or repository-dispatch secret. The final `ecosystem-report` artifact contains `report.md`, `report.json`, the run inventory, every consumer result, and command logs. The `ecosystem-runtime` artifact contains runtime provenance and binaries.

Run manually from [this repository's Actions](https://github.com/zig-utils/zig-js-ecosystem/actions/workflows/ecosystem.yml):

```sh
gh workflow run ecosystem.yml --repo zig-utils/zig-js-ecosystem \
  -f engine_sha=FULL_40_CHARACTER_ZIG_JS_COMMIT -F reference=true
```

An empty engine input resolves `zig-js/main` once, before building. Manual runs can disable the reference lane; automatic pushes enable it. No secrets are passed into consumer scripts. Third-party Actions and the automatic caller pin immutable commits. Source and toolchain pins live in [config.json](config.json); the workflow's eight-job cap is explicit in YAML.

For focused triage, add `-f repositories=stacksjs/stacks,home-lang/home` to a manual dispatch. The inventory and report explicitly identify the subset and retain the omitted-repository reasons. Push-triggered runs reject filtering and always cover the complete inventory.

Local orchestration checks use Python's standard library:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/discover.py --output inventory.json
python3 scripts/discover.py --from-inventory inventory.json --output /tmp/inventory.json
```

Discovery authenticates through `GH_TOKEN`/`GITHUB_TOKEN` in CI or `gh` locally. The checked-in inventory is an audit snapshot; each CI run refreshes it. Package execution requires the runtime artifact and the configured Zig/Bun versions:

```sh
python3 scripts/run.py --repositories '[{"full_name":"stacksjs/ts-collect","sha":"FULL_CONSUMER_COMMIT"}]' \
  --engine-sha FULL_ENGINE_COMMIT --home-tool /absolute/path/runtime/home-tool \
  --engine-root /absolute/path/runtime/engine --reference
```

## Extending coverage

Add owners or explicit repository exclusions in `config.json`, with reasons. Public organizations are discovered automatically; memberships hidden from the public profile require `additional_owners`. Package manifests and their root scripts are the default adapters. Extend `scripts/run.py` for a repository's real native integration or `scripts/runtime.py` only when the host actually supports the command. Add a failing witness for unsupported or missing-result behavior before changing how a check can pass.

After updating the reusable workflow, validate it, push it to this repository's `main`, and update both occurrences of its immutable suite revision in `zig-js/.github/workflows/ecosystem.yml`. Keeping the caller pinned makes each engine run's CI definition reviewable and reproducible, while downstream repository selection remains current.
