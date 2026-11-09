# Ecosystem dashboard

The public STX dashboard is deployed to <https://zig-utils.github.io/zig-js-ecosystem/> by `.github/workflows/deploy-site.yml`. It refreshes on site pushes, completed suite runs, manual dispatch and a 15-minute GitHub Actions schedule. GitHub may delay scheduled jobs.

From the repository root:

```sh
python3 scripts/site_data.py
cd site
bun install --frozen-lockfile --ignore-scripts
bun test
bun run build
bun run preview
```

The collector uses `GH_TOKEN` in Actions or the existing authenticated `gh` CLI locally (`GH` can set its path). `--offline` renders the checked-in, dated bootstrap snapshot. Native Bun renders the production STX website; it is separate from candidate compatibility execution and cannot change consumer outcomes.

The collector paginates jobs/artifacts in both engine and suite repositories, reads only JSON from SHA-256-verified report archives, and combines the newest available per-consumer measurements. A focused run never removes other selected repositories. Live job state and measured results have independent provenance. A new queued run does not overwrite older measurements or turn a missing result green. Cross-repository artifact access failures are displayed as refresh warnings with retained, dated bootstrap evidence; no PAT or extra consumer credential is used.

The checked-in bootstrap comes from actual report artifacts, with original run URLs, archive digests, consumer/engine revisions, check outcomes and runtime provenance. It contains public CI evidence only. The generated `snapshot.json`, JSON evidence downloads and repository detail pages preserve every recorded check. Native-only results and coverage-only not-applicable results do not count as zig-js passes. An unsupported host API or unexecuted scheduler is never a pass.

This deployment workflow only reads public CI reports. It never dispatches package publishing/deployment scripts as compatibility tests, and it does not change the engine's immutable suite caller pin.
