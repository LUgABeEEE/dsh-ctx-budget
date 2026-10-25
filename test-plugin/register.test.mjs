import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { apply } from '../lib/index.js'

function registeredTools() {
  const specs = []
  const ctx = {
    logger: { warn() {}, info() {} },
    get() {},
    inject(_deps, fn) {
      fn({
        effect(cb) { cb() },
        tools: { register(spec) { specs.push(spec); return () => {} } },
      })
    },
  }
  apply(ctx, {})
  return specs
}

async function tempDir() {
  return mkdtemp(path.join(os.tmpdir(), 'ctxbudget-plugin-'))
}

test('apply registers exactly the four read-only tools', () => {
  const names = registeredTools().map(spec => spec.name)
  assert.deepEqual(names.slice().sort(), [
    'ctx_budget_audit',
    'ctx_budget_check',
    'ctx_budget_doctor',
    'ctx_budget_report',
  ])
})

test('every tool has parameters, an output schema and a renderer', () => {
  for (const spec of registeredTools()) {
    assert.equal(spec.parameters.type, 'object', spec.name)
    assert.equal(typeof spec.output.schema, 'object', spec.name)
    assert.equal(typeof spec.output.render, 'function', spec.name)
    assert.equal(typeof spec.execute, 'function', spec.name)
    assert.ok(spec.description.length > 40, spec.name)
  }
})

test('ctx_budget_doctor runs against a temporary home and renders a summary', async () => {
  const spec = registeredTools().find(s => s.name === 'ctx_budget_doctor')
  const home = await tempDir()
  const value = await spec.execute({ dsh_home: home })
  assert.equal(value.ok, true, JSON.stringify(value))
  assert.equal(value.result.exists, true)
  const blocks = spec.output.render({}, value)
  assert.match(blocks[0].text, /DSH home/)
})

test('ctx_budget_check reports broken tokens and still returns the report', async () => {
  const spec = registeredTools().find(s => s.name === 'ctx_budget_check')
  const home = await tempDir()
  const doc = path.join(home, 'CAPABILITIES.md')
  await writeFile(doc, 'run `/bin/ls` and `/nope/missing`\n', 'utf8')
  const value = await spec.execute({ doc, base: home })
  assert.equal(value.ok, true, JSON.stringify(value))
  assert.equal(value.result.broken.length, 1)
  const blocks = spec.output.render({}, value)
  assert.match(blocks[0].text, /1 broken/)
})

test('ctx_budget_check refuses a missing doc argument', async () => {
  const spec = registeredTools().find(s => s.name === 'ctx_budget_check')
  assert.deepEqual(await spec.execute({}), { ok: false, error: 'doc is required' })
})
