# Ecosystem failure triage

User direction, November 7, 2026: monitor ecosystem CI, fix failures with Stacks and Home first, and push verified fixes as Chris directly to `origin/main`. A 15-minute chat heartbeat continues this work. Notify on meaningful changes or required user input, without repeating unchanged failures.

## Current evidence

- [Engine push cc936893](https://github.com/zig-utils/zig-js/actions/runs/42423315413) passed discovery and the exact-engine/Home runtime build and integration smokes. Its original matrix grouped `home-lang/home` with `home-lang/home-os` in consumer job 12, and `stacksjs/stacks` with `stacksjs/stamped` in job 58. Both jobs were queued when triage began.
- The initial [Home Ballerburg consumer job](https://github.com/zig-utils/zig-js-ecosystem/actions/runs/42422807468/job/128555383458) executed `ui/index.ts` on Home+zig-js and failed with `SyntaxError: import.meta is only valid inside modules.` Its ordinary Bun build also failed, because the sibling `craft` checkout was absent. These are separate failures. That initial manual run was cancelled after bootstrap verification; automatic push runs remain active.
- **Fixed and pushed:** [home-lang/home d703569e9](https://github.com/home-lang/home/commit/d703569e9a0413abc8af8a510d3931bf43f7dba8) adds opt-in frontend lowering and lexical per-module metadata. The saved before binary exits 1 with the CI SyntaxError; the after probe's actual URL/path/main/dir/file values match Bun. The emitter binary passed 2/2 linked tests. Existing Home smokes plus new cache, nested/escaped binding, URL-escaping and CommonJS-arguments witnesses pass on system JSC and the local zig-js archive. Local builds used Zig 0.17.0-dev.1857; promotion still needs the exact 0.18 CI bootstrap against the pushed engine.
- Suite commit `293a33c` gives Stacks and Home independent first legs and explicit manual focused reproductions; 18 orchestration tests pass. [The first focused baseline](https://github.com/zig-utils/zig-js-ecosystem/actions/runs/42424124733) uses the old Home host and remains useful for failure comparison. Its two consumer jobs were still running when the metadata fix landed.
- Bun/Node test schedulers and Bun's native build host are unsupported in the current Home tool. Their upstream tests remain unexecuted and red. Never replace them with fake passes or a native-Bun candidate fallback.

## Work order

1. Verify the updated `home_ref` in the exact-compiler/exact-engine CI bootstrap, then promote that immutable suite revision in the zig-js caller.
2. Inspect the focused Stacks/Home consumer artifacts and reproduce the next actionable error. Stacks' buddy wrapper can request `--conditions development`; the router and Home package resolver need real conditional-export support before accepting that flag. Do not ignore it to advance execution.
3. Continue Home module-loading/host fixes in the isolated checkout at `~/Code/Home/zig-js-ecosystem-home`, preserving the original checkout's unrelated branch and untracked evidence. Its suite host is revision-pinned: source fixes need a verified new `home_ref` before ecosystem consumers use them.
4. Address the next Stacks and Home failures, then expand to other ecosystem consumers. Retain unsupported and incomplete checks until actual implementations exist.

## Local constraints

- The ambient compiler is Zig 0.17; the suite requires exactly `0.18.0-dev.146+35accc06e`. Do not call an older local compiler a valid build check.
- Disk space is tight. Remove only this task's temporary files or explicitly reproducible caches without foreign work; avoid downloading entire runtime artifacts unnecessarily.
- `~/Code/Home/lang` is on an unrelated `codex/numeric-match-patterns` branch with untracked evidence. Do not commit or reset that work. Use an isolated checkout for Home fixes.
- Follow each repository's AGENTS.md. Engine semantics changes need a before binary and affected test262 evidence; host-only or orchestration changes must not claim corpus flips or compatibility passes they did not measure.
