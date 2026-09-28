export const meta = {
  name: 'orchestrate-execute',
  description: 'Implement, gate, review and rework each planned task in its own worktree, then export the reviewed patches',
  whenToUse: 'Phases 2 to 4 of the orchestrate skill; launch with the JSON printed by plan.py workflow-args as args',
  phases: [
    { title: 'Implement', detail: 'one implementer per task, a fresh agent per rework round, one tier up on escalation' },
    { title: 'Gate', detail: 'the spec gate through task.py, run by a utility agent that relays its JSON' },
    { title: 'Review', detail: 'conformance review, plus an adversary lens for risky tasks and a tie-break on disagreement' },
    { title: 'Finish', detail: 'dependency scaffolding, patch export and the verify-clean check' },
  ],
}

// Control flow lives here, not in the orchestrator's reasoning: every task runs the same
// implement -> gate -> review -> rework loop, bounded by args.limits.rework_rounds per tier,
// escalating one tier up when the rounds run out or a rework makes no progress.
// The script cannot touch files; agents run the helpers and return schema-checked JSON.

const A = args
if (!A || typeof A !== 'object' || !Array.isArray(A.tasks) || !A.schemas || !A.scripts) {
  throw new Error('args must be the JSON object printed by plan.py workflow-args (pass the object itself, not a file path or text)')
}
const ROUNDS = A.limits && A.limits.rework_rounds
if (!Number.isInteger(ROUNDS) || ROUNDS < 0 || ROUNDS > 5) {
  throw new Error('args.limits.rework_rounds must be an integer from 0 to 5')
}
const SEVERITY = { blocker: 3, major: 2, minor: 1 }

const q = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
const helper = (sub, parts) => [A.scripts.python, q(A.scripts.task), sub, ...parts].join(' ')
const list = xs => xs.map(x => `- ${x}`).join('\n')
const role = key => (A.agent_types[key] ? '' : `${A.role_text[key]}\n\n`)
const typeOf = key => (A.agent_types[key] ? { agentType: A.agent_types[key] } : {})
const pick = step => (step.effort ? { model: step.model, effort: step.effort } : { model: step.model })
const inScope = (t, path) => t.scope.some(s => path === s || path.startsWith(`${s}/`))
const names = paths => `${paths.slice(0, 10).join(', ')}${paths.length > 10 ? `, and ${paths.length - 10} more` : ''}`

// Durations and clock times differ between runs of the same failure; counts do not.
const volatile = text => String(text)
  .replace(/\b\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\b/g, '#time')
  .replace(/\d+(\.\d+)?\s*(ms|msec|s|sec|secs|seconds|m|min|minutes)\b/gi, '#duration')

// A red gate is identified by its command and its whole output with durations and clock times
// removed, so a rework that fixes some failures (a smaller count, fewer failure lines) produces a
// different fingerprint; review defects are identified by file and kind.
function fingerprint(defect) {
  const output = defect.kind === 'build' ? volatile(defect.scenario || '') : ''
  return `${String(defect.file || '').trim().toLowerCase()}|${defect.kind}|${output}`
}

// A rework made no progress when it introduced a regression, or when nothing got fewer:
// every defect it left was already there and there are at least as many as before.
// Then the next attempt goes one tier up instead of spending another round at this tier.
function nonConverging(previous, current) {
  if (!previous) return false
  if (current.some(d => d.kind === 'regression')) return true
  const before = new Set(previous.map(fingerprint))
  return current.length >= previous.length && current.every(d => before.has(fingerprint(d)))
}

// Changes outside the task's scope that the gate or the build left behind are an environment
// problem: no rework round can fix them, so the task stops with a precise reason.
function environmentProblem(t, gate) {
  const untracked = gate.untracked.filter(path => !inScope(t, path))
  const changed = gate.unstaged.filter(path => !inScope(t, path))
  if (!untracked.length && !changed.length) return ''
  const parts = []
  const advice = []
  if (untracked.length) {
    parts.push(`untracked files outside the scope (${names(untracked)})`)
    advice.push('for untracked files the gate or build writes, ignore them in .git/info/exclude or .gitignore; remove stray ones')
  }
  if (changed.length) {
    parts.push(`tracked files outside the scope changed and not staged (${names(changed)})`)
    advice.push('ignore rules do not apply to tracked files: stop the gate rewriting them (for example a frozen lock file), ' +
      'or ask the user to commit the refreshed file on the base and recreate the worktrees, or run ' +
      '`git update-index --skip-worktree <path>` in each task worktree; restore them if nothing should have changed them')
  }
  return `environment: after the gate the worktree has ${parts.join(' and ')}. ${advice.join('; ')}. Then run this task again.`
}

function gateDefects(t, gate, expectedHead) {
  if (expectedHead && gate.head && gate.head !== expectedHead) {
    // A commit inside the task worktree would leave part of the work out of the exported patch.
    return [{ file: '(worktree HEAD)', kind: 'scope', severity: 'blocker',
      summary: `HEAD moved from ${expectedHead} to ${gate.head}: the work was committed`,
      scenario: `Never commit. Run \`git -C ${q(t.worktree)} reset --soft ${expectedHead}\` so every change is staged again, then stage exactly the scoped files.` }]
  }
  if (gate.exit_code !== 0) {
    const failed = gate.results.filter(r => r.rc !== 0)
    const rows = failed.length ? failed : [{ command: 'gate', outcome: `exit ${gate.exit_code}` }]
    return rows.map(r => ({
      file: `(gate) ${r.command}`, kind: 'build', severity: 'blocker',
      summary: `gate command ${r.outcome}`, scenario: gate.tail,
    }))
  }
  const defects = []
  if (gate.outside_scope.length) {
    defects.push({ file: names(gate.outside_scope), kind: 'scope', severity: 'major',
      summary: 'staged files outside the task scope',
      scenario: `Scope: ${t.scope.join(', ')}. Unstage every other path, delete new scratch files and restore tracked files ` +
        'you did not mean to change; report anything the spec missed instead of widening the scope.' })
  }
  const pending = [...gate.unstaged, ...gate.untracked].filter(path => inScope(t, path))
  if (pending.length) {
    defects.push({ file: names(pending), kind: 'scope', severity: 'major',
      summary: 'changes in the scope are not staged',
      scenario: 'The patch contains only staged changes. Run `git add -A` inside the worktree after the last edit.' })
  }
  return defects
}

function claims(report) {
  const files = (report.files_changed || []).map(f => `${f.path}: ${f.change}`)
  const tests = (report.tests || []).map(t => `test ${t.name} fails if ${t.fails_if}`)
  return list([...files, ...tests]) || '- (the implementer listed no changes)'
}

function limits(t) {
  return `Hard limits: work only inside the worktree ${q(t.worktree)} (branch ${t.branch}) and never cd outside it. ` +
    'Do not commit, push, open pull requests or upload anything. Run only the scoped gate commands the spec lists; ' +
    'never run full-gate, code generation or format-all recipes. Edit files only inside this scope: ' +
    `${t.scope.join(', ')}. Put no AI or tool names into anything you produce. Finish with \`git add -A\` inside ` +
    'the worktree and `git status --porcelain`; every listed path must be inside the scope, so unstage anything else, ' +
    'delete new scratch files and restore tracked files you did not mean to change.'
}

const REPORT_FORMAT = 'Return the mandatory report as the structured output: files_changed (path and change), ' +
  'gate (every gate command you ran, its exit code and the last lines of output), tests (per test you wrote or changed: ' +
  'the change in the code under test that makes it fail), not_done, questions.'

// The spec's hash is part of every implementer prompt, so a changed spec never matches a
// journaled result on resume.
// The worktree's identity (it changes when a worktree is recreated) and its starting commit are part
// of every implementer prompt too, so a journaled result of an earlier worktree is never replayed.
const specLine = t => `Your first action: read the spec file ${t.spec} (sha256 ${t.spec_sha256 || 'unknown'}) in full, then ` +
  `follow it section by section. Worktree ${t.worktree_id || 'unknown'} started at ${t.start_head || 'unknown'}.`

function implementPrompt(t, step, history) {
  const previous = history.length
    ? '\n\nEarlier attempts on this task failed on:\n' +
      list(history.flatMap(h => h.defects.map(d => `${d.file}: ${d.summary} (${d.kind}, ${d.severity}). ${d.scenario}`)))
    : ''
  return `${role('implementer')}You are the tier ${step.tier} implementer for task ${t.id} (${t.title}) of an orchestrated run.\n` +
    `${specLine(t)} If the worktree already holds staged changes from an earlier attempt, keep, fix or discard them as ` +
    `the spec requires.\n\n${limits(t)}${previous}\n\n${REPORT_FORMAT}`
}

function reworkPrompt(t, step, report, gate, defects, round) {
  const failing = gate.exit_code !== 0 ? `\n\nGate output (verbatim):\n${gate.tail}` : ''
  return `${role('implementer')}You are the tier ${step.tier} implementer for rework round ${round} of task ${t.id} (${t.title}).\n` +
    `${specLine(t)} Its Definition of done is unchanged.\n` +
    `The previous attempt, whose changes are staged in the worktree, reported:\n${claims(report)}\n\n` +
    `These defects must be fixed (quoted from the gate and the independent review):\n` +
    list(defects.map(d => `${d.file}${d.line ? ':' + d.line : ''}: ${d.summary} (${d.kind}, ${d.severity}). Scenario: ${d.scenario}`)) +
    `${failing}\n\n${limits(t)}\n\n${REPORT_FORMAT}`
}

const RELAY = 'Run exactly this command once and nothing else, with the longest command timeout available (600000 ms). ' +
  'If it is still running when that timeout would expire, run it in the background and wait for it to finish. Then return ' +
  'the JSON object it prints as the structured result, unchanged; never write or guess the output yourself. A non-zero ' +
  'exit status is an expected outcome, not an error to fix. Do not edit any file.\n\n'

function gatePrompt(t, level, round) {
  return RELAY + helper('gate', ['--run-dir', q(`${A.run_dir}/gates`), '--label', q(`${t.gate_label_prefix}-L${level}r${round}`),
    '--worktree', q(t.worktree), ...t.scope.flatMap(s => ['--scope', q(s)]), '--', ...t.gate.map(q)])
}

// The spec hash and the staged tree are part of every review prompt, so a verdict is never
// replayed from a journal for other content or another spec.
function reviewPrompt(t, lens, report, previousDefects, tree) {
  const lensText = lens.key === 'adversary'
    ? '\n\nYour lens is adversarial: try to break the change. For each scenario below that applies to this code, ' +
      'reason it through against the actual implementation and its tests, and report a defect only with a concrete ' +
      `failing scenario:\n${list(A.adversary_variations)}\nGreen state assertions do not prove a collaborator was called once. ` +
      'Another reviewer reruns the gate; do not rerun the full gate commands yourself, run only single tests you need.'
    : `\n\nYour lens is conformance: check the diff against every Definition of done item and every Constraint of the spec.\n` +
      `Run the spec's gate commands yourself inside the worktree:\n${list(t.gate)}`
  const recheck = previousDefects && previousDefects.length
    ? `\n\nA previous review round found these defects; check that each one is fixed:\n` +
      list(previousDefects.map(d => `${d.file}: ${d.summary}`))
    : ''
  return `${role('reviewer')}You are an independent reviewer of task ${t.id} (${t.title}); you did not write this change.\n` +
    `Read the reviewer brief ${A.reviewer_brief} and the spec ${t.spec} (sha256 ${t.spec_sha256 || 'unknown'}) in full. ` +
    `The change is the staged diff of the worktree (\`git -C ${q(t.worktree)} diff --cached\`, staged tree ${tree}).\n\nVerify these implementer claims against the code; do not ` +
    `trust them:\n${claims(report)}${lensText}${recheck}\n\n` +
    'Do not edit any file, do not stage, do not commit. Never run full-gate, code generation or format-all recipes. ' +
    'Return the verdict as the structured output: verdict, defects (file, line, kind, severity, summary, scenario), ' +
    'notes (at most five), gate (each command you ran, its exit code and its last 10 lines).'
}

function confirmPrompt(t, defects, tree) {
  return `${role('reviewer')}Independent reviewers of task ${t.id} disagreed. The change is the staged diff of the worktree ` +
    `${q(t.worktree)} (staged tree ${tree}); the spec is ${t.spec} (sha256 ${t.spec_sha256 || 'unknown'}). For each defect below decide whether it is real. Mark confirmed=false only when ` +
    'you can show from the code that the claim is wrong, already handled or unreachable; when uncertain, mark confirmed=true. ' +
    'Do not edit any file.\n\n' + defects.map((d, i) => `${i}: ${JSON.stringify(d)}`).join('\n') +
    '\n\nReturn {defects: [{index, confirmed, evidence}]} as the structured output.'
}

async function review(t, level, round, report, previousDefects, tree) {
  const verdicts = await parallel(t.lenses.map(lens => () => agent(reviewPrompt(t, lens, report, previousDefects, tree), {
    label: `review:${t.id}:${lens.key}:L${level}r${round}`, phase: 'Review', ...typeOf('reviewer'), ...pick(lens),
    schema: A.schemas.verdict,
  })))
  if (verdicts.some(v => !v)) return null
  const reviews = verdicts.map((v, i) => ({ lens: t.lenses[i].key, model: t.lenses[i].model, verdict: v.verdict, defects: v.defects.length }))
  const failing = verdicts.filter(v => v.verdict === 'FAIL')
  let defects = failing.flatMap(v => v.defects)
  if (!failing.length) return { pass: true, reviews, defects: [] }
  if (failing.length < verdicts.length && defects.length) {
    const decision = await agent(confirmPrompt(t, defects, tree), {
      label: `confirm:${t.id}:L${level}r${round}`, phase: 'Review', ...typeOf('reviewer'), ...pick(t.confirm),
      schema: A.schemas.confirm,
    })
    if (!decision) return null
    const refuted = new Set(decision.defects.filter(d => d.confirmed === false).map(d => d.index))
    defects = defects.filter((_, i) => !refuted.has(i))
    reviews.push({ lens: 'confirm', model: t.confirm.model, verdict: defects.length ? 'FAIL' : 'PASS', defects: defects.length })
  }
  if (!defects.length && failing.every(v => v.defects.length === 0)) {
    // A FAIL that names no defect is not actionable as is; send it back as an explicit defect.
    defects = [{ file: '(review)', kind: 'other', severity: 'major', summary: 'a reviewer failed the change without naming a defect', scenario: 'Re-review required.' }]
  }
  return { pass: defects.length === 0, reviews, defects }
}

async function runTask(t, dependencies) {
  const outcome = (status, reason, extra = {}) => ({ id: t.id, status, reason, ...extra })
  if (t.done) {
    // Carried over from an earlier run by plan.py workflow-args --only: verified there, not rerun.
    return t.previous && t.previous.status === 'PASS'
      ? { ...t.previous, id: t.id, carried: true }
      : outcome('BLOCKED', 'a carried-over dependency has no PASS result in the previous run')
  }
  const failed = dependencies.filter(d => !d || d.status !== 'PASS')
  if (failed.length) return outcome('SKIPPED', `dependency did not pass: ${failed.map(d => (d ? d.id : 'unknown')).join(', ')}`)

  let expectedHead = t.start_head
  if (t.scaffold_from.length) {
    const patches = t.scaffold_from.map(id => dependencies.find(d => d.id === id).patch)
    const scaffold = await agent(RELAY + helper('scaffold', ['--worktree', q(t.worktree), ...patches.flatMap(p => ['--patch', q(p)])]), {
      label: `scaffold:${t.id}`, phase: 'Finish', ...pick(A.utility || t.chain[0]), schema: A.schemas.scaffold,
    })
    if (!scaffold) return outcome('BLOCKED', 'the scaffolding agent returned no result')
    if (scaffold.exit_code !== 0) return outcome('BLOCKED', `dependency scaffolding failed: ${scaffold.output}`)
    expectedHead = scaffold.head || expectedHead
  }

  const history = []
  for (let level = 0; level < t.chain.length; level++) {
    const step = t.chain[level]
    let report = await agent(implementPrompt(t, step, history), {
      label: `impl:${t.id}:L${level}`, phase: 'Implement', ...typeOf('implementer'), ...pick(step), schema: A.schemas.report,
    })
    if (!report) return outcome('BLOCKED', `implementer at tier ${step.tier} returned no result`, { history })
    let previous = null
    for (let round = 0; ; round++) {
      const gate = await agent(gatePrompt(t, level, round), {
        label: `gate:${t.id}:L${level}r${round}`, phase: 'Gate', ...pick(A.utility || step), schema: A.schemas.gate,
      })
      if (!gate) return outcome('BLOCKED', 'the gate agent returned no result', { history })
      let defects = gateDefects(t, gate, expectedHead)
      const environment = defects.length ? '' : environmentProblem(t, gate)
      if (environment) return outcome('BLOCKED', environment, { tier: step.tier, level, rounds: round, gate, history })
      let reviews = []
      if (!defects.length) {
        const verdict = await review(t, level, round, report, previous, gate.tree)
        if (!verdict) return outcome('BLOCKED', 'a reviewer returned no result', { history })
        reviews = verdict.reviews
        defects = verdict.defects
        if (verdict.pass) {
          const finish = await agent(RELAY + helper('finish', ['--worktree', q(t.worktree), '--patch', q(t.patch), ...t.scope.flatMap(s => ['--scope', q(s)])]), {
            label: `finish:${t.id}`, phase: 'Finish', ...pick(A.utility || step), schema: A.schemas.finish,
          })
          if (!finish) return outcome('BLOCKED', 'the finishing agent returned no result', { history })
          if (finish.verify_clean_exit !== 0) return outcome('BLOCKED', `verify-clean failed after review: ${finish.output}`, { history })
          if (finish.tree !== gate.tree) return outcome('BLOCKED', 'the staged tree changed between the gate and the export (a reviewer or another process wrote to the worktree)', { history })
          return outcome('PASS', '', {
            tier: step.tier, level, rounds: round, gate, reviews,
            patch: finish.patch, sha256: finish.sha256, tree: finish.tree, files: finish.files, history,
          })
        }
      }
      history.push({ level, round, gate_exit: gate.exit_code, reviews, defects })
      const stuck = nonConverging(previous, defects)
      if (round >= ROUNDS || stuck) {
        log(`${t.id}: tier ${step.tier} ${stuck ? 'is not converging' : 'used its rework rounds'}; escalating`)
        break
      }
      report = await agent(reworkPrompt(t, step, report, gate, defects, round + 1), {
        label: `rework:${t.id}:L${level}r${round + 1}`, phase: 'Implement', ...typeOf('implementer'), ...pick(step), schema: A.schemas.report,
      })
      if (!report) return outcome('BLOCKED', `implementer at tier ${step.tier} returned no result in rework`, { history })
      previous = defects
    }
  }
  const worst = history.length ? Math.max(0, ...history[history.length - 1].defects.map(d => SEVERITY[d.severity] || 0)) : 0
  return outcome('ESCALATE', `tier ${t.chain[t.chain.length - 1].tier} did not converge (worst open severity ${worst}); the orchestrator decides`, { history })
}

// One task's failure, including a runtime refusal such as a budget or agent-count limit,
// must not discard the results of the others.
async function settle(t, dependencies) {
  try {
    return await runTask(t, dependencies)
  } catch (error) {
    return { id: t.id, status: 'BLOCKED', reason: `the loop stopped for this task: ${String((error && error.message) || error)}` }
  }
}

const running = {}
for (const t of A.tasks) {
  const needed = [...new Set([...t.depends_on, ...t.scaffold_from])]
  running[t.id] = Promise.all(needed.map(id => running[id])).then(deps => settle(t, deps))
}
const tasks = await Promise.all(A.tasks.map(t => running[t.id]))
const counts = tasks.reduce((acc, r) => ({ ...acc, [r.status]: (acc[r.status] || 0) + 1 }), {})
log(`finished: ${Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(', ')}`)
return { schema: 'orchestrate-execute-result/v1', version: A.version, tasks }
