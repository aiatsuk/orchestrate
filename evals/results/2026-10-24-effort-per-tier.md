# 2026-10-24: Claude Code effort per tier (per-task replay)

The Claude tiers in `routing.json` carried `effort: null`, so every implementer and reviewer ran at
the session's effort level, which on the measuring machine is `xhigh` (user settings). This file
measures each tier's model at lower effort on the archetype it is meant to pass, holding everything
else fixed, to decide whether the table can name an effort per tier.

## Method

- Repository and tasks: the Flutter application of the 2026-09-13 runs, base commit identical to
  that run. The task specs are the 2026-09-13 run's own `task-1.md` (T1, two manager tests),
  `task-4.md` (T4, an async BLoC test) and `task-2.md` (T2, moving profile loading out of an
  830-line page into a BLoC with characterizing tests), with only the worktree paths replaced.
- One cell = one implementer, headless (`claude -p`, CLI 2.1.301), the tier's model with an explicit
  `--effort`, the `orchestrate-implementer` role text and the spec as the prompt, tools limited to
  file edits, reads and `just` recipes, no MCP servers. Each cell started from a clean worktree.
- Gate: the spec's definition-of-done commands, run by the harness after the implementer, judged by
  exit code. Review: one fixed reviewer for every cell (opus at `high`, the 2026-09-13 reviewer
  brief, read-only tools plus the gate recipes), which reruns the gates and returns
  `VERDICT: PASS | FAIL` with defects.
- Two repetitions per cell (three for T1 at medium, including a smoke run). Costs are the CLI's
  reported `total_cost_usd`.

## Results

| Tier / archetype | Effort | Verdicts | Gate | Implementer cost | Implementer time | Turns | Output tokens |
|---|---|---|---|---|---|---|---|
| 3 opus / T2 structural move | xhigh | PASS, PASS | 8/8, 8/8 | $4.78, $5.27 | 1154 s, 1274 s | 97, 108 | 84k, 103k |
| 3 opus / T2 structural move | high | PASS, PASS | 8/8, 8/8 | $3.16, $3.15 | 1033 s, 1056 s | 79, 74 | 58k, 60k |
| 2 sonnet / T4 async test | xhigh | PASS, PASS | 3/3, 3/3 | $0.72, $0.51 | 421 s, 161 s | 38, 31 | 22k, 17k |
| 2 sonnet / T4 async test | high | FAIL, PASS | 3/3, 3/3 | $0.45, $0.43 | 204 s, 147 s | 46, 34 | 13k, 11k |
| 1 haiku / T1 manager tests | xhigh | PASS, PASS | 5/5, 5/5 | $0.03, $0.04 | 133 s, 177 s | 24, 35 | 22k, 26k |
| 1 haiku / T1 manager tests | high | PASS, PASS | 5/5, 5/5 | $0.02, $0.02 | 101 s, 92 s | 27, 19 | 11k, 8k |
| 1 haiku / T1 manager tests | medium | PASS, PASS, PASS | 5/5 x3 | $0.02, $0.02, $0.01 | 146 s, 70 s, 74 s | 17, 19, 22 | 5k, 6k, 6k |

Reviewer cost per cell: $0.32 to $0.95. Whole measurement, 15 cells with reviews: $26.27 ($18.61 implementers, $7.66 reviewers).

- T2 at high: both diffs moved the behaviour faithfully by the reviewer's reading (964 and 1060
  diff lines against 984 and 1138 at xhigh; six files each); no defect in any T2 cell.
- T4 at high, first repetition: the only defect of the measurement. The file carried an eighth test
  ("the localized failure texts differ from each other") that names no BLoC behaviour and cannot
  fail on a change to the handler, against the spec's "every test fails if the behaviour it names is
  removed" and its list of seven tests. Minor, but the scope rule counts it.
- T1: every cell passed. Medium and high took about the same time (medium 146, 70 and 74 s, the
  first being the smoke run; high 101 and 92 s); xhigh took longer (133 and 177 s).

## Decision (routing table, Claude Code)

- Tier 1 haiku: `medium` (three agreeing passes, one of them the smoke run; no slower than high and
  faster than xhigh; cost unchanged at one to two cents).
- Tier 3 opus: `high` (two agreeing passes at the same quality; about a third cheaper and 10% and 17%
  faster than xhigh). The tier 3 reviewer follows the tier, so reviews also run at `high`, the level
  of the fixed reviewer that found the only defect here.
- Tier 2 sonnet: `xhigh`, named explicitly. High disagreed with itself (one FAIL in two), so it is
  not promoted; naming the tested level keeps the tier at it when the session level is lowered.
- Adversary (sonnet) and the explorer role are unchanged: not measured here.

## Limits

One repository, one archetype per tier, two repetitions; the reviewer judges against the spec and
reasons about discriminating tests rather than mutating the code. The implementer's report was cut
at 6000 characters in the reviewer's prompt by the measuring harness, which affected the two xhigh
T2 cells (their reports were longer); the reviewer verified the code itself in every cell.
