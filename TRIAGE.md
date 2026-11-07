# Ecosystem failure triage

User direction, November 7, 2026: monitor ecosystem CI, fix failures with Stacks and Home first, and push verified fixes as Chris directly to `origin/main`. A 15-minute chat heartbeat continues this work. Notify on meaningful changes or required user input, without repeating unchanged failures.

## Current evidence

- [Engine push cc936893](https://github.com/zig-utils/zig-js/actions/runs/42423315413) passed discovery and the exact-engine/Home runtime build and integration smokes. Its original matrix grouped `home-lang/home` with `home-lang/home-os` in consumer job 12, and `stacksjs/stacks` with `stacksjs/stamped` in job 58. Both jobs were queued when triage began.
- The initial [Home Ballerburg consumer job](https://github.com/zig-utils/zig-js-ecosystem/actions/runs/42422807468/job/128555383458) executed `ui/index.ts` on Home+zig-js and failed with `SyntaxError: import.meta is only valid inside modules.` Its ordinary Bun build also failed, because the sibling `craft` checkout was absent. These are separate failures. That initial manual run was cancelled after bootstrap verification; automatic push runs remain active.
- Home's repository-tool loader emits CommonJS and evaluates a function wrapper. Its current implementation does not lower `import.meta`, so module metadata needs real frontend/host support rather than string replacement. A source reading is not yet a validated fix.
- Bun/Node test schedulers and Bun's native build host are unsupported in the current Home tool. Their upstream tests remain unexecuted and red. Never replace them with fake passes or a native-Bun candidate fallback.

## Work order

1. Give `stacksjs/stacks` and `home-lang/home` independent priority matrix legs. Provide explicit manual focused reproductions while retaining every repository in automatic push runs.
2. Run the two primary consumers, inspect actual failure logs, and reproduce the earliest actionable runtime or integration error. Keep source, host and engine revisions in each report.
3. Fix Home's module-loading/host defects in `home-lang/home`, preserving the local checkout's unrelated branch and untracked evidence. Its suite host is revision-pinned: source fixes need a verified new `home_ref` in `config.json` before ecosystem consumers use them.
4. Address the next Stacks and Home failures, then expand to other ecosystem consumers. Retain unsupported and incomplete checks until actual implementations exist.

## Local constraints

- The ambient compiler is Zig 0.17; the suite requires exactly `0.18.0-dev.146+35accc06e`. Do not call an older local compiler a valid build check.
- Disk space is tight. Remove only this task's temporary files or explicitly reproducible caches without foreign work; avoid downloading entire runtime artifacts unnecessarily.
- `~/Code/Home/lang` is on an unrelated `codex/numeric-match-patterns` branch with untracked evidence. Do not commit or reset that work. Use an isolated checkout for Home fixes.
- Follow each repository's AGENTS.md. Engine semantics changes need a before binary and affected test262 evidence; host-only or orchestration changes must not claim corpus flips or compatibility passes they did not measure.
