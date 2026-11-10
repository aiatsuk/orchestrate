# 2026-11-10: the published model guide against this skill's routing

The vendor published a model selection guide on 2026-11-09, the day Fable 5.2 became the top
generally available Claude model. This file checks the Claude Code column of the routing table and
the skill's frontmatter against it, with the harness documentation and local usage data, and records
what changed in 0.10.0 and what stays a hypothesis until it is measured.

## Sources

- "Which Claude model should you build with?", 2026-11-09:
  `https://claude.dev/blog/which-claude-model-should-you-build-with/`.
- Claude Code documentation (model configuration, subagents, skills, prompt caching) and the
  pricing page, read on 2026-11-10 with CLI 2.1.321.
- Local session transcripts of the measuring machine, the 13 days to 2026-11-10, priced at list
  rates (API-equivalent estimates, not invoices).

## What the guide says that bears on the table

- Start on opus at `medium`; move down to sonnet for well-scoped work and to haiku for extraction
  and side-task subagents; move up to fable for major refactors and migrations, architecture
  planning, leading many subagents and long unattended runs.
- Effort is the finer dial. On SWE-bench Pro, opus at `medium` scored about 2.5 points under `high`
  for about 70% of the cost; `low` about 8 points under for a third; `xhigh` about 1.4 points over
  for 2.5 times the cost. Past `high`, test the next model up at a lower effort instead.
- When a model falls short: first give it a way to check its own work, then raise effort one level,
  then move to the next model up at a lower effort than the smaller one used.
- With an automatic outcome check, run everything low and re-run only the failures high: opus at
  `low` with failures re-run at `high` passed about 97% of tasks for $0.17 each, against 95.3% for
  $0.29 with every task at `high`.
- A lead model over cheaper parallel workers pays off for work larger than one context window (an
  opus lead over sonnet workers cost about a third less than opus alone for the same score); skip it
  for one chain of dependent steps or work that fits one window.
- Keep small models on work where a mistake is cheap to spot; keep edits and judgment on the bigger
  model. A lean main context matters more the bigger the main model is.
- The prompt cache belongs to the model that wrote it: the first turn on another model writes the
  whole conversation again, and a move down a tier loses the earlier reasoning.

List prices per million tokens (input / cache read / output): haiku 0.10 / 0.01 / 0.50 up to a
100k-token prompt and five times that above it; sonnet 2 / 0.10 / 10; opus 4 / 0.20 / 20;
fable 8 / 0.20 / 40. Sonnet's cache read is half of opus's, so every cost component of sonnet is
half of opus; an earlier local estimate that sonnet saved only about a quarter on this workload used
a cache-read rate of 0.20 and was too low.

## Harness facts checked

- A skill's `model` and `effort` frontmatter applies to the current turn only. The execution loop
  runs in the background, so every later turn of a run (a task notification, a user message) is on
  the session's model whatever the skill pins.
- A subagent definition accepts `effort`. A subagent without one runs at the session's level only
  when it is on the session's model; on another model the level is not documented.
- The built-in explorer runs on the session's model. A user or project agent named `Explore`
  replaces it and keeps its own `model`. A live run confirmed it: with a sonnet session, an
  `Explore` agent pinned to haiku answered on haiku.
- The default subagent model variable is only a default; a role's `model` or a passed `model` wins.
- Without a compaction window a 1M-context model compacts at about 967k tokens.

## Local measurements

- Main threads cost about $84 a day; 76% of that came from calls with more than 300k tokens of
  context, and the costliest sessions averaged 390k to 540k tokens per call.
- One observed switch from opus to fable at 258k tokens of context wrote all 258k tokens to the
  cache again in a single call ($2.6 at the five-minute rate, $4.1 at the one-hour rate).
- `/orchestrate` was typed three times in the last 30 days, each time in a fresh session (median
  15k tokens of context) whose model was already opus, so the 0.9.0 pin never changed a model.
  Since 2026-11-09 the default session model on the measuring machine is fable: the same pin would
  now move the planning turn, the most judgment-heavy part of a run, one tier down, and the rest of
  the run back up.

## Decisions for 0.10.0

1. The orchestrator runs on the session's model: frontmatter `model: inherit`, effort `high`
   unchanged. The user picks the model once, at the start of a fresh session: fable for a run that
   leads many agents or a major migration, opus otherwise; a session below opus is refused.
2. The rung above tier 3 is named: fable at `medium`, one subagent, once. It follows the guide's
   rule (the next model up at a lower effort than the failed attempt). By decision, not yet by a
   local measurement; hypothesis 7 in `skill/references/routing.md` covers it.
3. The tier rows change only by measurement: the cells listed below were run the same day and are
   in `2026-11-10-tier-efforts.md`, with the decisions they support.

## Considered and not adopted

- Reviews on sonnet (the guide lists repeated review work under sonnet at `medium`): two local runs
  favour the tier 3 reviewer, the only one that found cross-task defects. The corrected price makes
  one measurement worthwhile for tier 1 and 2 work (hypothesis 9).
- Planning on a big model and editing on a small one without a check: the tiers already do this,
  with a gate and an independent review behind every edit.
- An advisor model beside the implementer: the guide's own table shows no clear win.

## Cells measured the same day

Same replay method as `2026-10-24-effort-per-tier.md` (headless implementer, fixed reviewer, two
repetitions per cell).

| Cell | Question | Compare with |
|---|---|---|
| T4 async test, opus `medium` | Does the next model up at a lower effort beat sonnet `xhigh`? | sonnet `xhigh`: 2 of 2, $0.51 to $0.72 |
| T4 async test, sonnet `high`, rework at `xhigh` on a FAIL | What does a passed task cost when only failures run high? | the same |
| T2 structural move, opus `medium` | Does `medium` hold on tier 3 work at about 70% of the cost? | opus `high`: 2 of 2, $3.15 |
| T2 structural move, fable `medium` | What does the top rung cost and does it pass? | opus `high` |

Results and decisions: `2026-11-10-tier-efforts.md` (twelve cells, $26.93).
