/**
 * dsh-ctx-budget — DSH plugin half: four read-only tools backed by the bundled
 * `ctxbudget` Python CLI.
 *
 * Why a CLI wrapper instead of a pure-JS reimplementation: the audit logic is a
 * Python package with 96 tests and a documented release checklist; duplicating
 * it in TypeScript would create two implementations that drift. The plugin
 * therefore ships the Python package (the `ctxbudget/` directory) and runs it
 * with the interpreter of the host's choice.
 *
 * Everything registered here is read-only:
 *   ctx_budget_report   six-layer static-context snapshot
 *   ctx_budget_audit    memory duplicate / stale / over-limit report
 *   ctx_budget_check    verify backticked paths and commands in a capability map
 *   ctx_budget_doctor   environment / layout check
 *
 * `memory-archive` is deliberately NOT exposed: it moves entries, and a model
 * should not be able to do that through a tool call without a person watching.
 */
import { PACKAGE_ROOT, buildArgv, parseJson, resolvePython, runCtxbudget } from './runner.js'

export const name = 'dsh-ctx-budget'
export const inject = ['tools']

const ANY_JSON = () => ({})
const MAX_TEXT = 16_000

function truncate(text) {
  const value = String(text ?? '')
  return value.length > MAX_TEXT ? value.slice(0, MAX_TEXT) + '…' : value
}

function textBlocks(body) {
  return [{ type: 'text', text: truncate(body).trimEnd() }]
}

function failureText(tool, payload) {
  return `${tool} failed: ${payload?.error ?? 'unknown error'}` +
    (payload?.exitCode !== undefined && payload?.exitCode !== null ? ` (exit ${payload.exitCode})` : '') +
    (payload?.stderr ? `\n\n${truncate(payload.stderr)}` : '') +
    (payload?.hint ? `\n${payload.hint}` : '')
}

async function invoke(command, config) {
  const timeoutMs = Number.isFinite(config.timeoutMs) ? config.timeoutMs : undefined
  const res = await runCtxbudget({
    python: resolvePython(config),
    argv: buildArgv(command),
    ...(timeoutMs ? { timeoutMs } : {}),
  })
  if (res.pythonMissing) {
    return {
      ok: false,
      error: 'python_missing',
      hint: 'Set the plugin option `python` (for example "python3.12") or the CTXBUDGET_PYTHON environment variable.',
      command,
    }
  }
  if (res.timedOut) return { ok: false, error: 'timeout', command, stderr: truncate(res.stderr) }
  const parsed = parseJson(res.stdout)
  if (!parsed.ok) {
    return { ok: false, error: parsed.error, exitCode: res.exitCode, command, stderr: truncate(res.stderr) }
  }
  return { ok: true, exitCode: res.exitCode, durationMs: res.durationMs, command, result: parsed.value }
}

function fmtBytes(value) {
  return typeof value === 'number' ? value + ' B' : 'not present'
}

function renderReport(value) {
  if (!value || value.ok !== true) return textBlocks(failureText('ctx_budget_report', value))
  const s = value.result ?? {}
  const l1 = s.L1_rules ?? {}
  const l2 = s.L2_memory ?? {}
  const l3 = s.L3_skills ?? {}
  const l4 = s.L4_tools ?? {}
  const l5 = s.L5_sessions ?? {}
  const l6 = s.L6_kb ?? {}
  const mcp = (l4.cordis ?? {}).mcp_enabled
  return textBlocks([
    `Static-context baseline for ${s.home ?? 'the agent home'} (${s.generated_at ?? 'unknown time'}).`,
    '',
    `- rules: global AGENTS.md ${fmtBytes(l1.global_agents_bytes)}, project AGENTS.md ${fmtBytes(l1.project_agents_bytes)}`,
    `- memory: ${l2.items ?? 0} item(s) across ${l2.files ?? 0} file(s), ~${l2.est_tokens_cn ?? 0} tokens (estimate)`,
    `- skills: ${l3.count ?? 0} SKILL.md, metadata ~${l3.est_tokens ?? 0} tokens (estimate)`,
    `- tool surface: ${mcp === undefined ? 'unknown' : mcp + ' MCP server(s) enabled'}; exact schema tokens are not estimated (session logs truncate descriptions)`,
    `- sessions: ${l5.files ?? 0} file(s), ${l5.mb ?? 0} MB on disk`,
    `- knowledge base: ${l6.bytes ? 'present, ' + fmtBytes(l6.bytes) : 'not found'}`,
    '',
    'Estimates use 2 chars/token for CJK and 3.5 for English. Tool-surface numbers are a lower bound.',
  ].join('\n'))
}

function renderAudit(value) {
  if (!value || value.ok !== true) return textBlocks(failureText('ctx_budget_audit', value))
  const a = value.result ?? {}
  const stale = Array.isArray(a.stale) ? a.stale.length : 0
  const over = Array.isArray(a.over) ? a.over : []
  const lines = [
    `Memory audit: ${a.total ?? 0} item(s) across ${a.files ?? 0} file(s).`,
    `- entries without tags: ${a.no_tags ?? 0}`,
    `- duplicate-content groups: ${a.dup_group_count ?? 0}`,
    `- entries older than 90 days: ${stale}`,
    `- over-limit files (>300 items or >20k estimated tokens): ${over.length}`,
  ]
  if (over.length) {
    lines.push('', 'Over-limit files:', ...over.map(o => `- ${o.scope}: ${o.items} item(s), ~${o.est_tokens_cn} tokens`))
  }
  lines.push('', 'Read-only: nothing was modified. Archiving is a separate shell command that requires --apply.')
  return textBlocks(lines.join('\n'))
}

function renderCheck(value) {
  if (!value || value.ok !== true) return textBlocks(failureText('ctx_budget_check', value))
  const c = value.result ?? {}
  const broken = Array.isArray(c.broken) ? c.broken : []
  if (broken.length === 0) {
    return textBlocks(`Capability map ${c.doc}: ${c.checked ?? 0} token(s) checked, no broken paths or commands.`)
  }
  return textBlocks([
    `Capability map ${c.doc}: ${c.checked ?? 0} token(s) checked, ${broken.length} broken.`,
    '',
    ...broken.map(b => `- ${b.kind}: \`${b.token}\`${b.path && b.path !== b.token ? ` -> ${b.path}` : ''}`),
    '',
    'Fix the map or the missing target: a model following this map would try to use things that no longer exist.',
  ].join('\n'))
}

function renderDoctor(value) {
  if (!value || value.ok !== true) return textBlocks(failureText('ctx_budget_doctor', value))
  const d = value.result ?? {}
  const checks = Array.isArray(d.checks) ? d.checks : []
  return textBlocks([
    `DSH home ${d.home}: ${d.exists ? 'found' : 'missing'}.`,
    '',
    ...checks.map(c => `- ${c.name}: ${c.status}${c.note ? ' — ' + c.note : ''}`),
  ].join('\n'))
}

const HOME_PARAM = {
  type: 'string',
  description: 'Agent home to inspect. Defaults to the host environment: $DSH_HOME, else ~/.dsh.',
}

const REPORT_PARAMETERS = {
  type: 'object',
  properties: {
    dsh_home: HOME_PARAM,
    workspace: { type: 'string', description: 'Workspace whose AGENTS.md should be measured as the project rule layer.' },
    kb: { type: 'string', description: 'Knowledge-base directory or kb.sqlite path, when one exists.' },
    include_cost: { type: 'boolean', description: 'Also read the usage/cost layer (.dshw-usage.json) when present.' },
  },
  additionalProperties: false,
}

const AUDIT_PARAMETERS = {
  type: 'object',
  properties: { dsh_home: HOME_PARAM },
  additionalProperties: false,
}

const CHECK_PARAMETERS = {
  type: 'object',
  properties: {
    doc: { type: 'string', description: 'Path to the markdown capability map (for example CAPABILITIES.md). Required.' },
    base: { type: 'string', description: 'Base directory for relative path tokens. Defaults to the plugin package directory.' },
  },
  required: ['doc'],
  additionalProperties: false,
}

const DOCTOR_PARAMETERS = {
  type: 'object',
  properties: {
    dsh_home: HOME_PARAM,
    workspace: { type: 'string', description: 'Also check the workspace AGENTS.md.' },
  },
  additionalProperties: false,
}

function homeFlags(args) {
  const flags = []
  if (typeof args.dsh_home === 'string' && args.dsh_home.trim() !== '') flags.push('--dsh-home', args.dsh_home.trim())
  return flags
}

export function apply(ctx, config = {}) {
  const host = ctx ?? {}
  const settings = typeof config === 'object' && config !== null ? config : {}
  const logger = host.logger

  host.inject?.(['tools'], (toolCtx) => {
    toolCtx.effect?.(() => {
      const register = (spec) => {
        try {
          toolCtx.tools.register(spec)
        } catch (error) {
          // A name clash must degrade one tool, not the whole plugin row.
          logger?.warn?.(`[dsh-ctx-budget] failed to register ${spec.name}: ${String(error)}`)
        }
      }

      register({
        name: 'ctx_budget_report',
        description:
          'Measure the static context this DSH home pays for on every request: rule files, memory store size, skill metadata, enabled MCP servers, session-log volume and knowledge-base size. Read-only. Token figures are estimates (CJK 2 chars/token, English 3.5); exact tool-schema tokens are not estimated because session logs truncate descriptions.',
        parameters: REPORT_PARAMETERS,
        output: { schema: ANY_JSON(), render: (_args, value) => renderReport(value) },
        execute: async (args) => {
          const argv = ['baseline', ...homeFlags(args)]
          if (typeof args.workspace === 'string' && args.workspace.trim()) argv.push('--workspace', args.workspace.trim())
          if (typeof args.kb === 'string' && args.kb.trim()) argv.push('--kb', args.kb.trim())
          if (args.include_cost === true) argv.push('--cost')
          return invoke(argv, settings)
        },
      })

      register({
        name: 'ctx_budget_audit',
        description:
          'Read-only memory audit for this DSH home: entries per file, entries without tags, groups of identical content, entries older than 90 days, and files over the size limits (300 items or 20k estimated tokens). Nothing is deleted or moved.',
        parameters: AUDIT_PARAMETERS,
        output: { schema: ANY_JSON(), render: (_args, value) => renderAudit(value) },
        execute: async (args) => invoke(['memory-audit', ...homeFlags(args)], settings),
      })

      register({
        name: 'ctx_budget_check',
        description:
          'Verify the backticked paths and commands in a markdown capability map (CAPABILITIES.md and similar). Absolute paths and path-like tokens must exist; relative tokens resolve against the base directory. Returns the broken entries, so a stale map is caught before a model follows it.',
        parameters: CHECK_PARAMETERS,
        output: { schema: ANY_JSON(), render: (_args, value) => renderCheck(value) },
        execute: async (args) => {
          const doc = typeof args.doc === 'string' ? args.doc.trim() : ''
          if (!doc) return { ok: false, error: 'doc is required' }
          const argv = ['capabilities', '--doc', doc, ...homeFlags(args)]
          if (typeof args.base === 'string' && args.base.trim()) argv.push('--base', args.base.trim())
          return invoke(argv, settings)
        },
      })

      register({
        name: 'ctx_budget_doctor',
        description:
          'Check that the agent home layout and the Python runtime this plugin needs are usable: home directory, rules file, memory, skills, sessions, profiles, and the zstd CLI used to read compressed session logs. Read-only.',
        parameters: DOCTOR_PARAMETERS,
        output: { schema: ANY_JSON(), render: (_args, value) => renderDoctor(value) },
        execute: async (args) => {
          const argv = ['doctor', ...homeFlags(args)]
          if (typeof args.workspace === 'string' && args.workspace.trim()) argv.push('--workspace', args.workspace.trim())
          return invoke(argv, settings)
        },
      })
    })
  })
}

export { PACKAGE_ROOT }
