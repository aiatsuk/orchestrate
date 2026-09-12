# Routing: measured results and open hypotheses

Two runs on the same repository, a Flutter mobile application with a canon-alignment backlog,
on 2026-09-12. Same protocol, two harnesses, comparable task archetypes, one data point per cell.
Task archetypes are defined in `evals/tasks/`.

## Claude Code run (5 tasks, 4 implementers + 5 reviewers + 1 final review)

| Archetype | Implementer | Reviewer | Rework | Outcome | Tokens impl / review |
|-----------|-------------|----------|--------|---------|----------------------|
| T1 mechanical unit test (43-line class) | haiku | sonnet | 0 | pass | 61k / 63k |
| T2 structural move (12 files, 10 imports) | opus | opus | 0 | pass, fewest tool calls | 82k / 45k |
| T3 multi-file behaviour-preserving refactor (4 files) | sonnet | sonnet | 0 | pass, found a non-code site itself | 90k / 78k |
| T4 async test (stream subscription, skip, dispose) | sonnet | sonnet | 1 | non-discriminating assertion, fixed | 184k / 89k |
| T5 dependent docs sync (9 files) | sonnet | sonnet | 2 | both rounds caused by the orchestrator | 147k / 86k |
| integrated review | | opus | | pass, found two cross-task issues | 97k |

Totals: haiku 61k, sonnet 737k, opus 224k. Three of four rework rounds were orchestrator errors
(a grep gate scoped too widely; a dependent worktree scaffolded on one predecessor instead of all).

## Codex run (6 tasks, 6 implementers + 6 reviewers + 1 final review, 54 minutes)

Tiers: Luna = gpt-5.6-luna medium, Sol = gpt-5.6-sol medium, Astra = gpt-6-astra low,
orchestrator gpt-6-astra xhigh. Cost is the API-equivalent estimate from local logs.

| Archetype | Implementer | Reviewer | Rework | Outcome | Cost impl + review |
|-----------|-------------|----------|--------|---------|--------------------|
| T1 mechanical unit test (73-line class) | Luna | Sol | 1 | three gaps found by the reviewer, fixed | $0.07 + $0.9 |
| T4 async test (BLoC stream) | Sol | Astra | 0 | pass | $1-2 + $1 |
| T3 multi-file refactor + localization (15 files) | Sol | Astra | 0 | pass | $2 + $1 |
| T2 structural retirement (8 callers, class deleted) | Astra | Astra | 0 | pass | $4 + $1.6 |
| T5 dependent docs sync (7 files) | Sol | Astra | 1 | one wording fix | $0.5 + $1 |
| lint follow-up on the integrated tree | Luna | Sol | 0 | pass | $0.02 + $0.3 |
| integrated review | | Astra | | pass | $0.6 |

Totals: orchestrator $19.8 (117 requests), subagents about $16. Zero orchestrator-caused reworks;
the orchestrator compared analyzer issues against the baseline list, caught a new info-level issue,
and opened a task for it.

## What held across both runs

- Tier 1 works only with an exact reference file, a runnable check and a reviewer one tier up.
  haiku passed at round zero; Luna needed one round on a comparable task.
- Tier 3 was the best value on structural work and as a reviewer: zero rework, complete reports,
  and the only tier that found cross-task problems in the integrated diff.
- The spec moved cost more than the tier did. The discriminating-test criterion in the definition
  of done coincided with a round-zero pass on the async test in the second run.
- A resumed reviewer for round two cost 10k to 18k tokens against 60k to 80k for a fresh one.

## Hypotheses to confirm before promoting into the table

1. Tier 3 as the default reviewer for tier 2 and 3 work; tier 2 reviewers only for tier 1 work.
2. Async and timer tests: tier 2 with the discriminating criterion, tier 3 without it.
3. An orchestrator at the top effort pays for itself on runs of five or more tasks by avoiding
   spec-caused rework; measure on a run of two or three tasks.
