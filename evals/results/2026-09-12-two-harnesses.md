# 2026-09-12: the same protocol on two harnesses

Repository: a Flutter mobile application with a canon-alignment backlog and a `just` gate
(analyzer, scoped tests, format check, docs lint). Base commit identical for both runs.
Full data and the hypotheses derived from it: `skill/references/routing.md`.

| Harness | Tasks | Passed | Rework rounds | Orchestrator-caused rounds | Wall clock | Cost |
|---------|-------|--------|---------------|----------------------------|------------|------|
| Claude Code (haiku / sonnet / opus, orchestrator at the top tier) | 5 | 5 | 4 | 3 | about 2.5 h including operator latency | about 1.0M subagent tokens |
| Codex (Luna / Sol / Astra low, orchestrator Astra xhigh) | 6 | 6 | 2 | 0 | 54 min autonomous | about $36 API-equivalent |

Protocol completeness (`evals/score.py`) was not yet available for these runs; both run directories
contain every artifact the scorer checks, verified by hand.

Notes:
- Tier 1 needed a rework round on Codex and none on Claude Code for comparable tasks.
- Tier 3 passed at round zero on both harnesses and was the only tier that found cross-task issues
  in the integrated diff.
- The orchestrator at the top effort on Codex caused no rework; the orchestrator on Claude Code caused
  three of four rounds through spec and scaffolding mistakes. Those mistakes are now rules in the skill.
