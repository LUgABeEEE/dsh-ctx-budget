/**
 * Runs the bundled `ctxbudget` Python CLI and returns its JSON.
 *
 * The Python package ships inside this npm package (the `ctxbudget/` directory),
 * so the plugin does not depend on a separate install: it resolves the package
 * root from this file and puts it on PYTHONPATH.
 *
 * Nothing here writes to disk unless the CLI is asked to (all four tools are
 * read-only), and the interpreter is configurable so a venv or a Windows
 * launcher can be used instead of `python3`.
 */
import { execFile } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

const DEFAULT_TIMEOUT_MS = 120_000
const MAX_BUFFER = 32 * 1024 * 1024

/** --python config > CTXBUDGET_PYTHON > python3 */
export function resolvePython(config = {}, env = process.env) {
  const fromConfig = typeof config.python === 'string' ? config.python.trim() : ''
  if (fromConfig) return fromConfig
  const fromEnv = typeof env.CTXBUDGET_PYTHON === 'string' ? env.CTXBUDGET_PYTHON.trim() : ''
  if (fromEnv) return fromEnv
  return 'python3'
}

/**
 * Build the argv for `python -m ctxbudget ...`.
 * `command` is the subcommand plus its flags (without --json); --json is appended.
 */
export function buildArgv(command, options = {}) {
  const argv = ['-m', 'ctxbudget', ...command]
  if (options.json !== false) argv.push('--json')
  return argv
}

/** PYTHONPATH that makes the bundled package (and any existing entries) importable. */
export function buildEnv(baseEnv, root = PACKAGE_ROOT) {
  const existing = baseEnv.PYTHONPATH
  return {
    ...baseEnv,
    PYTHONPATH: existing ? root + path.delimiter + existing : root,
  }
}

/**
 * Run the CLI. Never throws: a missing interpreter, a timeout and a non-zero
 * exit are all reported as values so the tool can explain itself to the model.
 */
export function runCtxbudget(options) {
  const {
    python = resolvePython(),
    root = PACKAGE_ROOT,
    argv,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    env = process.env,
    cwd,
  } = options

  const started = Date.now()
  return new Promise((resolve) => {
    execFile(
      python,
      argv,
      {
        cwd: cwd ?? root,
        env: buildEnv(env, root),
        timeout: timeoutMs,
        maxBuffer: MAX_BUFFER,
        windowsHide: true,
      },
      (error, stdout, stderr) => {
        const durationMs = Date.now() - started
        const base = { exitCode: 0, stdout: stdout ?? '', stderr: stderr ?? '', durationMs }
        if (!error) return resolve(base)
        const code = typeof error.code === 'number' ? error.code : null
        resolve({
          ...base,
          exitCode: code,
          stderr: (stderr ?? '') + (code === null ? String(error.message ?? error) : ''),
          timedOut: error.killed === true,
          pythonMissing: error.code === 'ENOENT',
        })
      },
    )
  })
}

/** Parse stdout as JSON; returns {ok, value} or {ok:false, error}. */
export function parseJson(stdout) {
  const text = String(stdout ?? '').trim()
  if (!text) return { ok: false, error: 'the CLI produced no output' }
  try {
    return { ok: true, value: JSON.parse(text) }
  } catch (error) {
    return { ok: false, error: 'the CLI output was not JSON: ' + String(error.message ?? error) }
  }
}
