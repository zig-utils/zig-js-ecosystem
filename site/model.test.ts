import { expect, test } from 'bun:test'
import { makeModel } from './model'
import { renderTemplate } from '@stacksjs/stx'

test('every selected consumer remains visible, with unknown evidence and separate native/reference lanes', () => {
  const model = makeModel({ generated_at: '2026-11-09T20:00:00Z', warnings: [], live: {},
    inventory: { selected: [{ full_name: 'home-lang/home' }, { full_name: 'stacksjs/stacks' }], excluded_owners: ['meemalabs'] },
    records: [{ name: 'home-lang/home', status: 'failed', sha: 'a'.repeat(40), engine_sha: 'b'.repeat(40),
      source: { measured_at: '2026-11-08T20:00:00Z' }, lanes: { candidate: { failed: 1 }, reference: { passed: 1 } }, checks: [] }],
  })
  expect(model.total).toBe(2)
  expect(model.measured).toBe(1)
  expect(model.counts.passed).toBe(0)
  expect(model.rows[0].status).toBe('unknown')
  expect(model.rows[1].candidate).toBe('1 failing')
  expect(model.rows[1].reference).toBe('1 passed')
})

test('STX escapes untrusted check output instead of executing report content', async () => {
  const html = await renderTemplate(`${import.meta.dir}/views/repository.stx`, { injectCSS: false, context: {
    generated: '2026-11-09', repo: { name: 'test/repo', label: 'Failing', status: 'failed', sourceUrl: 'https://github.com/test/repo',
      scopes: 1, checks: [{ label: '<img src=x onerror=alert(1)>', reason: '</td><script>alert(2)</script>',
        lane: 'candidate', status: 'failed', statusLabel: 'Failing', secondsLabel: '1s' }] },
  } })
  expect(html).not.toContain('<img src=x onerror=alert(1)>')
  expect(html).not.toContain('</td><script>alert(2)</script>')
  expect(html).toContain('&lt;img')
})
