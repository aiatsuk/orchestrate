# Rules for agents working on this repository

## Setup

1. Prerequisites: `git` 2.30 or newer, `python3` 3.10 or newer. No Python packages are required.
2. `./install.sh` links `skill/` into `~/.claude/skills/orchestrate` and `~/.agents/skills/orchestrate`
   so both harnesses load the same file. `./install.sh --uninstall` removes the links.
3. `make test` runs every unit test with the standard library test runner. Run it before and after
   any change. `make check` runs the hygiene tests only.
4. To validate a real run of the skill, score its run directory: `python3 evals/score.py <run-dir>`.

## Rules

- English only, everywhere: docs, code, comments, commit messages. The hygiene test fails on any
  Cyrillic character.
- No personal absolute paths (`/Users/<name>/`, `/home/<name>/`) anywhere; use `<worktree>`,
  `<run dir>` or `~` placeholders. The hygiene test enforces this.
- No AI or tool names in commit messages and no attribution trailers. The skill's own text may name
  the harnesses it supports; nothing else may.
- `skill/SKILL.md` is harness-agnostic. Anything specific to one harness goes into
  `skill/references/harness-<name>.md`. Keep `SKILL.md` under 250 lines.
- Every script under `skill/scripts/` and `evals/` has a unit test under `tests/` that exercises its
  success path and at least one failure path, using temporary directories and repositories only.
- Routing changes require evidence: add a dated file under `evals/results/` first, and promote a
  hypothesis from `skill/references/routing.md` into the routing table only after two runs agree.
- Examples under `examples/` are anonymized: placeholder repository names, no real identifiers.
- Commit messages follow `type(scope): summary` in the imperative, one change per commit.

## Definition of done for a change

- [ ] `make test` passes.
- [ ] If a script changed, its test changed with it.
- [ ] If the protocol changed, `SKILL.md`, the affected reference file, and the matching example agree.
- [ ] If the routing table changed, the evidence file exists.
