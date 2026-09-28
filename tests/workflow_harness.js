// Run a workflow script outside Claude Code with scripted agents, for tests.
//
//   node workflow_harness.js <script.js> <scenario.json>
//
// The scenario holds `args` (the workflow's args global) and `responses`: a map from an agent
// label to a list of results returned in order (null means the agent failed). Labels without a
// scripted response get a default by their prefix: impl/rework -> a report, gate -> a pass,
// review -> PASS, confirm -> everything confirmed, scaffold/finish -> success; authority steps:
// prepare/dispatch/return -> success, collect -> accepted, review-open -> a token per --lens,
// review-close -> FAIL when a review of the same target and round failed, else PASS. Prints one JSON
// object: {result, calls, logs, phases}. Date.now, Math.random and new Date() throw, as in the
// real runtime, so a script that depends on them fails here too.
'use strict'
const fs = require('fs')

const [, , scriptPath, scenarioPath] = process.argv
const scenario = JSON.parse(fs.readFileSync(scenarioPath, 'utf8'))
const source = fs.readFileSync(scriptPath, 'utf8')

const metaMatch = source.match(/^export const meta = (\{[\s\S]*?\n\})\n/)
if (!metaMatch || source.indexOf('export const meta') !== 0) {
  throw new Error('the script must start with `export const meta = {...}`')
}
// The meta block must be a pure literal: evaluate it with no scope at all.
const meta = new Function(`"use strict"; return (${metaMatch[1]})`)()
const body = source.slice(metaMatch[0].length)

const calls = []
const logs = []
const phases = []
const queues = {}
for (const [label, values] of Object.entries(scenario.responses || {})) queues[label] = [...values]
const trees = {}

const results = []

function defaultResult(label, opts, prompt) {
  const [kind, id] = label.split(':')
  const tree = trees[id] || (trees[id] = `tree-${id}`)
  if (kind === 'impl' || kind === 'rework') {
    return { files_changed: [{ path: 'a.txt', change: 'edited' }], gate: [], tests: [], not_done: [], questions: [] }
  }
  if (kind === 'gate') {
    return { attempt_dir: `/run/gates/${label}`, exit_code: 0, results: [{ command: 'check', rc: 0, outcome: 'ok', log: '1.log' }], tail: 'ok', tree, dirty: false, unstaged: [], untracked: [], outside_scope: [], head: `head-${id}` }
  }
  if (kind === 'review') return { verdict: 'PASS', defects: [], notes: [], gate: [] }
  if (kind === 'confirm') return { defects: [] }
  if (kind === 'scaffold') return { exit_code: 0, output: `head head-${id}`, head: `head-${id}` }
  if (kind === 'prepare') return { exit_code: 0, output: 'prepared', worktree: `/wt/gov-${id}`, branch: `gov/${id}`, head: `head-${id}`, spec_sha256: `spec-${id}` }
  if (kind === 'dispatch') return { exit_code: 0, output: 'registered', dispatch_id: `dispatch-${label.split(':').slice(1).join('-')}` }
  if (kind === 'collect') return { exit_code: 0, output: 'imported', accepted: true }
  if (kind === 'return') return { exit_code: 0, output: 'rework recorded' }
  if (kind === 'review-open') {
    const lenses = [...prompt.matchAll(/--lens '([^']+)'/g)].map(m => m[1])
    return { exit_code: 0, output: 'issued', tokens: Object.fromEntries(lenses.map(l => [l, `token-${id}-${l}`])) }
  }
  if (kind === 'review-close') {
    const prefix = `review:${label.split(':').slice(1).join(':')}:`
    const failed = results.some(r => r.label.startsWith(prefix) && r.value && r.value.verdict === 'FAIL')
    return { exit_code: 0, output: 'imported', verdict: failed ? 'FAIL' : 'PASS' }
  }
  if (kind === 'finish') {
    return { patch: `/run/patches/task-${id}.patch`, sha256: `sha-${id}`, files: ['a.txt'], tree, verify_clean_exit: 0, output: 'clean' }
  }
  throw new Error(`no default result for label ${label}`)
}

async function agent(prompt, opts = {}) {
  if (!opts.label) throw new Error('every agent needs a label')
  if (calls.some(c => c.label === opts.label)) throw new Error(`duplicate agent label ${opts.label}`)
  calls.push({ label: opts.label, phase: opts.phase, model: opts.model, effort: opts.effort, agentType: opts.agentType, schema: Boolean(opts.schema), prompt })
  await new Promise(resolve => setImmediate(resolve))
  let value
  if (queues[opts.label] && queues[opts.label].length) {
    value = queues[opts.label].shift()
    if (value === '__throw__') throw new Error(`scripted failure of ${opts.label}`)
  } else {
    value = defaultResult(opts.label, opts, prompt)
  }
  results.push({ label: opts.label, value })
  return value
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

function phase(title) { phases.push(title) }
function log(message) { logs.push(String(message)) }

const RealDate = Date
function GuardedDate(...values) {
  if (!values.length) throw new Error('new Date() without arguments is not available in workflows')
  return new RealDate(...values)
}
GuardedDate.now = () => { throw new Error('Date.now() is not available in workflows') }
const guardedMath = Object.create(Math)
guardedMath.random = () => { throw new Error('Math.random() is not available in workflows') }

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const run = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'Date', 'Math', body)
const budget = { total: null, spent: () => 0, remaining: () => Infinity }

run(agent, parallel, pipeline, phase, log, scenario.args, budget, GuardedDate, guardedMath)
  .then(result => {
    const titles = new Set((meta.phases || []).map(p => p.title))
    const unknown = [...new Set(calls.map(c => c.phase).filter(p => p && !titles.has(p)))]
    process.stdout.write(JSON.stringify({ result, calls, logs, phases, meta, unknownPhases: unknown }))
  })
  .catch(error => {
    process.stdout.write(JSON.stringify({ error: String(error && error.stack || error), calls, logs }))
    process.exitCode = 1
  })
