# 2026-11-10: lower first-attempt efforts, a middle tier on the next model up, and the top rung

The published model guide (see `2026-11-10-model-guide.md`) suggests three things this table had not
measured: run the first attempt lower and keep the higher level for what fails; past `high`, try the
next model up at a lower effort; and use the top model at `medium` above tier 3. This file measures
them on the same per-task replay as `2026-10-24-effort-per-tier.md`.

## Method

- Repository, base commit and task specs: the same Flutter application and the 2026-09-13 specs as
  on 2026-10-24: `task-4.md` (T4, an async BLoC test, the tier 2 archetype) and `task-2.md` (T2,
  moving profile loading out of an 830-line page into a BLoC, the tier 3 archetype), with only the
  worktree paths replaced.
- One cell = one headless implementer (`claude -p`, CLI 2.1.321, project settings only, no MCP
  servers), the model with an explicit `--effort`, the `orchestrate-implementer` role text followed
  by the spec in the prompt, tools limited to file edits, reads and `just` recipes. Each cell started
  from a clean worktree.
- Gate: the spec's definition-of-done commands, run by the driver after the implementer, judged by
  exit code. Review: one fixed reviewer for every cell, opus at `high`, the 2026-09-13 reviewer
  brief, read-only tools plus the gate recipes; it reruns the gates and answers `VERDICT: PASS |
  FAIL`. A failed cell would have got one rework round one level up; none failed.
- Costs are the CLI's reported `total_cost_usd`.

## Results

| Archetype | Model, effort | Verdicts | Gate | Implementer cost | Implementer time | Turns | Output tokens |
|---|---|---|---|---|---|---|---|
| T2 structural move | opus, high (control) | PASS | 8/8 | $3.20 | 776 s | 100 | 58k |
| T2 structural move | opus, medium | PASS, PASS | 8/8, 8/8 | $2.17, $2.62 | 444 s, 526 s | 49, 63 | 40k, 48k |
| T2 structural move | fable, medium | PASS, PASS | 8/8, 8/8 | $4.36, $4.28 | 527 s, 516 s | 60, 69 | 49k, 47k |
| T4 async test | sonnet, high | PASS, PASS, PASS | 3/3 x3 | $0.38, $0.26, $0.36 | 89 s, 92 s, 112 s | 20, 18, 24 | 9k, 9k, 10k |
| T4 async test | sonnet, medium | PASS, PASS | 3/3, 3/3 | $0.37, $0.29 | 177 s, 115 s | 28, 31 | 11k, 10k |
| T4 async test | opus, medium | PASS, PASS | 3/3, 3/3 | $0.73, $0.67 | 118 s, 106 s | 22, 18 | 11k, 10k |

Reviewer cost per cell: $0.36 to $0.44 on T4, $0.75 to $1.05 on T2. Whole measurement, 12 cells
with reviews: $26.93 ($19.70 implementers, $7.23 reviewers).

- Comparability with 2026-10-24. The T2 control at `high` matched that day's cells ($3.20 against
  $3.15 and $3.16; 58k output tokens against 58k and 60k). The T4 cells did not: sonnet at `high`
  cost $0.26 to $0.38 here against $0.43 and $0.45 then, in about half the turns, because this
  driver puts the spec into the prompt. So T4 costs are compared within a day only.
- T2 at `medium`: both diffs passed the fixed reviewer with no defect. Against the same-day control
  the implementer cost about 25% less and finished in about 37% less time.
- T4 at `high`: three passes. With 2026-10-24 (one FAIL for a test outside the spec's list, one
  PASS) that is four of five. `medium` also passed twice, but took more turns and more time than
  `high` and was no cheaper.
- T4 on opus at `medium`: two passes at about twice the cost of sonnet at `high`.
- T2 on fable at `medium`: two passes at 1.35 times the control and 1.8 times opus at `medium`.

## Decisions (routing table, Claude Code)

- Tier 3 opus: implementers at `medium` (two agreeing passes under the same reviewer). Reviews by
  this tier stay at `high`, the level of the fixed reviewer in every cell here and on 2026-10-24:
  the tier names `review_effort: high`.
- Tier 2 sonnet: implementers at `high` (three agreeing passes; four of five with 2026-10-24, whose
  same-day pair put `high` 29% under `xhigh`). Reviews by this tier stay at `xhigh`, the level they
  ran at before; a tier 2 reviewer was not measured here. A failed first attempt costs one rework
  and one more review, about $0.75 on this archetype, so `high` stays ahead of `xhigh` up to a
  failure rate of about one in four.
- Tier 2 does not move to opus at `medium`: the same verdicts for twice the cost.
- The top rung, fable at `medium`, passes tier 3 work; it stays the rung above tier 3 only.

## Limits

One repository, one archetype per tier, two or three repetitions, one control cell. No cell failed,
so the rework path at a higher level was not exercised and its cost above is an estimate. The top
rung was measured on a task tier 3 passes, not on one tier 3 failed. The reviewer judges against
the spec and reasons about discriminating tests rather than mutating the code.
