# 2026-09-27: evidence for 0.6.0 (execution loop as code, adversary lens, checked plan)

No new run was made for this file. It collects the evidence from the four recorded runs
(2026-09-12 on both harnesses, 2026-09-13 on both harnesses) that motivated the 0.6.0 changes, so
that the next benchmark can confirm or reject them. Run directories are the ones cited in
`2026-09-12-two-harnesses.md` and `2026-09-13-skill-0.3.md`.

## 1. Single-lens review missed a behavioural regression

- 2026-09-13, T2 (profile loading moved into a BLoC): the Claude Code opus review passed round zero;
  the Codex tier-3 review failed the same archetype on a same-turn coalescing defect (two refreshes
  in one turn against a repository that completes synchronously called it twice). An independent
  opus review told to exercise exactly that scenario found the same defect in the Claude
  implementation. All seven gates were green in both cells.
- 2026-09-12: the integrated review found cross-task problems that every per-task review had passed,
  and the integrated analyzer comparison caught a new lint issue that every per-task gate had passed.

Change: tasks tagged `async`, `concurrency`, `security`, `migration` or `data` get a second reviewer
with an adversarial lens on a different model (routing `adversary`), driven by a fixed list of
failure scenarios; when the two lenses disagree, a tie-break reviewer must show from the code that a
defect is wrong before it is dropped. Untagged tasks keep one reviewer. This is a protocol default,
not a tier change; the tier table is unchanged.

Open question for the next run: does the adversary lens catch the T2 defect class at round zero, and
what does it add per risky task (one reviewer, plus a tie-break on disagreement)?

## 2. Orchestrator mistakes caused rework that a validator prevents

- 2026-09-12 Claude Code: three of four rework rounds were orchestrator errors: a dependent worktree
  scaffolded on one predecessor instead of all, and a grep criterion scoped too widely.
- 2026-09-13 Codex B: an exploratory path that did not exist, recorded as an orchestrator setup error.

Change: the plan is `plan.json`, checked by `plan.py check` (literal scopes, unknown or cyclic
dependencies, overlapping scopes that are not serialized); the workflow scaffolds every task on all
transitive dependencies in topological order, computed by `plan.py workflow-args`, not by memory.

## 3. Free-text reports and verdicts needed repair

- 2026-09-13 Codex B: a report-only supplement was needed because the per-test mutation mapping was
  missing. 2026-09-12: a one-line task spec without Change and Report sections lowered the protocol score.
- 2026-09-12 Claude Code: `status.md` kept stale "running" rows after the run.

Change: implementer reports, verdicts, gate results and exports are JSON validated against
`skill/schemas/`; each test in a report carries `fails_if`; the final table is rendered from the
execute result by `plan.py report`.

## 4. Steps the protocol requires but the orchestrator performs by hand

The anti-patterns list (doing a subtask yourself, narrating a spawn that never happened, reporting
an agent that has not returned, removing a worktree without `verify-clean`) is prompt-enforced in
0.5.0. In 0.6.0 phases 2 to 4 run as a workflow script: an agent result exists only if the runtime
spawned the agent; the loop bound, escalation and the order gate -> review -> export are code; the
export refuses a staged tree that changed after the gate. On Codex the same script runs under
`run_workflow.js`, and the gate, scaffold and export steps run as local commands, not agents.

Cost to watch: the workflow runtime cannot resume an agent, so rework and re-review use fresh
agents. The 2026-09-12 measurement put a resumed re-review at 10k to 18k tokens against 60k to 80k
fresh; expect that difference per rework round.

## 5. Live smoke run of the workflow on Claude Code (2026-09-27)

A throwaway Python repository with two tasks: t1 (tier 1, tests for an existing method) and t2
(tier 2, depends on t1, tagged `concurrency`, a new method with tests). Launched with the `Workflow`
tool, `scriptPath` = `skill/workflows/orchestrate-execute.js`, `args` from `plan.py workflow-args`.

| Attempt | Agents | Subagent tokens | Wall | Outcome |
|---------|--------|-----------------|------|---------|
| 1 | 12 | 335k | 5.7 min | t1 ESCALATE after all three tiers, t2 SKIPPED |
| 2 | 10 | 234k | 4.4 min | t1 PASS at tier 1 round 0, t2 PASS at tier 2 round 0 |

Attempt 1 found a defect in the loop, not in the implementers: the gate command wrote
`__pycache__` files, and the loop charged every untracked file to the implementer, so no tier could
converge (the non-convergence rule did bound the waste to one rework per tier). Fixed before attempt
2: `task.py gate` reports unstaged changes, untracked files and staged paths outside the scope
separately; untracked files outside the scope stop the task with an environment reason; Phase 0
requires a baseline gate without untracked files (here: `__pycache__/` in `.git/info/exclude`).

Attempt 2: t2 was scaffolded on t1's patch and its exported patch holds only its own two files; the
risky task got the opus conformance review and the sonnet adversary review, both PASS;
`plan.py verify-result` re-checked both patches, gate logs, `verify-clean` and trees (exit 0).

## 6. Live run of the same script on Codex (2026-09-28)

The same two-task plan with `"harness": "codex"`, run by `run_workflow.js` with codex-cli 0.157.0
inside the default sandbox (nested workspace-write and read-only sandboxes worked; implementers got
the Git common directory through `--add-dir`). Strict output schemas were accepted.

| Task | Implementer | Rounds | Reviews | Outcome |
|------|-------------|--------|---------|---------|
| t1 | gpt-5.6-luna xhigh | 1 | gpt-5.6-sol xhigh: FAIL (zero and negative step tests started from zero, so they did not prove an existing value stays unchanged), then PASS | PASS |
| t2 | gpt-5.6-sol xhigh, scaffolded on t1's patch | 0 | gpt-6-astra low conformance PASS, gpt-5.6-sol xhigh adversary PASS | PASS |

Wall time 6 min 10 s; gate, scaffold and export ran as local commands; `plan.py verify-result`
exited 0. A rerun of the same command replayed the implementers and t2's reviews from the journal
and re-reviewed only t1, whose staged tree had changed since its first review (verdicts are keyed to
content), then verified again.

## 7. Saved workflow

`install.sh` copies the script into `~/.claude/workflows/`. A fresh headless session resolved
`Workflow` by `name: "orchestrate-execute"`; with empty args the script refused at its args check
before any agent started. Saved workflows load at session start or on `/reload-skills`.
