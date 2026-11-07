# Agent instructions

- Land small conventional commits directly on `main`, using the user's configured Git identity; push to `origin/main`. Do not add authorship trailers.
- Run `python3 -m unittest discover -s tests -v` and validate workflow YAML with actionlint before pushing workflow changes.
- Every candidate JS execution must use the exact pushed zig-js archive through Home. Reference Bun runs and native Zig checks are separate evidence.
- Never turn missing host APIs, unexecuted tests, absent artifacts, empty inventories, failed installs or timeouts into successful checks. Do not implement fake Bun/Node APIs to earn passes.
- Inventory and reports must retain exclusions, unsupported checks and immutable revisions. Public compatibility claims require actual runtime evidence, not orchestration unit tests.
- Keep the reusable workflow caller in zig-utils/zig-js pinned to an immutable suite commit. Do not add PATs or consumer credentials to avoid the existing reusable-workflow design.
- Check package commands in a clean candidate checkout. Keep reference artifacts separate. Never invoke publishing/deployment/release scripts as compatibility checks.
