# Evals

Two kinds of measurement, both cheap enough to repeat after every change to the skill.

## 1. Protocol completeness (automatic)

`score.py <run-dir>` checks a run directory for the artifacts the protocol requires: a plan with a
task table, a reviewer brief, a status trail, a final report, and per task a spec with every
required section, a review with a verdict, and an exported patch after a pass. It prints a score in
[0, 1] and the failed checks. Run it on every real run; a score below 1 means the orchestrator
skipped part of the protocol, whatever the code looks like.

```sh
python3 evals/score.py ~/.claude/orchestrate/runs/<date>-<slug>
python3 evals/score.py ~/.agents/orchestrate/runs/<date>-<slug> --json
```

## 2. Tier fitness (manual cells)

A cell is one task archetype from `tasks/` run through the skill on one harness with the routing
table as written. Record per task: implementer tier, reviewer tier, rework rounds, verdict, gate
result, duration, and tokens or cost when the harness reports them. The archetypes are chosen so
that each tier has a task it should pass and a task it should fail:

| Archetype | Expected pass at | Discriminates |
|-----------|------------------|---------------|
| T1 mechanical unit test | tier 1 | whether an exact reference plus a runnable check is enough |
| T2 structural move | tier 3 | tool-call economy and report completeness on many files |
| T3 multi-file behaviour-preserving refactor | tier 2 | judgment on sites that must not change |
| T4 async test | tier 2 with the discriminating criterion | assertion strength, not just green tests |
| T5 dependent docs sync | tier 2 | truthfulness against the scaffolded code |

Write the results as a dated file in `results/`. Promote a hypothesis from
`skill/references/routing.md` into the routing table only after a second run agrees.

## How to run a cell

1. Pick a repository with a real backlog and a scoped gate (analyzer, tests, format).
2. Write a brief like `examples/brief.md`: repo facts, gate commands, tasks with pointers, constraints.
3. Invoke the skill with the brief; for an autonomous Codex run use the launch line in
   `skill/references/harness-codex.md`.
4. Verify the integrated result yourself: run the full gate, check no AI or tool names in the diff.
5. Score the run directory, then fill the results table.
