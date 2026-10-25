import test from 'node:test'
import assert from 'node:assert/strict'
import { buildArgv, buildEnv, parseJson, resolvePython, runCtxbudget } from '../lib/runner.js'

test('resolvePython: plugin option wins over the environment', () => {
  assert.equal(resolvePython({ python: '/opt/py' }, { CTXBUDGET_PYTHON: '/env/py' }), '/opt/py')
})

test('resolvePython: environment is used when the option is empty', () => {
  assert.equal(resolvePython({ python: '  ' }, { CTXBUDGET_PYTHON: '/env/py' }), '/env/py')
})

test('resolvePython: falls back to python3', () => {
  assert.equal(resolvePython({}, {}), 'python3')
})

test('buildArgv: appends --json and keeps order', () => {
  assert.deepEqual(
    buildArgv(['capabilities', '--doc', '/tmp/caps.md']),
    ['-m', 'ctxbudget', 'capabilities', '--doc', '/tmp/caps.md', '--json'],
  )
})

test('buildArgv: can skip --json', () => {
  assert.deepEqual(buildArgv(['--version'], { json: false }), ['-m', 'ctxbudget', '--version'])
})

test('buildEnv: prepends the package root to PYTHONPATH', () => {
  assert.equal(buildEnv({}, '/repo').PYTHONPATH, '/repo')
  assert.equal(buildEnv({ PYTHONPATH: '/old' }, '/repo').PYTHONPATH, '/repo' + (process.platform === 'win32' ? ';' : ':') + '/old')
})

test('parseJson: accepts JSON and explains bad output', () => {
  assert.deepEqual(parseJson('{"a":1}').value, { a: 1 })
  assert.equal(parseJson('').ok, false)
  assert.equal(parseJson('nope').ok, false)
})

test('runCtxbudget: reports a missing interpreter instead of throwing', async () => {
  const result = await runCtxbudget({ python: 'definitely-not-a-python-binary', argv: ['-m', 'ctxbudget', '--version'] })
  assert.equal(result.pythonMissing, true)
  assert.equal(result.exitCode, null)
})

test('runCtxbudget: runs the bundled CLI through PYTHONPATH', async () => {
  const result = await runCtxbudget({ argv: ['-m', 'ctxbudget', '--version'], timeoutMs: 60_000 })
  assert.equal(result.pythonMissing, undefined)
  assert.equal(result.exitCode, 0, result.stderr)
  assert.match(result.stdout, /ctxbudget 0\.2\.0/)
})
