export const labels: Record<string, string> = {
  passed: 'Passed', failed: 'Failing', unsupported: 'Unsupported', 'not-run': 'Not executed',
  unknown: 'Awaiting evidence', 'native-only': 'Native only', blocked: 'Setup blocked',
  'not-applicable': 'Not applicable', 'timed-out': 'Timed out', 'memory-exceeded': 'Memory limit',
}

export function date(value?: string) {
  return value ? new Date(value).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : 'No measurement yet'
}

export function laneSummary(counts: Record<string, number> = {}) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  if (!total) return 'No checks recorded'
  return Object.entries(counts).map(([status, count]) => `${count} ${labels[status]?.toLowerCase() || status}`).join(' · ')
}

export function makeModel(snapshot: any) {
  const records = new Map(snapshot.records.map((r: any) => [r.name, r]))
  const latestEngine = snapshot.live['zig-utils/zig-js']?.head_sha
  const rows = snapshot.inventory.selected.map((selected: any) => {
    const record: any = records.get(selected.full_name)
    const name = selected.full_name
    const [owner, shortName] = name.split('/')
    const status = record?.status || 'unknown'
    const candidate = record?.lanes.candidate || {}
    const count = Object.values(candidate).reduce((a: number, b: any) => a + b, 0)
    return {
      name, owner, shortName, status, label: labels[status],
      language: selected.language || 'Other',
      href: `./repos/${name.replace('/', '--')}/`,
      sourceUrl: `https://github.com/${name}`,
      candidate: laneSummary(candidate), candidateCount: count, candidatePassed: candidate.passed || 0,
      reference: laneSummary(record?.lanes.reference), native: laneSummary(record?.lanes['native-zig'] || record?.lanes.native),
      hasNative: Boolean(record?.lanes['native-zig'] || record?.lanes.native),
      engine: record?.engine_sha?.slice(0, 8) || '—', sha: record?.sha?.slice(0, 8) || '—',
      fullEngine: record?.engine_sha, fullSha: record?.sha,
      hasProvenance: Boolean(record?.provenance?.suite_ref && record?.provenance?.home_ref),
      scopes: record?.scopes || 0, measured: date(record?.source.measured_at),
      priorEngine: Boolean(record && latestEngine && record.engine_sha !== latestEngine),
      source: record?.source, provenance: record?.provenance,
      checks: (record?.checks || []).map((check: any) => ({ ...check,
        label: check.label || 'Unnamed check', lane: check.lane || 'unknown',
        statusLabel: labels[check.status] || check.status,
        secondsLabel: Number.isFinite(check.seconds) ? `${check.seconds.toFixed(2)}s` : '—',
        reason: check.reason || '',
      })),
    }
  }).sort((a: any, b: any) => {
    const priority = ['stacksjs/stacks', 'home-lang/home']
    return (priority.includes(a.name) ? priority.indexOf(a.name) - 2 : 0)
      - (priority.includes(b.name) ? priority.indexOf(b.name) - 2 : 0) || a.name.localeCompare(b.name)
  })
  const counts = Object.fromEntries(Object.keys(labels).map(key => [key, rows.filter((r: any) => r.status === key).length]))
  const measured = rows.filter((r: any) => r.source).length
  const candidateChecks = rows.reduce((sum: number, r: any) => sum + r.candidateCount, 0)
  return {
    rows, counts, measured, candidateChecks, total: rows.length,
    owners: [...new Set(rows.map((r: any) => r.owner))].sort(),
    priorities: rows.filter((r: any) => ['stacksjs/stacks', 'home-lang/home'].includes(r.name)),
    statuses: ['failed', 'unsupported', 'not-run', 'blocked', 'passed', 'native-only', 'not-applicable', 'unknown']
      .map(status => ({ status, label: labels[status], count: counts[status] })),
    runs: Object.entries(snapshot.live).map(([repository, run]: [string, any]) => ({
      repository, ...run, sha: run.head_sha?.slice(0, 8),
      state: run.status === 'completed' ? run.conclusion : run.status.replaceAll('_', ' '),
      stateClass: run.status === 'completed' ? (run.conclusion === 'success' ? 'passed' : 'failed') : 'pending',
      completed: run.jobs.filter((j: any) => j.status === 'completed').length, total: run.jobs.length,
    })),
    generated: date(snapshot.generated_at), warnings: snapshot.warnings || [],
    inventoryDate: snapshot.inventory.generated_at, cutoff: snapshot.inventory.activity_cutoff,
    exclusions: snapshot.inventory.excluded_owners.join(', '),
    latestEngine: latestEngine?.slice(0, 8) || 'Unknown',
    clientRows: rows.map((r: any) => ({ name: r.name, owner: r.owner, status: r.status })),
  }
}
