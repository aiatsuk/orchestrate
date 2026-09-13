# orchestrate

One skill that turns a coding agent into an orchestrator: it splits a task into subtasks, writes a
self-contained spec for each, routes every subtask to a model tier, runs them in parallel in isolated
git worktrees, verifies each result with a mechanical gate plus an independent reviewer, sends rework
back to the same agent, integrates what passed on a separate branch, and reports per wave.

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
itself. Role files live in `skill/claude/agents/` and `skill/codex/agents/`; the sandbox is enforced
by the harness, not by the prompt.

## Measuring a run

```sh
python3 evals/codex_usage.py --list --date 2026-09-12   # sessions that spawned subagents
python3 evals/codex_usage.py --latest                   # per thread, per model, cache rate, rate-limit window delta
python3 evals/score.py <run-dir>                        # protocol completeness of a run directory
```

## How it works

1. **Gate and intake**: decide root-only versus delegated; read the repo's agent instructions and gate
   commands; explorers map each task area; one worktree per task with dependencies fetched; a baseline gate run.
2. **Plan**: a table of tasks, scopes, tiers, dependencies, gates and reviewer tiers.
3. **Specs**: one file per task from `skill/references/spec-template.md`, absolute paths, a
   definition of done with a scoped gate, a mandatory report format.
4. **Dispatch**: a wave of subagents; dependent tasks wait and are scaffolded on the patches they need.
5. **Verify**: the orchestrator runs the gate itself, then an independent reviewer checks the diff
   against the spec and re-runs the gate.
6. **Rework**: the same agent gets the defects verbatim; the same reviewer re-checks; two rounds, then
   escalation one tier up.
7. **Integrate**: patches applied on a dedicated branch, the full gate once, the analyzer compared
   against the baseline list, a final review of the whole diff.

## Repository layout

```
skill/                 the skill: SKILL.md, references/, scripts/, agents/openai.yaml
skill/scripts/         gate.py (gate runner and analyzer delta), integrate.sh, prepare_worktrees.sh
skill/claude/agents/   named roles for Claude Code; skill/codex/agents/ and config.example.toml for Codex
evals/                 score.py (protocol completeness), codex_usage.py (usage and rate-limit attribution), task archetypes, dated results
examples/              a brief, a plan, a spec, a reviewer brief, a verdict, a status trail, a final report
tests/                 unit tests for the scripts and repository hygiene (English only, no personal paths)
AGENTS.md              rules for agents working on this repository; CLAUDE.md imports it
install.sh             user-scope install for both harnesses
```

## Versioning

Semantic versioning. The version lives in `skill/SKILL.md` (frontmatter `metadata.version`), in
`VERSION`, and as the newest entry of `CHANGELOG.md`; a unit test keeps the three in sync. Every
release is an annotated git tag `vX.Y.Z` created with `make tag`. Runs report the skill version in
their final report, so eval results are comparable across versions.

## License

MIT.
