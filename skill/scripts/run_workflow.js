#!/usr/bin/env node
// Run an orchestrate workflow script on Codex with the control flow Claude Code runs natively.
// Every agent() call becomes one `codex exec` process with a strict output schema, except the
// relay steps (gate, finish, scaffold), whose single helper command runs here. Every agent is
// journaled. On a rerun, a codex agent whose result is journaled under the same key is replayed
// instead of spawned; relays always run again against the current worktree. Every child runs in
// its own process group, which is killed when the child exceeds its time limit or the runner is
// stopped, and is recorded while it runs so that a later run can find groups left behind.
'use strict'
const fs = require('fs')
const os = require('os')
const path = require('path')
const crypto = require('crypto')
const childProcess = require('child_process')
const { spawn } = childProcess

const USAGE = `usage: run_workflow.js --script FILE --args FILE [--codex BIN] [--max-parallel N]
                       [--journal FILE] [--out FILE] [--log-dir DIR] [--agent-timeout S]
                       [--idle-timeout S] [--relay-timeout S] [--kill-leftovers]

Run a workflow script (skill/workflows/orchestrate-execute.js) under Node on Codex with the
globals of the Claude Code workflow runtime: agent, parallel, pipeline, phase, log, args and
budget. --args is the JSON printed by \`plan.py workflow-args\` for a codex plan.

agent(prompt, opts) is mapped by its label \`kind:task-id:...\`, the task looked up in args.tasks:
  impl, rework      codex exec in the task worktree, sandbox workspace-write, implementer role;
                    the repository's common Git directory is added with --add-dir so staging works
  review, confirm   codex exec in the task worktree, sandbox read-only, reviewer role
  gate, finish,     relays, never sent to codex: the last line of the prompt must be
  scaffold          \`<args.scripts.python> '<args.scripts.task>' <kind> ...\` with plain or
                    single-quoted words only; it runs here with bash -c in the task worktree
                    and its stdout, validated against the schema, is the result
Codex gets --output-schema with the strict form of opts.schema (every object closed, every
property required). The final message is validated against the original schema; an invalid,
unparsable, failed (non-zero exit) or stopped (timed out) attempt runs once more, with the
problem appended to the prompt, and a second failure makes agent() return null. The model and
effort come from opts; when opts names no model, args.routing.codex.tiers["3"] is used, and
without it the run stops. When args.role_text has no text for the role, the role's
developer_instructions from codex/agents/orchestrate-<role>.toml are prepended to the prompt.

Every agent is journaled: {"type":"started","key","agentId","label","phase"}, then
{"type":"result","key","agentId","result"} or {"type":"failed","key","agentId"}; the key is the
sha256 of the label, prompt, model, effort and sandbox. A rerun of the same command replays the
journaled result of a codex agent with the same key (implementer prompts carry the spec's sha256,
so a changed spec runs the implementer again). Relays are never replayed: gate, scaffold and
finish run again against the current worktree on every run (scaffolding is idempotent) and are
journaled again.

Before anything runs, every task not marked done is checked against args: the sha256 of its spec
file must equal spec_sha256, and the id of its worktree (plan.py worktree_id: sha256 over the
absolute path, the inode and the ctime of <worktree>/.git) must equal worktree_id, so a worktree
removed and recreated since then is refused. Either mismatch exits 2; regenerate the arguments
with plan.py workflow-args.

Every child runs in its own process group. A child that exceeds its limit has the whole group
stopped: SIGTERM, then SIGKILL 5 seconds later. For codex that counts as a failed attempt; a
stopped relay returns null. While a child runs, its group is listed in
<run_dir>/agents/process-groups.json (<log-dir>/process-groups.json without run_dir). A run that
finds a group there still alive refuses to start (exit 2) unless --kill-leftovers is given. On
SIGINT, SIGTERM or SIGHUP the runner stops every child group (SIGTERM, then SIGKILL after 5
seconds) and exits with 128 plus the signal number; rerun the same command to resume.

Only one runner works on a run directory: <run_dir>/agents/runner.lock (<log-dir>/runner.lock
without run_dir) is created exclusively with the runner's pid and start time and removed when it
exits, also after a signal. A second runner refuses (exit 2) while the recorded pid is alive with
the recorded start time; a lock whose runner is gone (or whose pid now belongs to another
process) is stale and taken over.

Options:
  --codex BIN          the codex executable (default: codex)
  --max-parallel N     at most N codex processes at once (default: 4)
  --journal FILE       agent journal (default: <run_dir>/execute-journal.jsonl)
  --out FILE           the script's return value as JSON (default: <run_dir>/execute-result.json)
  --log-dir DIR        per-agent logs: <label with : replaced by _>.jsonl holds the codex event
                       stream, .log the relay output (default: <run_dir>/agents)
  --agent-timeout S    stop a codex attempt after S seconds (default: 1800)
  --idle-timeout S     stop a codex attempt that printed no event for S seconds (default: 1800);
                       an agent that runs a gate command prints nothing until the command ends,
                       so this must exceed the longest gate command that runs without output
  --relay-timeout S    stop a relay helper after S seconds (default: 1800)
  --kill-leftovers     kill process groups an earlier run left alive, then start

Exit status: 0 when the script finished (whatever the task statuses), 1 when it threw or the run
stopped on a configuration error, 2 on a usage error, a changed spec, a held runner lock or
leftover process groups,
128 plus the signal number when stopped by SIGINT, SIGTERM or SIGHUP (no result is written then).
`

const RELAY_KINDS = new Set(['gate', 'finish', 'scaffold'])
const KINDS = {
  impl: { sandbox: 'workspace-write', role: 'implementer' },
  rework: { sandbox: 'workspace-write', role: 'implementer' },
  review: { sandbox: 'read-only', role: 'reviewer' },
  confirm: { sandbox: 'read-only', role: 'reviewer' },
}
const ROLE_NAMES = { implementer: 'orchestrate-implementer', reviewer: 'orchestrate-reviewer' }
const ROLE_DIR = path.join(__dirname, '..', 'codex', 'agents')
const BUDGET = { total: null, spent: () => 0, remaining: () => Infinity }

class UsageError extends Error {}
class FatalError extends Error {}

// ---------------------------------------------------------------- command line

function parseArgs(argv) {
  const valued = {
    '--script': 'script', '--args': 'args', '--codex': 'codex', '--max-parallel': 'maxParallel',
    '--journal': 'journal', '--out': 'out', '--log-dir': 'logDir',
    '--agent-timeout': 'agentTimeout', '--idle-timeout': 'idleTimeout', '--relay-timeout': 'relayTimeout',
  }
  const opts = { codex: 'codex', maxParallel: '4', agentTimeout: '1800', idleTimeout: '1800', relayTimeout: '1800', killLeftovers: false }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '-h' || arg === '--help') {
      opts.help = true
      continue
    }
    if (arg === '--kill-leftovers') {
      opts.killLeftovers = true
      continue
    }
    if (!(arg in valued)) throw new UsageError(`unknown argument ${arg}`)
    if (i + 1 >= argv.length) throw new UsageError(`${arg} needs a value`)
    opts[valued[arg]] = argv[++i]
  }
  if (opts.help) return opts
  if (!opts.script || !opts.args) throw new UsageError('--script and --args are required')
  const n = Number(opts.maxParallel)
  if (!Number.isInteger(n) || n < 1) throw new UsageError(`--max-parallel must be a positive integer, got ${opts.maxParallel}`)
  opts.maxParallel = n
  for (const [name, flag] of [['agentTimeout', '--agent-timeout'], ['idleTimeout', '--idle-timeout'], ['relayTimeout', '--relay-timeout']]) {
    const seconds = Number(opts[name])
    if (!Number.isFinite(seconds) || seconds <= 0) throw new UsageError(`${flag} must be a positive number of seconds, got ${opts[name]}`)
    opts[name] = seconds
  }
  return opts
}

// ---------------------------------------------------------------- script loading (as tests/workflow_harness.js)

function loadScript(source) {
  const match = source.match(/^export const meta = (\{[\s\S]*?\n\})\n/)
  if (!match) throw new UsageError('the script must start with `export const meta = {...}`')
  // The meta block must be a pure literal: evaluate it with no scope at all.
  const meta = new Function(`"use strict"; return (${match[1]})`)()
  return { meta, body: source.slice(match[0].length) }
}

function guards() {
  const RealDate = Date
  function GuardedDate(...values) {
    if (!values.length) throw new Error('new Date() without arguments is not available in workflows')
    return new RealDate(...values)
  }
  GuardedDate.now = () => { throw new Error('Date.now() is not available in workflows') }
  GuardedDate.UTC = RealDate.UTC
  GuardedDate.parse = RealDate.parse
  const guardedMath = Object.create(Math)
  guardedMath.random = () => { throw new Error('Math.random() is not available in workflows') }
  return { GuardedDate, guardedMath }
}

// ---------------------------------------------------------------- schemas

// Codex structured output needs strict schemas: every object closed and every property required.
// Optional properties stay typed as before, so the model must always fill them.
function strictSchema(node) {
  if (!node || typeof node !== 'object' || Array.isArray(node)) return node
  const out = { ...node }
  if (node.properties && typeof node.properties === 'object') {
    out.properties = Object.fromEntries(Object.entries(node.properties).map(([key, sub]) => [key, strictSchema(sub)]))
  }
  if (node.items) out.items = strictSchema(node.items)
  const types = [].concat(node.type === undefined ? [] : node.type)
  if (types.includes('object') || out.properties) {
    out.properties = out.properties || {}
    out.required = Object.keys(out.properties)
    out.additionalProperties = false
  }
  return out
}

const TYPE_CHECKS = {
  null: v => v === null,
  boolean: v => typeof v === 'boolean',
  integer: v => typeof v === 'number' && Number.isInteger(v),
  number: v => typeof v === 'number' && Number.isFinite(v),
  string: v => typeof v === 'string',
  array: v => Array.isArray(v),
  object: v => v !== null && typeof v === 'object' && !Array.isArray(v),
}

function typeName(value) {
  return Object.keys(TYPE_CHECKS).find(name => TYPE_CHECKS[name](value)) || typeof value
}

function same(a, b) {
  return typeof a === typeof b && JSON.stringify(a) === JSON.stringify(b)
}

// The schema subset the result schemas use (type, properties, required, items, enum), as plan.py validate().
function validate(instance, schema, where = '$') {
  const errors = []
  if (schema.type !== undefined) {
    const names = [].concat(schema.type)
    if (!names.some(name => (TYPE_CHECKS[name] || (() => false))(instance))) {
      return [`${where}: expected ${names.join(' or ')}, got ${typeName(instance)}`]
    }
  }
  if (Array.isArray(schema.enum) && !schema.enum.some(option => same(instance, option))) {
    errors.push(`${where}: ${JSON.stringify(instance)} is not one of ${JSON.stringify(schema.enum)}`)
  }
  if (TYPE_CHECKS.object(instance)) {
    const own = key => Object.prototype.hasOwnProperty.call(instance, key)
    for (const key of schema.required || []) {
      if (!own(key)) errors.push(`${where}: missing required property '${key}'`)
    }
    for (const [key, sub] of Object.entries(schema.properties || {})) {
      if (own(key)) errors.push(...validate(instance[key], sub, `${where}.${key}`))
    }
  }
  if (Array.isArray(instance) && schema.items) {
    instance.forEach((item, index) => errors.push(...validate(item, schema.items, `${where}[${index}]`)))
  }
  return errors
}

function stripFence(text) {
  const trimmed = String(text).trim()
  const fenced = trimmed.match(/^```[A-Za-z]*\n([\s\S]*?)\n?```$/)
  return fenced ? fenced[1].trim() : trimmed
}

function parseResult(text, schema) {
  if (text === null || text === undefined) return { errors: ['no final message was written'] }
  if (!schema) return String(text).trim() ? { value: String(text).trim() } : { errors: ['the final message is empty'] }
  let value
  try {
    value = JSON.parse(stripFence(text))
  } catch (error) {
    return { errors: [`the final message is not valid JSON (${error.message})`] }
  }
  const errors = validate(value, schema)
  return errors.length ? { errors } : { value }
}

// ---------------------------------------------------------------- role files

function unescapeBasic(text, multiline) {
  const simple = { b: '\b', t: '\t', n: '\n', f: '\f', r: '\r', '"': '"', '\\': '\\' }
  let out = ''
  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (char !== '\\') {
      out += char
      continue
    }
    if (multiline) {
      const continuation = /^[ \t]*\r?\n/.exec(text.slice(i + 1))
      if (continuation) {
        let j = i + 1 + continuation[0].length
        while (j < text.length && /\s/.test(text[j])) j++
        i = j - 1
        continue
      }
    }
    const next = text[i + 1]
    if (next !== undefined && Object.prototype.hasOwnProperty.call(simple, next)) {
      out += simple[next]
      i++
      continue
    }
    if (next === 'u' || next === 'U') {
      const length = next === 'u' ? 4 : 8
      const hex = text.slice(i + 2, i + 2 + length)
      if (hex.length === length && /^[0-9A-Fa-f]+$/.test(hex)) {
        out += String.fromCodePoint(parseInt(hex, 16))
        i += 1 + length
        continue
      }
    }
    out += char
  }
  return out
}

// A minimal TOML string reader: the value of a top-level `key = <string>` in any of the four
// string forms (basic, literal, and their multi-line variants). Returns null when absent.
function tomlString(text, key) {
  const name = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`^[ \\t]*${name}[ \\t]*=[ \\t]*`, 'm').exec(text)
  if (!match) return null
  const rest = text.slice(match.index + match[0].length)
  if (rest.startsWith("'''")) {
    const end = rest.indexOf("'''", 3)
    return end < 0 ? null : rest.slice(3, end).replace(/^\r?\n/, '')
  }
  if (rest.startsWith('"""')) {
    let i = 3
    while (i < rest.length && !rest.startsWith('"""', i)) i += rest[i] === '\\' ? 2 : 1
    return i >= rest.length ? null : unescapeBasic(rest.slice(3, i).replace(/^\r?\n/, ''), true)
  }
  if (rest[0] === "'") {
    const end = rest.indexOf("'", 1)
    const value = end < 0 ? null : rest.slice(1, end)
    return value === null || value.includes('\n') ? null : value
  }
  if (rest[0] === '"') {
    let i = 1
    while (i < rest.length && rest[i] !== '"' && rest[i] !== '\n') i += rest[i] === '\\' ? 2 : 1
    return rest[i] === '"' ? unescapeBasic(rest.slice(1, i), false) : null
  }
  return null
}

// ---------------------------------------------------------------- codex and relay commands

// A linked worktree keeps its index and objects in the repository's common Git directory, outside
// the worktree. An implementer that stages its work needs that directory writable as well.
function gitCommonDir(cwd) {
  try {
    const out = childProcess.execFileSync('git', ['-C', cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
    return out && !path.resolve(out).startsWith(path.resolve(cwd) + path.sep) ? out : null
  } catch (error) {
    return null
  }
}

function codexArgs({ cwd, sandbox, model, effort, schemaFile, lastFile, prompt, addDirs = [] }) {
  return [
    'exec', '--skip-git-repo-check', '-C', cwd, '-s', sandbox, '-m', model,
    ...(effort ? ['-c', `model_reasoning_effort=${effort}`] : []),
    ...addDirs.flatMap(dir => ['--add-dir', dir]),
    ...(schemaFile ? ['--output-schema', schemaFile] : []),
    '-o', lastFile, '--json', prompt,
  ]
}

const shellQuote = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
// A plain word, or a single-quoted word as the workflow's q() writes it ('a'\''b').
const WORD = String.raw`(?:[A-Za-z0-9_./=:@%+,-]+|(?:'[^']*'|\\')+)`

// The helper command a relay prompt ends with, or why it is refused.
function relayCommand(prompt, kind, scripts) {
  if (!scripts || !scripts.python || !scripts.task) return { error: 'args.scripts names no python or task helper' }
  const lines = String(prompt).split('\n').map(line => line.replace(/\s+$/, '')).filter(line => line.trim())
  const line = lines.length ? lines[lines.length - 1] : ''
  const prefix = `${scripts.python} ${shellQuote(scripts.task)} `
  if (!line.startsWith(prefix)) return { error: `the last line does not start with ${prefix.trim()}` }
  if (!new RegExp(`^${kind}(?: +${WORD})*$`).test(line.slice(prefix.length))) {
    return { error: `the last line is not a plain \`${kind}\` helper call (only plain and single-quoted words are allowed)` }
  }
  return { command: line }
}

function retryNote(problem) {
  if (problem.stopped !== undefined) {
    return `The previous attempt was stopped (${problem.stopped}) before it returned a result. ` +
      'Continue from the current state of the worktree and return the structured result.'
  }
  if (problem.exit !== undefined) {
    return `The previous attempt ended with exit status ${problem.exit} before it returned a result. ` +
      'Continue from the current state of the worktree and return the structured result.'
  }
  return 'Your previous answer was rejected because its structured output did not match the output schema:\n' +
    problem.errors.map(e => `- ${e}`).join('\n') + '\nReturn exactly one JSON object that matches the output schema.'
}

// ---------------------------------------------------------------- runtime

const KILL_GRACE_MS = 5000

// Children are spawned detached, so each leads its own process group: signal the whole group,
// which also reaches the commands a codex agent or a gate helper started.
function killGroup(child, signal) {
  if (!child || !child.pid) return
  try {
    process.kill(-child.pid, signal)
  } catch (error) {
    try {
      child.kill(signal)
    } catch (ignored) {
      // already gone
    }
  }
}

function signalGroup(pgid, signal) {
  try {
    process.kill(-pgid, signal)
    return true
  } catch (error) {
    return false
  }
}

// A group is alive while one of its members has not exited: members that exited but were not
// reaped yet (zombies, state Z) do not count. When ps cannot answer, an existing group counts as alive.
function groupAlive(pgid) {
  try {
    process.kill(-pgid, 0)
  } catch (error) {
    if (error.code !== 'EPERM') return false
  }
  try {
    const out = childProcess.execFileSync('ps', ['-A', '-o', 'pgid=,stat='], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
    return out.split('\n').map(line => line.trim().split(/\s+/)).some(([group, state]) => Number(group) === pgid && state && !state.startsWith('Z'))
  } catch (error) {
    return true
  }
}

// The start time ps reports for a process, used to tell a recorded group leader from a later
// process that reused its pid. Empty when the process is gone.
function processStart(pid) {
  try {
    return childProcess.execFileSync('ps', ['-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch (error) {
    return ''
  }
}

function groupMembers(pgid) {
  try {
    const out = childProcess.execFileSync('ps', ['-A', '-o', 'pid=,pgid=,command='], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
    return out.split('\n').map(line => line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/))
      .filter(m => m && Number(m[2]) === pgid).map(m => `${m[1]} ${m[3]}`)
  } catch (error) {
    return []
  }
}

async function sleep(ms) {
  await new Promise(resolve => setTimeout(resolve, ms))
}

// SIGTERM every group, wait up to KILL_GRACE_MS for them to end, SIGKILL the rest; returns the
// groups still alive afterwards.
async function stopGroups(pgids) {
  pgids.forEach(pgid => signalGroup(pgid, 'SIGTERM'))
  for (let waited = 0; waited < KILL_GRACE_MS && pgids.some(groupAlive); waited += 100) await sleep(100)
  pgids.filter(groupAlive).forEach(pgid => signalGroup(pgid, 'SIGKILL'))
  for (let waited = 0; waited < 2000 && pgids.some(groupAlive); waited += 100) await sleep(100)
  return pgids.filter(groupAlive)
}

// The registry of running child groups: rewritten whenever a child starts or ends.
function openRegistry(file) {
  const entries = new Map()
  const save = () => {
    fs.mkdirSync(path.dirname(file), { recursive: true })
    const tmp = `${file}.${process.pid}.tmp`
    fs.writeFileSync(tmp, JSON.stringify([...entries.values()], null, 2) + '\n')
    fs.renameSync(tmp, file)
  }
  save()
  return {
    add(pgid, label) {
      entries.set(pgid, { pgid, label, runner: process.pid, started: processStart(pgid) })
      save()
    },
    remove(pgid) {
      if (entries.delete(pgid)) save()
    },
    pgids: () => [...entries.keys()],
  }
}

function readRegistry(file) {
  try {
    const entries = JSON.parse(fs.readFileSync(file, 'utf8'))
    return Array.isArray(entries) ? entries.filter(e => e && Number.isInteger(e.pgid) && e.pgid > 1 && e.pgid !== process.pid) : []
  } catch (error) {
    return []
  }
}

// Recorded groups that still run: the group exists and, when its leader is alive, the leader is
// the process that was recorded (not a later process with a reused pid).
function liveLeftovers(entries) {
  return entries.filter(entry => {
    if (!groupAlive(entry.pgid)) return false
    const start = processStart(entry.pgid)
    return !(start && entry.started && start !== entry.started)
  })
}

// Specs edited after plan.py workflow-args ran: the implementer would work from a spec that the
// arguments (and the journal keys) do not describe.
function specProblems(tasks) {
  const problems = []
  for (const t of tasks || []) {
    if (!t || t.done) continue
    let digest
    try {
      digest = crypto.createHash('sha256').update(fs.readFileSync(t.spec)).digest('hex')
    } catch (error) {
      problems.push(`task ${t.id}: cannot read its spec ${t.spec}`)
      continue
    }
    if (digest !== t.spec_sha256) {
      problems.push(`task ${t.id}: the spec ${t.spec} has sha256 ${digest}, the arguments record ${t.spec_sha256 || 'none'}`)
    }
  }
  return problems
}

// plan.py worktree_id(): the hex sha256 of the absolute path (UTF-8), NUL, the decimal st_ino of
// <worktree>/.git, NUL, and its decimal st_ctime_ns. A recreated worktree gets a new .git file.
function worktreeId(worktree) {
  const absolute = path.resolve(worktree)
  const st = fs.statSync(path.join(absolute, '.git'), { bigint: true })
  const nul = Buffer.from([0])
  const data = Buffer.concat([Buffer.from(absolute, 'utf8'), nul, Buffer.from(String(st.ino), 'ascii'), nul, Buffer.from(String(st.ctimeNs), 'ascii')])
  return crypto.createHash('sha256').update(data).digest('hex')
}

function worktreeProblems(tasks) {
  const problems = []
  for (const t of tasks || []) {
    if (!t || t.done) continue
    let id
    try {
      id = worktreeId(t.worktree)
    } catch (error) {
      problems.push(`task ${t.id}: the worktree ${t.worktree} is missing (no .git)`)
      continue
    }
    if (id !== t.worktree_id) {
      problems.push(`task ${t.id}: the worktree ${t.worktree} has id ${id}, the arguments record ${t.worktree_id || 'none'}`)
    }
  }
  return problems
}

function processAlive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error.code === 'EPERM'
  }
}

// One runner per run directory. The lock holds {pid, started}; it is live while that pid runs
// with that start time (any live pid when no start time could be recorded). Returns
// {lock, previous} on success (previous: the stale holder taken over, if any) or {holder}.
function acquireLock(file) {
  const mine = { pid: process.pid, started: processStart(process.pid) }
  const text = JSON.stringify(mine) + '\n'
  fs.mkdirSync(path.dirname(file), { recursive: true })
  let previous = null
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      fs.writeFileSync(file, text, { flag: 'wx' })
      return { lock: mine, previous }
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
    }
    let seen
    try {
      seen = fs.readFileSync(file, 'utf8')
    } catch (error) {
      if (error.code === 'ENOENT') continue
      throw error
    }
    let holder = null
    try {
      holder = JSON.parse(seen)
    } catch (error) {
      holder = null
    }
    const valid = holder && Number.isInteger(holder.pid) && holder.pid > 0
    if (valid && processAlive(holder.pid) && (!holder.started || processStart(holder.pid) === holder.started)) return { holder }
    // Stale: remove exactly the lock that was read, then compete for a fresh one.
    try {
      if (fs.readFileSync(file, 'utf8') === seen) fs.unlinkSync(file)
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
    }
    previous = holder || { unreadable: seen.slice(0, 200) }
  }
  return { holder: null }
}

function releaseLock(file) {
  try {
    const holder = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (holder && holder.pid === process.pid) fs.unlinkSync(file)
  } catch (error) {
    // gone already, or not ours
  }
}

function limiter(max) {
  let active = 0
  const queue = []
  return async fn => {
    if (active < max) active++
    else await new Promise(resolve => queue.push(resolve)) // the releasing caller hands over its slot
    try {
      return await fn()
    } finally {
      const next = queue.shift()
      if (next) next()
      else active--
    }
  }
}

function openJournal(file, log) {
  const results = new Map()
  let started = 0
  let text = ''
  try {
    text = fs.readFileSync(file, 'utf8')
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  text.split('\n').forEach((line, index) => {
    if (!line.trim()) return
    let entry
    try {
      entry = JSON.parse(line)
    } catch (error) {
      log(`journal line ${index + 1} is not JSON; ignored`)
      return
    }
    if (entry.type === 'started') started++
    else if (entry.type === 'result' && typeof entry.key === 'string') results.set(entry.key, entry.result)
  })
  fs.mkdirSync(path.dirname(file), { recursive: true })
  if (text && !text.endsWith('\n')) fs.appendFileSync(file, '\n') // an interrupted write left a partial line
  return { results, started, append: entry => fs.appendFileSync(file, JSON.stringify(entry) + '\n') }
}

function createRuntime({ args: A, codex, maxParallel, journalPath, logDir, agentTimeout, idleTimeout, relayTimeout,
  registryPath, write = text => process.stderr.write(text) }) {
  let stopping = false
  let fatalError = null
  const fatal = message => {
    const error = new FatalError(message)
    if (!fatalError) fatalError = error
    throw error
  }
  const log = message => write(`[${new Date().toISOString()}] ${message}\n`)
  const journal = openJournal(journalPath, log)
  let counter = journal.started
  const slot = limiter(maxParallel)
  const children = new Set()
  const roles = {}
  fs.mkdirSync(logDir, { recursive: true })
  const registry = openRegistry(registryPath)
  const logFile = (label, ext) => path.join(logDir, `${label.replace(/:/g, '_')}${ext}`)

  function roleInstructions(role) {
    if (!(role in roles)) {
      const file = path.join(ROLE_DIR, `${ROLE_NAMES[role]}.toml`)
      let text
      try {
        text = fs.readFileSync(file, 'utf8')
      } catch (error) {
        fatal(`cannot read the role file ${file}: ${error.message}`)
      }
      const value = tomlString(text, 'developer_instructions')
      if (!value || !value.trim()) fatal(`${file} has no developer_instructions string`)
      roles[role] = value.trim()
    }
    return roles[role]
  }

  function withRole(role, prompt) {
    const inline = A.role_text && A.role_text[role]
    return inline && String(inline).trim() ? prompt : `${roleInstructions(role)}\n\n${prompt}`
  }

  function tierThree() {
    const tiers = A.routing && A.routing.codex && A.routing.codex.tiers
    const entry = tiers && tiers['3']
    if (!entry || !entry.model) fatal('an agent call names no model and args.routing.codex.tiers["3"] is missing')
    return entry
  }

  // Run one child in its own process group. `timeout` bounds the whole run and `idle` the time
  // without stdout output (seconds; absent means no limit). A child over a limit has its group
  // stopped (SIGTERM, then SIGKILL after KILL_GRACE_MS) and the result carries `stopped`.
  function spawnProcess(bin, argv, { label, cwd, stdoutFile, timeout, idle }) {
    return new Promise(resolve => {
      let child = null
      const chunks = []
      let stderr = ''
      let settled = false
      let stopped
      let exitCode = null
      let idleTimer = null
      const timers = new Set()
      const later = (ms, fn) => {
        const timer = setTimeout(() => { timers.delete(timer); fn() }, ms)
        timers.add(timer)
        return timer
      }
      const fd = stdoutFile ? fs.openSync(stdoutFile, 'a') : null
      const finish = (code, extra = '') => {
        if (settled) return
        settled = true
        for (const timer of timers) clearTimeout(timer)
        if (fd !== null) fs.closeSync(fd)
        children.delete(child)
        if (child && child.pid) registry.remove(child.pid)
        resolve({ code, stdout: Buffer.concat(chunks).toString('utf8'), stderr: stderr + extra, stopped })
      }
      const stop = reason => {
        if (stopped || settled) return
        stopped = reason
        log(`${label}: ${reason}; stopping its process group (SIGTERM)`)
        killGroup(child, 'SIGTERM')
        later(KILL_GRACE_MS, () => {
          log(`${label}: still running ${KILL_GRACE_MS / 1000}s after SIGTERM; sending SIGKILL to its process group`)
          killGroup(child, 'SIGKILL')
          // A process that left the group may still hold the pipes: do not wait for it forever.
          later(KILL_GRACE_MS, () => {
            child.stdout.destroy()
            child.stderr.destroy()
            finish(exitCode === null ? 128 + os.constants.signals.SIGKILL : exitCode)
          })
        })
      }
      const touch = () => {
        if (!idle || stopped) return
        if (idleTimer) {
          clearTimeout(idleTimer)
          timers.delete(idleTimer)
        }
        idleTimer = later(idle * 1000, () => stop(`no output for ${idle}s (idle timeout)`))
      }
      if (stopping) {
        stopped = 'the runner is stopping'
        finish(-1)
        return
      }
      try {
        child = spawn(bin, argv, { cwd, stdio: ['ignore', 'pipe', 'pipe'], detached: true })
      } catch (error) {
        finish(-1, error.message)
        return
      }
      children.add(child)
      if (child.pid) registry.add(child.pid, label)
      if (timeout) later(timeout * 1000, () => stop(`still running after ${timeout}s (timeout)`))
      touch()
      child.stdout.on('data', data => {
        if (fd !== null) fs.writeSync(fd, data)
        else chunks.push(data)
        touch()
      })
      child.stderr.setEncoding('utf8')
      child.stderr.on('data', data => { stderr = (stderr + data).slice(-20000) })
      const status = (code, signal) => (code === null ? 128 + (os.constants.signals[signal] || 0) : code)
      child.on('exit', (code, signal) => { exitCode = status(code, signal) })
      child.on('error', error => finish(-1, `\n${error.message}`))
      child.on('close', (code, signal) => finish(status(code, signal)))
    })
  }

  const tail = text => String(text).trim().split('\n').slice(-5).join(' / ')

  async function runCodex({ label, cwd, sandbox, model, effort, schema, prompt }) {
    if (!fs.existsSync(cwd)) {
      log(`${label}: the worktree ${cwd} does not exist`)
      return null
    }
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'orchestrate-agent-'))
    try {
      const schemaFile = schema ? path.join(tmp, 'schema.json') : null
      if (schemaFile) fs.writeFileSync(schemaFile, JSON.stringify(strictSchema(schema), null, 2))
      const lastFile = path.join(tmp, 'last-message.txt')
      let problem = null
      for (let attempt = 1; attempt <= 2; attempt++) {
        fs.rmSync(lastFile, { force: true })
        const text = problem ? `${prompt}\n\n${retryNote(problem)}` : prompt
        const common = sandbox === 'workspace-write' ? gitCommonDir(cwd) : null
        const argv = codexArgs({ cwd, sandbox, model, effort, schemaFile, lastFile, prompt: text, addDirs: common ? [common] : [] })
        const run = await slot(() => {
          log(`${label}: codex attempt ${attempt} (${model}${effort ? ` ${effort}` : ''}, ${sandbox})`)
          return spawnProcess(codex, argv, { label, cwd, stdoutFile: logFile(label, '.jsonl'), timeout: agentTimeout, idle: idleTimeout })
        })
        if (stopping) return null
        if (run.stopped) {
          problem = { stopped: run.stopped }
          log(`${label}: codex attempt ${attempt} was stopped: ${run.stopped}`)
          continue
        }
        if (run.code !== 0) {
          problem = { exit: run.code }
          log(`${label}: codex exited with status ${run.code}${run.stderr.trim() ? `: ${tail(run.stderr)}` : ''}`)
          continue
        }
        const outcome = parseResult(fs.existsSync(lastFile) ? fs.readFileSync(lastFile, 'utf8') : null, schema)
        if (outcome.errors) {
          problem = { errors: outcome.errors }
          log(`${label}: invalid structured output: ${outcome.errors.join('; ')}`)
          continue
        }
        return outcome.value
      }
      log(`${label}: no valid result after the retry; the agent failed`)
      return null
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true })
    }
  }

  async function runRelay({ label, kind, prompt, cwd, schema }) {
    const check = relayCommand(prompt, kind, A.scripts)
    if (check.error) {
      log(`${label}: relay refused: ${check.error}`)
      return null
    }
    if (!fs.existsSync(cwd)) {
      log(`${label}: the worktree ${cwd} does not exist`)
      return null
    }
    log(`${label}: running ${check.command}`)
    const run = await spawnProcess('bash', ['-c', check.command], { label, cwd, timeout: relayTimeout })
    fs.writeFileSync(logFile(label, '.log'), `$ ${check.command}\nexit ${run.code}${run.stopped ? ` (stopped: ${run.stopped})` : ''}\n` +
      `--- stdout\n${run.stdout}\n--- stderr\n${run.stderr}\n`)
    if (run.stopped) {
      log(`${label}: the helper was stopped: ${run.stopped}`)
      return null
    }
    const outcome = parseResult(run.stdout, schema)
    if (outcome.errors) {
      log(`${label}: the helper printed no valid result (exit ${run.code}): ${outcome.errors.join('; ')}`)
      return null
    }
    return outcome.value
  }

  async function agent(prompt, opts = {}) {
    const label = opts && opts.label
    if (typeof label !== 'string' || !label) fatal('every agent() call needs a label')
    const [kind, id] = label.split(':')
    const relay = RELAY_KINDS.has(kind)
    const mapping = KINDS[kind]
    if (!relay && !mapping) fatal(`agent ${label}: unknown label kind '${kind}'`)
    const task = (A.tasks || []).find(t => t && t.id === id)
    if (!task) fatal(`agent ${label}: no task '${id}' in args.tasks`)
    let model = opts.model || null
    let effort = opts.effort || null
    if (!relay && !model) {
      const fallback = tierThree()
      model = fallback.model
      effort = effort || fallback.effort || null
    }
    const sandbox = relay ? 'local' : mapping.sandbox
    const text = String(prompt)
    const key = crypto.createHash('sha256').update(JSON.stringify([label, text, model, effort, sandbox])).digest('hex')
    // Only codex agents replay: a relay reads the worktree as it is now, so it always runs again.
    if (!relay && journal.results.has(key)) {
      log(`${label}: resumed from the journal`)
      return JSON.parse(JSON.stringify(journal.results.get(key)))
    }
    const codexPrompt = relay ? null : withRole(mapping.role, text)
    const agentId = `${relay ? 'local' : 'agent'}-${++counter}`
    journal.append({ type: 'started', key, agentId, label, phase: opts.phase || null })
    let result = null
    try {
      result = relay
        ? await runRelay({ label, kind, prompt: text, cwd: task.worktree, schema: opts.schema })
        : await runCodex({ label, cwd: task.worktree, sandbox, model, effort, schema: opts.schema, prompt: codexPrompt })
    } catch (error) {
      log(`${label}: ${error.message}`)
      result = null
    }
    if (result === null || result === undefined) {
      journal.append({ type: 'failed', key, agentId })
      return null
    }
    journal.append({ type: 'result', key, agentId, result })
    return result
  }

  async function parallel(thunks) {
    return Promise.all(thunks.map(thunk => Promise.resolve().then(thunk).catch(() => null)))
  }

  async function pipeline(items, ...stages) {
    return Promise.all(items.map(async (item, index) => {
      let value = item
      try {
        for (const stage of stages) value = await stage(value, item, index)
        return value
      } catch (error) {
        return null
      }
    }))
  }

  function phase(title) {
    log(`phase: ${title}`)
  }

  // Stop for good: no new child starts, and every running group is stopped.
  async function stopAll() {
    stopping = true
    const survivors = await stopGroups(registry.pgids())
    // Let the stopped children close (their agents then return null and journal a failure), but
    // do not wait for a process that left its group and keeps a pipe open.
    for (let waited = 0; waited < KILL_GRACE_MS && children.size; waited += 50) await sleep(50)
    registry.pgids().filter(pgid => !survivors.includes(pgid)).forEach(pgid => registry.remove(pgid))
    return survivors
  }

  return { agent, parallel, pipeline, phase, log, children, stopAll, fatalError: () => fatalError }
}

// ---------------------------------------------------------------- main

async function main(argv) {
  let opts
  try {
    opts = parseArgs(argv)
  } catch (error) {
    process.stderr.write(`run_workflow: ${error.message}\n\n${USAGE}`)
    return 2
  }
  if (opts.help) {
    process.stdout.write(USAGE)
    return 0
  }
  let A
  let script
  let runDir = null
  try {
    A = JSON.parse(fs.readFileSync(opts.args, 'utf8'))
    script = loadScript(fs.readFileSync(opts.script, 'utf8'))
    if (!A || typeof A !== 'object' || Array.isArray(A)) throw new UsageError('the workflow arguments must be a JSON object')
    if (A.harness !== undefined && A.harness !== 'codex') {
      throw new UsageError(`the workflow arguments are for harness ${JSON.stringify(A.harness)}; this runtime runs codex arguments`)
    }
    runDir = typeof A.run_dir === 'string' && A.run_dir ? A.run_dir : null
    for (const [name, value] of [['journal', opts.journal], ['out', opts.out], ['log-dir', opts.logDir]]) {
      if (!value && !runDir) throw new UsageError(`args.run_dir is missing, so --${name} is required`)
    }
    opts.journal = path.resolve(opts.journal || path.join(runDir, 'execute-journal.jsonl'))
    opts.out = path.resolve(opts.out || path.join(runDir, 'execute-result.json'))
    opts.logDir = path.resolve(opts.logDir || path.join(runDir, 'agents'))
  } catch (error) {
    process.stderr.write(`run_workflow: ${error.message}\n`)
    return 2
  }

  const stale = specProblems(A.tasks)
  if (stale.length) {
    process.stderr.write(`run_workflow: a spec changed after the arguments were generated:\n${stale.map(p => `  ${p}`).join('\n')}\n` +
      'Regenerate the arguments with plan.py workflow-args (the same PLAN and options), then run again.\n')
    return 2
  }
  const replaced = worktreeProblems(A.tasks)
  if (replaced.length) {
    process.stderr.write(`run_workflow: a worktree is not the one the arguments were generated for:\n` +
      `${replaced.map(p => `  ${p}`).join('\n')}\nAfter recreating a worktree, regenerate the arguments with plan.py ` +
      'workflow-args (the same PLAN and options), then run again.\n')
    return 2
  }

  const stateDir = runDir ? path.resolve(runDir, 'agents') : opts.logDir
  const lockPath = path.join(stateDir, 'runner.lock')
  const locked = acquireLock(lockPath)
  if (!locked.lock) {
    const who = locked.holder ? `pid ${locked.holder.pid}, started ${locked.holder.started || 'at an unknown time'}` : 'unknown'
    process.stderr.write(`run_workflow: another runner holds ${lockPath} (${who}) and is still running; ` +
      'wait for it to finish or stop it, then run again.\n')
    return 2
  }
  process.on('exit', () => releaseLock(lockPath))
  if (locked.previous) {
    process.stderr.write(`run_workflow: taking over a stale runner lock (${JSON.stringify(locked.previous)})\n`)
  }

  const registryPath = path.join(stateDir, 'process-groups.json')
  const leftovers = liveLeftovers(readRegistry(registryPath))
  if (leftovers.length) {
    const list = leftovers.map(e => `  process group ${e.pgid} (${e.label || 'unknown agent'}, runner ${e.runner || '?'}): ` +
      `${groupMembers(e.pgid).join('; ') || 'members unknown'}`).join('\n')
    if (!opts.killLeftovers) {
      process.stderr.write(`run_workflow: process groups recorded in ${registryPath} are still alive:\n${list}\n` +
        'Another run may still be working. Stop them, or rerun with --kill-leftovers to kill them first.\n')
      return 2
    }
    process.stderr.write(`run_workflow: killing leftover process groups:\n${list}\n`)
    const survivors = await stopGroups(leftovers.map(e => e.pgid))
    if (survivors.length) {
      process.stderr.write(`run_workflow: process groups ${survivors.join(', ')} survived SIGKILL; not starting\n`)
      return 2
    }
  }

  const runtime = createRuntime({
    args: A, codex: opts.codex, maxParallel: opts.maxParallel, journalPath: opts.journal, logDir: opts.logDir,
    agentTimeout: opts.agentTimeout, idleTimeout: opts.idleTimeout, relayTimeout: opts.relayTimeout, registryPath,
  })
  let stoppingBy = null
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
    process.on(signal, () => {
      const code = 128 + os.constants.signals[signal]
      if (stoppingBy) {
        for (const child of runtime.children) killGroup(child, 'SIGKILL')
        process.exit(code)
      }
      stoppingBy = signal
      process.stderr.write(`run_workflow: stopped by ${signal}; stopping every child process group\n`)
      runtime.stopAll().then(survivors => {
        if (survivors.length) process.stderr.write(`run_workflow: process groups ${survivors.join(', ')} are still alive\n`)
        process.stderr.write(`run_workflow: rerun the same command to resume from ${opts.journal}\n`)
        process.exit(code)
      })
    })
  }
  const { GuardedDate, guardedMath } = guards()
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  let result
  try {
    const run = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'Date', 'Math', script.body)
    result = await run(runtime.agent, runtime.parallel, runtime.pipeline, runtime.phase, runtime.log, A, BUDGET, GuardedDate, guardedMath)
  } catch (error) {
    if (stoppingBy) return new Promise(() => {}) // the signal handler exits once the children are gone
    process.stderr.write(`run_workflow: the script threw: ${(error && error.stack) || error}\n`)
    return 1
  }
  // A stopped run writes no result: the agents it cut short only look failed.
  if (stoppingBy) return new Promise(() => {})
  if (runtime.fatalError()) {
    process.stderr.write(`run_workflow: stopped on a configuration error: ${runtime.fatalError().message}\n`)
    return 1
  }
  const text = JSON.stringify(result === undefined ? null : result, null, 2) + '\n'
  fs.mkdirSync(path.dirname(opts.out), { recursive: true })
  fs.writeFileSync(opts.out, text)
  process.stdout.write(text)
  return 0
}

module.exports = {
  parseArgs, loadScript, strictSchema, validate, parseResult, tomlString, codexArgs, relayCommand, limiter, gitCommonDir,
  specProblems, worktreeId, worktreeProblems, readRegistry, liveLeftovers, acquireLock, releaseLock,
}

if (require.main === module) {
  main(process.argv.slice(2)).then(
    code => { process.exitCode = code },
    error => {
      process.stderr.write(`run_workflow: ${(error && error.stack) || error}\n`)
      process.exitCode = 1
    })
}
