import { mkdir, rm } from 'node:fs/promises'
import { renderTemplate } from '@stacksjs/stx'
import { makeModel } from './model'

const root = import.meta.dir
const snapshot = await Bun.file(`${root}/data/snapshot.json`).json()
const model = makeModel(snapshot)
if (!model.total) throw new Error('Refusing to publish an empty inventory')
const dist = `${root}/dist`
await rm(dist, { recursive: true, force: true })
await mkdir(`${dist}/assets`, { recursive: true })
const render = (name: string, context: any) => renderTemplate(`${root}/views/${name}.stx`, {
  context, injectCSS: false,
})
await Bun.write(`${dist}/index.html`, await render('index', model))
await Bun.write(`${dist}/assets/site.css`, Bun.file(`${root}/assets/site.css`))
await Bun.write(`${dist}/snapshot.json`, JSON.stringify(snapshot))
await Bun.write(`${dist}/.nojekyll`, '')
await Bun.write(`${dist}/favicon.svg`, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" rx="10" fill="#f7a541"/><path d="M12 13h25L20 30h16v6H10l17-17H12z" fill="#171a17"/></svg>')
for (const row of model.rows) {
  const dir = `${dist}/repos/${row.name.replace('/', '--')}`
  await mkdir(dir, { recursive: true })
  await Bun.write(`${dir}/index.html`, await render('repository', { repo: row, generated: model.generated }))
  await Bun.write(`${dir}/evidence.json`, JSON.stringify(snapshot.records.find((r: any) => r.name === row.name) || {
    name: row.name, status: 'unknown', reason: 'No measured result in the published snapshot',
  }, null, 2))
}
console.log(`Built STX dashboard: ${model.total} repositories, ${model.measured} measured; ${model.candidateChecks} candidate checks`)
