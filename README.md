# orchestrate

One skill that turns a coding agent into an orchestrator: it splits a task into subtasks, writes a
self-contained spec for each and a checked `plan.json`, routes every subtask to a model tier, runs
them in parallel in isolated git worktrees, verifies each result with a mechanical gate plus
independent review, sends rework back with the defects verbatim, escalates what does not converge,
integrates what passed on a separate branch, and reports per wave. The loop from dispatch to export
is a workflow script, so no step depends on the orchestrator remembering it.

The same `SKILL.md` runs in Claude Code (`/orchestrate <task>`) and in Codex (`$orchestrate <task>`).
It never triggers on its own.

## Install

```sh
git clone https://github.com/aiatsuk/orchestrate
cd orchestrate
./install.sh          # symlinks skill/ into both harnesses' skill dirs and the Claude roles; copies the Codex roles (symlinks are ignored there)
./install.sh --repo ~/src/app   # project scope: copies the skill and roles, appends an orchestration block to AGENTS.md
make test             # unit tests, no dependencies beyond python3 and git
```

Repo scope instead of user scope: copy or symlink `skill/` to `.claude/skills/orchestrate/` or
`.agents/skills/orchestrate/` inside the repository.

## Use

```
/orchestrate Add a unit test for SmallManager, retire the legacy logger, then sync the docs. Repo ~/src/app, branch feature/canon.
$orchestrate Run the brief at ~/briefs/canon-wave-1.md
```

The orchestrator shows a plan table with tiers before dispatching, then reports per wave with gate
output and reviewer verdicts. Nothing is committed unless you ask. The integration branch is left
staged for you to merge or discard.

For a fully autonomous Codex run see the launch line in `skill/references/harness-codex.md`.

## Model tiers

| Tier | Claude Code | Codex | Use for |
|------|-------------|-------|---------|
| 1 mechanical | haiku | gpt-5.6-luna, xhigh | fully specified edits with a reference file and a runnable check |
| 2 standard | sonnet | gpt-5.6-sol, xhigh | implementation from a clear spec; reviews of tier 1 and 2 work |
| 3 structural | opus | gpt-6-astra, low | structural refactors, hard bugs; reviews of tier 3 work and of the integrated whole |
| orchestrator | top tier available | gpt-6-astra, high | planning, specs, verification decisions |

Measured results behind the table and the open hypotheses: `skill/references/routing.md`.

## Named roles

`orchestrate-explorer` (read-only, tier 1) maps a task area before the spec is written;
`orchestrate-implementer` (workspace-write, model passed per task) executes a spec in its own
worktree; `orchestrate-reviewer` (read-only, tier 3) verifies against the spec and runs the gate
itself. Role files live in `skill/claude/agents/` and `skill/codex/agents/`. Read-only is enforced
by the sandbox on Codex; on Claude Code the roles keep Bash, so it is a convention checked
afterwards with `verify-clean`.

## Measuring a run

```sh
python3 evals/claude_usage.py --session-dir ~/.claude/projects/<project>/<session>   # Claude Code: per agent and model
python3 evals/codex_usage.py --list --date 2026-09-12   # sessions that spawned subagents
python3 evals/codex_usage.py --latest                   # per thread, per model, cache rate, rate-limit window delta
python3 evals/score.py <run-dir>                        # protocol completeness of a run directory
```

## How it works

1. **Gate and intake**: decide root-only versus delegated; read the repo's agent instructions and gate
   commands; explorers map each task area; one worktree per task with dependencies fetched; a baseline gate run.
2. **Plan**: `plan.json` with tasks, literal scopes, tiers, dependencies, gates, risk tags and
   reviewer tiers, checked by `plan.py check` and rendered as a table.
3. **Specs**: one file per task from `skill/references/spec-template.md`, absolute paths, a
   definition of done with a scoped gate, a mandatory report format.
4. **Execution loop** (`skill/workflows/orchestrate-execute.js`; the `Workflow` tool on Claude Code,
   `run_workflow.js` on Codex): per task in dependency order, scaffold on all dependency patches,
   implement, gate through `task.py`, review (plus an adversary lens on another model for risky
   tasks, and a tie-break when lenses disagree), rework with the defects verbatim, escalate one tier
   up after two rounds or when a rework makes no progress, export and `verify-clean`.
5. **Verify the result**: `plan.py verify-result` re-checks every passed task against the files.
6. **Integrate**: patches applied on a dedicated branch, the full gate once, the analyzer compared
   against the baseline list, a final review of the whole diff.

## Repository layout

```
skill/                 the skill: SKILL.md, references/, scripts/, agents/openai.yaml, routing.json, schemas/
skill/workflows/       orchestrate-execute.js, the execution loop as a workflow script
skill/scripts/         plan.py (plan checks, workflow args, result verification), task.py (one step, JSON out),
                       run_workflow.js (the loop on Codex), gate.py, integrate.sh, prepare_worktrees.sh
skill/claude/agents/   named roles for Claude Code; skill/codex/agents/ and config.example.toml for Codex
evals/                 score.py (protocol completeness), claude_usage.py and codex_usage.py (usage attribution), task archetypes, dated results
examples/              a brief, a plan, a spec, a reviewer brief, a verdict, a status trail, a final report
tests/                 unit tests for the scripts, the workflow under node with scripted agents, and repository hygiene
AGENTS.md              rules for agents working on this repository; CLAUDE.md imports it
install.sh             user-scope install for both harnesses
```

## What the scripts enforce and what the prompt enforces

The scripts enforce: any non-zero gate exit is a failure, including signals and failures inside a
pipeline; every gate run gets its own attempt directory, so logs are never overwritten even after an
interrupted or concurrent attempt; the analyzer delta compares issue lists keyed by file and text,
not exit codes; integration is all or nothing, stages only the patches' files and refuses to
overwrite untracked or ignored files; a worktree is removed only after `verify-clean` proved that
its staged diff equals the saved patch, nothing is unstaged or untracked, and every staged path,
including the old path of a rename, is inside the spec's scope.

The execution loop enforces, as code: a result exists only if an agent was actually spawned and
returned schema-valid JSON; the gate runs before any review and a red gate never reaches a
reviewer; reviewer and implementer are separate agents; the rework limit and the escalation; risky
tasks get the adversary lens; dependent tasks wait for their dependencies and are scaffolded on all
of them; an export is refused when the staged tree changed after the gate. `plan.py check` refuses
cycles and overlapping scopes that are not serialized, and `plan.py verify-result` re-checks every
passed task against the files after the run.

The prompt enforces, and the code cannot: the delegation gate, spec quality, that the orchestrator
does not edit task worktrees itself outside the loop, and on Claude Code that a reviewer does not
write through Bash (the tree check detects staged writes, `verify-clean` the rest). Treat the run
directory as the evidence and read the staged diff before merging; an orchestrated run is not a
substitute for review of the result.

## Versioning

Semantic versioning. The version lives in `skill/SKILL.md` (frontmatter `metadata.version`), in
`VERSION`, and as the newest entry of `CHANGELOG.md`; a unit test keeps the three in sync. Every
release is an annotated git tag `vX.Y.Z` created with `make tag`. Runs report the skill version in
their final report, so eval results are comparable across versions.

## License

MIT.
