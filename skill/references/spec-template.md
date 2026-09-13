# Task <#>: <title>

## Goal
<One paragraph: what changes and why. Name the observable outcome.>

## Repo and location
- Repo: <absolute path>
- Branch: <name>
- You work in your own worktree: <absolute path>. Do not cd outside it. Do not touch other checkouts.
- Read first, in this order: <files>
- Copy patterns from: <one existing file that does the same kind of thing>

## Change
1. <concrete step with file path>
2. <...>
Interfaces that must not change: <list or "none">

## Constraints
- Do not edit: <paths>
- Do not commit, push, create pull requests, upload, or edit files outside the scope above.
- Do not put AI or tool names into anything you produce: code, comments, docs, messages.
- Forbidden recipes: <full-gate, code generation, format-all recipes of this repo>
- <rules from the repo's agent instructions that apply to this task>

## Definition of done
- [ ] <observable criterion>
- [ ] <observable criterion>
- [ ] (tests) each test fails if the behaviour it names is removed; the report says, per test, what change would make it fail
- [ ] `<scoped gate command>` passes with zero new warnings (baseline: <n> pre-existing, listed in status.md)

## Finish
Run `git add -A` inside your worktree, then `git status --porcelain`: every listed path must be
inside the scope above. Unstage and delete anything else (logs, scratch files) before you report.
Do not commit.

## Report (mandatory, in this order)
1. Files changed, as a list with one line each on what changed.
2. Gate output: the last 30 lines of every gate command; if the output is dominated by log noise, filter it, quote the summary lines verbatim, and say that you filtered.
3. What you could not do, and why.
4. Open questions for the orchestrator.
