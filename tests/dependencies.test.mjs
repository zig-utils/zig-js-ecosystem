import { afterEach, expect, test } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { ancestorDependencies } from '../scripts/dependencies.mjs'

const helper = resolve(import.meta.dir, '../scripts/dependencies.mjs')
const roots = []
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }) })

function put(root, path, contents) {
  const target = join(root, path)
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, typeof contents === 'string' ? contents : JSON.stringify(contents))
}

function fixture({ referenced = true, linker = 'hoisted' } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'ecosystem-dependencies-'))
  roots.push(root)
  put(root, 'package.json', { private: true, workspaces: ['packages/*', '!packages/excluded'],
    dependencies: referenced ? { 'ci-workspace-library': 'workspace:*', 'ci-other': 'workspace:*' } : {} })
  put(root, 'bunfig.toml', `[install]\nlinker = "${linker}"\n`)
  put(root, 'packages/library/package.json', { name: 'ci-workspace-library', version: '1.2.3', main: 'index.js',
    scripts: { postinstall: 'touch lifecycle-ran' } })
  put(root, 'packages/library/index.js', 'module.exports = 42')
  put(root, 'packages/other/package.json', { name: 'ci-other', version: '2.2.0' })
  put(root, 'packages/excluded/package.json', { name: 'ci-excluded', version: '1.0.0' })
  put(root, 'deployment/package.json', { dependencies: { 'ci-workspace-library': 'workspace:*', 'ci-other': '^2.0.0' } })
  return root
}

function bun(cwd, ...args) {
  return Bun.spawnSync([process.execPath, ...args], { cwd, env: { ...process.env, CI: '1' } })
}

test('nested install reproduces workspace failure; ancestor setup runs real dependency code', () => {
  const root = fixture()
  const deployment = join(root, 'deployment')
  const before = bun(deployment, 'install', '--ignore-scripts')
  expect(before.exitCode).not.toBe(0)
  expect(before.stderr.toString()).toContain('Workspace dependency')
  expect(bun(root, 'install', '--ignore-scripts').exitCode).toBe(0)
  const audit = ancestorDependencies(deployment, root)
  expect(audit.reused).toBe(true)
  expect(audit.dependencies).toHaveLength(2)
  expect(audit.dependencies.every(dep => dep.provider && dep.satisfied)).toBe(true)
  expect(bun(deployment, helper, root).exitCode).toBe(0)
  const execute = bun(deployment, '--eval', 'console.log(require("ci-workspace-library"))')
  expect(execute.exitCode).toBe(0)
  expect(execute.stdout.toString().trim()).toBe('42')
  expect(existsSync(join(root, 'packages/library/lifecycle-ran'))).toBe(false)
})

test('unreferenced isolated workspace is not an installed ancestor dependency', () => {
  const root = fixture({ referenced: false, linker: 'isolated' })
  expect(bun(root, 'install', '--ignore-scripts').exitCode).toBe(0)
  const audit = ancestorDependencies(join(root, 'deployment'), root)
  expect(audit.reused).toBe(false)
  expect(audit.dependencies.some(dep => !dep.satisfied)).toBe(true)
})

test('a registry-shaped provider cannot impersonate a declared workspace', () => {
  const root = fixture()
  put(root, 'node_modules/ci-workspace-library/package.json', { name: 'ci-workspace-library', version: '1.2.3' })
  const audit = ancestorDependencies(join(root, 'deployment'), root)
  expect(audit.reused).toBe(false)
  expect(audit.dependencies[0].reason).toContain('not the declared workspace')
})

test('wrong external and explicit workspace versions are rejected', () => {
  const root = fixture()
  expect(bun(root, 'install', '--ignore-scripts').exitCode).toBe(0)
  put(root, 'deployment/package.json', { dependencies: { 'ci-workspace-library': 'workspace:^2.0.0', 'ci-other': '^3.0.0' } })
  const audit = ancestorDependencies(join(root, 'deployment'), root)
  expect(audit.reused).toBe(false)
  expect(audit.dependencies.every(dep => !dep.satisfied)).toBe(true)
})

test('workspace wildcard allows prereleases; a child lockfile prevents reuse', () => {
  const root = fixture()
  expect(bun(root, 'install', '--ignore-scripts').exitCode).toBe(0)
  put(root, 'packages/library/package.json', { name: 'ci-workspace-library', version: '1.2.3-beta.1' })
  expect(ancestorDependencies(join(root, 'deployment'), root).reused).toBe(true)
  put(root, 'deployment/bun.lock', '{}')
  expect(ancestorDependencies(join(root, 'deployment'), root).reused).toBe(false)
})

test('excluded members and outside-checkout symlinks do not satisfy workspace references', () => {
  const root = fixture()
  mkdirSync(join(root, 'node_modules'))
  symlinkSync(join(root, 'packages/excluded'), join(root, 'node_modules/ci-excluded'))
  put(root, 'deployment/package.json', { dependencies: { 'ci-excluded': 'workspace:*' } })
  expect(ancestorDependencies(join(root, 'deployment'), root).reused).toBe(false)
  const other = fixture()
  symlinkSync(join(other, 'packages/library'), join(root, 'node_modules/ci-workspace-library'))
  put(root, 'deployment/package.json', { dependencies: { 'ci-workspace-library': 'workspace:*' } })
  expect(ancestorDependencies(join(root, 'deployment'), root).dependencies[0].reason).toContain('outside checkout')
})

test('independent apps still perform real installs with lifecycle scripts disabled', () => {
  const root = fixture()
  put(root, 'deployment/package.json', { scripts: { postinstall: 'touch lifecycle-ran' } })
  const result = bun(join(root, 'deployment'), helper, root)
  expect(result.exitCode).toBe(0)
  expect(result.stdout.toString()).toContain('bun install')
  expect(existsSync(join(root, 'deployment/lifecycle-ran'))).toBe(false)
})
