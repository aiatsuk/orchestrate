# Changelog

All notable changes to the skill. The format follows Keep a Changelog; versions follow semantic versioning.

## [0.5.0] - 2026-09-13

Second reliability release after the external review of 0.4.1. Each item has a regression test.
The gate's run-directory layout changed, hence the minor bump before 1.0.

### Fixed
- `gate.py`: commands ran under a plain shell, so a failure inside a pipeline (`false | cat`, a test piped through `tee`) counted as success; commands now run under `bash -o pipefail`.
- `gate.py`: log overwrite protection only worked after a completed attempt; an interrupted attempt or two concurrent runs with one label overwrote logs. Each run now reserves an attempt directory atomically (`<label>/`, `<label>-r2/`, ...) with `<n>.log` and `result.json` inside.
- `gate.py delta`: ESLint stylish output puts the file on its own line, so the same issue in a second file, or twice, was not new; issues are now keyed by file and text and counted.
- `integrate.sh verify-clean --scope`: a rename from outside the scope into it passed; the check now sees the old path of a rename.
- `integrate.sh apply`: adoption could overwrite an untracked or ignored file present in the integration worktree; it now refuses and leaves the worktree unchanged.
- `install.sh --uninstall`: a symlink whose target merely started with the skill path counted as ours; ownership is now an exact path or a path inside it.
- CI: the signal test used a nested shell that reports 143 on Linux; the gate is now killed directly.

### Changed
- Documentation no longer calls the Claude Code reviewer read-only: the role lacks Edit and Write but keeps Bash; `verify-clean` after a review is the mechanical check. Codex roles remain sandbox-enforced.

## [0.4.1] - 2026-09-13

### Changed
- Results of the 2026-09-13 two-harness evaluation completed with the addendum on the profile coalescing fix round, recorded as the evidence for the routing hypotheses in `references/routing.md`; no protocol or script change since 0.4.0.

## [0.4.0] - 2026-09-13

Reliability release after an external review of 0.2.0. Every item below has a regression test in
`tests/test_reliability.py` that reproduced the defect first.

### Fixed
- `gate.py`: a command killed by a signal (negative return code) was reported as success; any non-zero code now fails, and the outcome names the signal.
- `gate.py`: re-running a label overwrote earlier logs; repeated labels get `-r2`, `-r3` suffixes.
- `gate.py delta`: ESLint-style `line:col  warning` lines were invisible to the default issue pattern.
- `integrate.sh apply`: patches were checked one by one against the base, so a patch that depended on an earlier one was rejected, and a conflict mid-sequence left the integration worktree half applied with conflict markers. Patches now apply in order on a temporary worktree and the result is adopted whole or not at all; only the patches' files are staged.
- `prepare_worktrees.sh`: the dependency log was written inside the worktree where `git add -A` would stage it; it goes to `--log-dir` (default: next to the worktree).
- `install.sh --uninstall` removed any symlink at the target path; it now removes only links that point into this repository.

### Added
- `integrate.sh verify-clean <worktree> <patch> [--scope <pathspec>...]`: the check before a worktree is removed (staged diff equals the patch byte for byte, nothing unstaged or untracked, staged paths inside the scope). `same-tree` is kept for integration-versus-replay comparison only.
- Reviewer brief: refactors that move async coordination must be checked with same-turn event pairs against fakes that complete synchronously.
- README section on what the scripts enforce and what only the prompt enforces; least-privilege `--add-dir` guidance for Codex.
- Routing evidence: the 2026-09-13 two-harness run in `evals/results/` and `references/routing.md`.

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
