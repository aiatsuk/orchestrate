# Changelog

All notable changes to the skill. The format follows Keep a Changelog; versions follow semantic versioning.

## [0.3.1] - 2026-09-13

### Fixed
- Codex ignores symlinked custom-agent files; `install.sh` now copies the Codex roles and links only the Claude ones. The Codex spawn parameter for a role is `agent_type`.
- The delegation gate distinguishes a rejected role name (spawn again without the role, instructions inlined) from an unavailable spawn tool (report and stop). Found when an eval run stopped at Phase 0.
- Codex run directory moved to `~/.local/share/orchestrate/runs/`; `~/.agents` is sandbox-protected like `~/.codex`.
- `prepare_worktrees.sh` flattens a prefix containing `/` into the directory name (`feature/run` gives `feature-run-<slug>` on branch `feature/run/<slug>`).

## [0.3.0] - 2026-09-13

### Added
- Named roles for both harnesses: `orchestrate-explorer` (read-only, tier 1), `orchestrate-implementer` (workspace-write), `orchestrate-reviewer` (read-only, tier 3), with `install.sh` linking them into `~/.claude/agents` and `~/.codex/agents`.
- `evals/codex_usage.py`: attribution of one Codex run from the rollout logs, per thread and model, with cache hit rate and the rate-limit window delta labelled by window length.
- Delegation gate (root-only versus delegated, real spawns only) and a completion gate before the final report.
- Explorer phase: task areas are mapped by read-only tier-1 agents before specs are written, keeping the orchestrator context small.
- Orchestration-overhead protocol in `evals/README.md` with the 2026-09-12 reference point.
- `install.sh --repo <path> [--force]`: project-scope install that appends an orchestration block to `AGENTS.md` once.
- `skill/codex/config.example.toml` with the `[agents]` keys the skill relies on; `service_tier` noted as an option.

### Changed
- Codex orchestrator effort from `xhigh` to `high`; the 2026-09-12 measurement at `xhigh` stays as the baseline.

## [0.2.0] - 2026-09-12

### Changed
- Codex tier 1 (`gpt-5.6-luna`) and tier 2 (`gpt-5.6-sol`) run at `xhigh` reasoning effort. The 2026-09-12 measurements were taken at `medium` and remain in `skill/references/routing.md` as the baseline.

### Added
- Versioning: `metadata.version` in the skill frontmatter, the `VERSION` file, this changelog, a consistency test, and `make tag`.
- The final report of a run states the skill version.

## [0.1.0] - 2026-09-12

### Added
- The unified skill for Claude Code and Codex with references, scripts, evals, examples, tests, installer and CI.
