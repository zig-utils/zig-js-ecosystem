// Setup only: native Bun installs dependencies; consumer execution uses Home.
import { existsSync, readFileSync, realpathSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const locks = ['bun.lock', 'bun.lockb', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml']
const manifest = directory => JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8'))
const inside = (root, path) => {
  const rel = relative(root, path)
  return rel !== '..' && !rel.startsWith('../') && !isAbsolute(rel)
}

function workspaceMembers(root) {
  const members = new Set()
  const pending = [root]
  while (pending.length) {
    const directory = pending.pop()
    if (members.has(directory)) continue
    members.add(directory)
    const workspaces = manifest(directory).workspaces ?? []
    const patterns = Array.isArray(workspaces) ? workspaces : workspaces.packages ?? []
    const children = new Set()
    for (const pattern of patterns.filter(p => !p.startsWith('!'))) {
      for (const child of new Bun.Glob(pattern).scanSync({ cwd: directory, onlyFiles: false })) {
        const path = resolve(directory, child)
        if (existsSync(join(path, 'package.json')) && inside(root, realpathSync(path))) {
          children.add(realpathSync(path))
        }
      }
    }
    for (const pattern of patterns.filter(p => p.startsWith('!'))) {
      for (const child of new Bun.Glob(pattern.slice(1)).scanSync({ cwd: directory, onlyFiles: false })) {
        const path = resolve(directory, child)
        if (existsSync(path)) children.delete(realpathSync(path))
      }
    }
    pending.push(...children)
  }
  return members
}

export function ancestorDependencies(directory, checkout) {
  directory = realpathSync(directory)
  checkout = realpathSync(checkout)
  if (!inside(checkout, directory)) throw new Error('Dependency scope is outside checkout')
  const pkg = manifest(directory)
  const dependencies = ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']
    .flatMap(group => Object.entries(pkg[group] ?? {}).map(([name, range]) => ({ group, name, range })))
  // A child lockfile must retain its exact install contract. Reuse is narrowly
  // for workspace-dependent deployment/apps resolved by an ancestor install.
  if (directory === checkout || locks.some(lock => existsSync(join(directory, lock)))
      || !dependencies.some(dep => dep.range.startsWith('workspace:'))) {
    return { reused: false, reason: 'independent install or locked scope', dependencies: [] }
  }
  const members = existsSync(join(checkout, 'package.json')) ? workspaceMembers(checkout) : new Set()
  const evidence = dependencies.map(dep => {
    let provider
    if (!/^(@[^/]+\/)?[^/.][^/]*$/.test(dep.name)) return { ...dep, satisfied: false, reason: 'unsupported dependency name' }
    for (let current = directory; inside(checkout, current); current = dirname(current)) {
      const candidate = join(current, 'node_modules', dep.name, 'package.json')
      if (existsSync(candidate)) {
        provider = candidate
        break
      }
      if (current === checkout) break
    }
    if (!provider) return { ...dep, satisfied: false, reason: 'no installed provider inside checkout' }
    const installed = JSON.parse(readFileSync(provider, 'utf8'))
    const source = realpathSync(dirname(provider))
    if (!inside(checkout, source)) {
      return { ...dep, provider, satisfied: false, reason: 'provider points outside checkout' }
    }
    let range = dep.range
    let anyWorkspaceVersion = false
    if (range.startsWith('workspace:')) {
      range = range.slice(10)
      if (!members.has(source) || installed.name !== dep.name) {
        return { ...dep, provider, satisfied: false, reason: 'provider is not the declared workspace package' }
      }
      anyWorkspaceVersion = ['*', '^', '~'].includes(range)
    }
    let satisfied = anyWorkspaceVersion
    if (!satisfied) {
      try { satisfied = Bun.semver.satisfies(installed.version, range) } catch {}
    }
    return { ...dep, provider, source, installed_version: installed.version, satisfied,
      ...(!satisfied ? { reason: 'installed version does not satisfy dependency range' } : {}) }
  })
  return { reused: evidence.every(dep => dep.satisfied), dependencies: evidence }
}

if (import.meta.main) {
  const [checkout, ...installFlags] = process.argv.slice(2)
  const audit = ancestorDependencies(process.cwd(), checkout)
  console.log(JSON.stringify({ dependency_setup: audit }, null, 2))
  if (!audit.reused) {
    const result = spawnSync(process.execPath, ['install', '--ignore-scripts', ...installFlags], {
      stdio: 'inherit', env: process.env,
    })
    if (result.error) throw result.error
    process.exit(result.status ?? 1)
  }
}
