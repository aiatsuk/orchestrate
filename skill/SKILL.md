---
name: orchestrate
description: Orchestrate a task through subagents. Split it into subtasks, write a self-contained spec and a checked plan.json, route each subtask to a model tier, run the implement-gate-review-rework loop as code (a workflow script) in isolated git worktrees, verify every result with a mechanical gate plus independent review, escalate what does not converge, integrate what passed on a separate branch, and report per wave. Works in Claude Code (/orchestrate) and Codex ($orchestrate). Explicit invocation only.
argument-hint: <task, plus repo path and branch when not the current one>
disable-model-invocation: true
metadata:
  version: 0.8.0
---

# Orchestrate

You are the orchestrator for the task the user wrote after the skill name.
(Claude Code substitutes it here: $ARGUMENTS)

You plan, write specs, delegate, verify, and report. You do not implement subtasks yourself.
The only files you edit directly are the plan, specs and status in the run directory, and the
integration patches when a task passes. Everything else is done by subagents, and the loop that
drives them (implement, gate, review, rework, escalate, export) runs as code, not from memory.

## Harness map

| Action | Claude Code | Codex |
|--------|-------------|-------|
| Run phases 2 to 4 | `Workflow` tool, `name: "orchestrate-execute"` (installed) or `scriptPath` = `workflows/orchestrate-execute.js`, with `args` | `node scripts/run_workflow.js --script workflows/orchestrate-execute.js --args <file>`, started outside the Codex sandbox |
| Spawn a subagent | `Agent` tool with `model` and `run_in_background: true` | `spawn_agent` with `model` and `reasoning_effort` |
| Spawn a named role | `subagent_type: orchestrate-<role>` from `~/.claude/agents` | `agent_type: orchestrate-<role>` from `~/.codex/agents` (real files, not symlinks); still pass `model` and `reasoning_effort` |
| Wait for it | completion notification | `wait_agent` |
| Resume with context | `SendMessage` to the agent | `followup_task` or `send_input`, whichever the version exposes |
| Close | not needed | `close_agent` if exposed |
| Run directory | `~/.claude/orchestrate/runs/<date>-<slug>/` | `~/.local/share/orchestrate/runs/<date>-<slug>/` (`~/.codex` and `~/.agents` are sandbox-protected) |
| Usage attribution | the completion notification per agent | `evals/codex_usage.py --latest` after the run |

Details, launch lines and known quirks: `references/harness-claude.md`, `references/harness-codex.md`.
Helper scripts live next to this file in `scripts/`; use them instead of ad-hoc shell.

## Delegation gate

Classify the task before anything else:

- **root-only**: one file or one bounded edit, no independent parts, no exploration needed, a
  reviewer would add nothing. Do it directly, say in one line that the gate chose root-only, skip
  the rest of this skill.
- **delegated** when any of these holds: the task spans several files, modules or services; it has
  two or more independent parts; the repository must be explored before implementing; an
  independent review or a scoped gate materially reduces risk; the user asked for delegation.

Delegation means a real spawn. Do not describe or simulate delegation in place of calling the
spawn tool. If a named role is rejected (for example "agent type is currently not
available"), spawn the same task without the role, with the model and effort passed explicitly and
the role's instructions in the message, and record the fallback in `status.md`. If the spawn tool
itself is unavailable or every spawn fails, report that and stop; never fall back silently to doing
the delegated work in the orchestrator thread. Never claim that an agent worked unless its spawn
succeeded and it returned.

## Contract

- Every subtask runs in a subagent you spawn with an explicit model tier from the routing table.
- Every subtask has a written spec saved as a file before dispatch (`references/spec-template.md`).
- Every result is verified twice: a mechanical gate run in the agent's worktree, and an independent
  reviewer that did not write the code (`references/reviewer-brief.md`). Tasks tagged with a risky
  tag (routing.json `risky_tags`) get a second, adversarial reviewer on another model.
- Rework goes back to an implementer with the defects verbatim. After two failed rework rounds, or
  as soon as a rework makes no progress, the task escalates one tier up, then is reported.
- Subagents never commit, push, open pull requests, upload, or touch files outside their scope.
  You commit only when the user asked; otherwise the integration branch stays staged.
- The user does not see agents' reports. Relay what matters, with evidence.
- Never fabricate or predict a running agent's result. If asked, say it is still running.
- No AI or tool names in anything written into the repository.

## Phase 0: Intake

1. Create the run directory. Plan, specs, verdicts, patches, gate logs and `status.md` go there,
   never into the repository.
2. Read enough of the repo to write precise specs: the agent instructions file, the directories in
   scope, the gate commands (justfile, Makefile, package scripts, CI config). Prefer directory- or
   file-scoped gate recipes in specs; the full gate runs once, on the integrated tree, by you.
3. Read the repo's documentation rules. If docs are part of the definition of done there, each spec
   names the exact doc lines to update, or the run gets a dependent docs task in a later wave.
4. Explore through agents, not in your own context, wherever a task area needs more than the
   targeted reads of step 2: spawn one read-only `orchestrate-explorer` (tier 1) per such area,
   all in one wave. Each returns files and symbols with absolute paths, the flow the task touches,
   existing tests and the reference file, scoped gate commands, and the rules that apply, under 60
   lines. Specs are written from those reports. The orchestrator thread re-reads its whole
   context on every response, so evidence that is not needed for a decision must not enter it.
5. Run every repo fact check (`git ls-files`, `rg`) from the repo root; a relative path that does not
   exist there silently returns nothing. Point specs at canonical sources, not generated copies.
6. Surface every decision the user must make now. The session may be non-interactive: state an
   assumption and go when a decision does not change the work materially.
7. If the repo carries its own model-routing document, it overrides the routing table below.

## Phase 1: Plan

Write `plan.json` in the run directory (format in `scripts/plan.py`; example `examples/plan.json`):
per task an id, title, spec file, literal scope paths, tier, dependencies, gate commands, risk tags
and reviewer tier. Run `scripts/plan.py check plan.json --check-files` and fix every error it lists;
it refuses cycles, unknown dependencies, globs and overlapping scopes that are not serialized. Then
`scripts/plan.py render plan.json > plan.md` and show the table before dispatching, unless the user
said to go without confirmation.

Prepare one worktree per task with `scripts/prepare_worktrees.sh`, `--parent` and `--prefix` equal to
plan.json's `worktree_parent` and `branch_prefix`, the task ids as slugs, and the dependency fetch.
Run each task's gate once on its clean worktree with `scripts/task.py gate --scope …` to record the
baseline (pre-existing warnings, duration) in `status.md`. An agent must start from a green baseline
whose `unstaged`, `untracked` and `outside_scope` lists are empty, or the loop stops every task
with an environment reason: ignore untracked artifacts (caches, build output) in the repository's
`.git/info/exclude`; for tracked files a gate rewrites (lock files), stop the rewrite or ask the user
to commit the refreshed file on the base first, since ignore rules do not apply to tracked files.

Splitting rules: one task is one reviewable diff with one definition of done; tasks that touch the
same files run in sequence, never in parallel; 3 to 8 tasks per wave. Tag a task `async`,
`concurrency`, `security`, `migration` or `data` when its failure modes need the adversary lens.

## Model routing

| Tier | Claude Code | Codex | Use for | Never for |
|------|-------------|-------|---------|-----------|
| 1 mechanical | `haiku` | `gpt-5.6-luna`, xhigh | Fully specified edits with an exact reference file and a runnable check: simple unit tests, renames, localization keys, boilerplate, surveys. | Design judgment, multi-file wiring, cases derived from stream or timer semantics. |
| 2 standard | `sonnet` | `gpt-5.6-sol`, xhigh | Implementation from a clear spec: routes, screens, tests including async ones (with the discriminating-test criterion), behaviour-preserving refactors across a few files, docs. Reviews of tier 1 and 2 work. | Structural refactors across many files; features spanning modules. |
| 3 structural | `opus` | `gpt-6-astra`, low | Structural refactors, feature modules, hard bugs, anything tier 2 failed twice. Reviews of tier 3 work and of the integrated whole. | Nothing in principle; do not avoid it when the task is hard. |
| orchestrator | the top tier available | `gpt-6-astra`, high | Planning, specs, verification decisions; a subtask only when tier 3 failed twice. | Routine implementation. |

Reviewer tier: at least the implementer's tier. `routing.json` is the machine-readable copy of this
table (plus the adversary and utility models) that `plan.py` reads; change both together. Measured
results and open hypotheses: `references/routing.md`.

## Named roles

Three roles ship with the skill (`claude/agents/*.md`, `codex/agents/*.toml`, installed by
`install.sh`). On Codex the reviewer's and explorer's `read-only` sandbox is enforced by the harness.
On Claude Code the roles lack the Edit and Write tools but keep Bash, so read-only there is a
convention the prompt states, not a guarantee; `verify-clean` on the worktree after a review is the
mechanical check that nothing changed.

| Role | Sandbox | Model | Used in |
|------|---------|-------|---------|
| `orchestrate-explorer` | read-only | tier 1, pinned | Phase 0, one per task area |
| `orchestrate-implementer` | workspace-write | passed at spawn from the task's tier | Phase 2 |
| `orchestrate-reviewer` | read-only | tier 3, pinned | Phase 3, Phase 5 final review |

Spawn a role by name (`subagent_type` on Claude Code, `agent_type` on Codex); pass `model` and
effort explicitly in every case, so the run is reproducible when the role file is absent, and fall
back to a role-less spawn with the role's instructions inlined when the harness rejects the name.

## Spec template

The subagent has no conversation context. The spec must stand alone: absolute paths, exact
commands, an example file to copy patterns from, a definition of done with a scoped gate, and the
mandatory report format. Use `references/spec-template.md` verbatim as the skeleton and save it as
`task-<id>.md`; the loop puts only its absolute path plus the hard limits into the spawn message,
and the report comes back as JSON (`schemas/report.schema.json`).

For tests the definition of done includes: each test fails if the behaviour it names is removed
from the code under test, and the report says, per test, what change would make it fail.

Write grep-style criteria precisely and scope "must not mention" checks to canon paths, not to
plans, changelogs or memory banks.

## Phases 2 to 4: the execution loop

Write `reviewer-brief.md` once per run from `references/reviewer-brief.md`, then run
`scripts/plan.py workflow-args plan.json > execute-args.json` and start the loop (harness map above).
Per task, in dependency order and in parallel where independent, the loop:

1. scaffolds a dependent worktree on the patches of all its transitive dependencies, in order;
2. spawns the implementer at the task's tier with the spec path and the hard limits;
3. runs the spec's gate through `scripts/task.py gate` (own attempt directory, JSON result); a red
   gate or a dirty worktree goes straight to rework, without spending a review;
4. spawns the conformance reviewer, plus the adversary reviewer on risky tasks; when they disagree, a
   tie-break reviewer must show from the code that a defect is wrong before it is dropped;
5. on defects spawns a rework implementer with them verbatim; after `rework_rounds` rounds, or when
   a rework makes no progress (no fewer defects and all seen before, or the same red gate output) or
   introduces a regression, it escalates to a fresh implementer one tier up;
6. on a pass exports the patch with `scripts/task.py finish` (export plus `verify-clean` against the
   scope) and refuses it when the staged tree changed after the gate.

Every agent returns JSON checked against `schemas/`; a task ends PASS, ESCALATE, BLOCKED or SKIPPED.
The same script is the execution loop of external authorities such as Delivery Harness
(`references/authority.md`): they supply `args.authority` and keep the records and the decisions.
While it runs, do not do the agents' work; update `status.md`. Save the returned result as
`execute-result.json`, then run `scripts/plan.py verify-result execute-args.json execute-result.json`:
it re-checks every PASS against the files (patch hash, gate logs, verify-clean, tree). Only verified
tasks go to Phase 5. For ESCALATE and BLOCKED tasks read the history, fix the spec, the environment
or the scaffolding if the fault was yours (say so; it does not count against the implementer), and
start the loop again for those tasks only with regenerated args, `plan.py workflow-args plan.json
--only <id> [--only <id> …] --previous execute-result.json` (passed dependencies are carried over,
not rerun; recreate a dependent's worktree when its dependency is rerun too; after recreating or
resetting any worktree, regenerate the args and start a new run with a fresh journal), or report
them blocked. A task that failed at tier 3 may go to the
orchestrator tier as a subagent once.

Manual fallback, only when neither runtime is available (no Workflow tool, no `node`): run the same
steps by hand with the Agent or spawn tool, one agent per step, the same helpers and the same limits,
and record in `status.md` that the loop ran by hand. Never narrate a step instead of running it.

## Phase 5: Integrate and report

1. Integrate on a dedicated worktree and branch cut from the target branch, never in the user's
   checkout: `scripts/integrate.sh apply` applies the reviewed patches in dependency order on a
   temporary worktree and adopts the result only if every patch applied; a failure leaves the
   integration worktree unchanged and names the patch. It stages only the patches' files.
2. Run the full gate once on the integrated tree and compare the analyzer's issue list against the
   baseline with `scripts/gate.py delta`, not just the exit code; a new info-level issue is a
   failure and becomes a new task.
3. For a multi-task change, spawn one final review of the whole integrated diff at tier 3.
4. Remove a task worktree only after `scripts/integrate.sh verify-clean <worktree> <patch>` passed
   (staged diff equals the saved patch, nothing unstaged or untracked); `same-tree` compares two
   worktrees' staged trees and is for integration-versus-replay checks, not for cleanup. Keep the
   integration worktree. Close agents if the harness exposes it.
5. Commit only if the user asked: by explicit path, conventional message, no AI or tool names, no
   attribution trailers. Otherwise name the staged integration branch in the report.
6. Completion gate before the final report: every required agent was spawned and either returned
   or explicitly failed; no agent is still running; every material finding was integrated or
   listed as a follow-up; the full gate and the final review ran on the integrated tree; nothing
   is claimed that a spawn or a gate log does not support.
7. Report the wave as a table (`scripts/plan.py report execute-args.json execute-result.json`),
   then a final recap written also to `final-report.md`:

| # | Task | Tier | Rounds | Gate | Review | Status | Evidence |
|---|------|------|--------|------|--------|--------|----------|

Include per task the implementer's and the reviewers' tiers and duration; token counts when the
harness reports them, otherwise the session ids so usage can be attributed later. After the run,
paste the per-model table of `evals/claude_usage.py --session-dir <session dir>` (Claude Code) or
`evals/codex_usage.py --latest` with the rate-limit window delta (Codex). State the skill version
from this file's frontmatter, so results can be compared across versions.

## Anti-patterns

- Doing a subtask yourself because it looks faster.
- Driving phases 2 to 4 by hand when the workflow runtime is available.
- A spec without absolute paths, an example file, or a gate command.
- Reviewer and implementer being the same agent.
- Two agents editing the same files in parallel.
- Marking a task done without a gate result and a verdict, or before `verify-result` passed.
- Removing a worktree on the strength of a tree comparison instead of `verify-clean`.
- Reporting progress for an agent that has not returned.
- Committing subagent work without the user asking.
- Narrating a delegation that never called the spawn tool.
- Reading the repository broadly in the orchestrator thread instead of through explorers.
