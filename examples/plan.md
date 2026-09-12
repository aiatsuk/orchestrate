# Plan

Assumptions: non-interactive; base pinned to `<sha>`; routing from the repo's own document; only
task 3 may regenerate localization; only task 5 may regenerate agent-config copies.

| # | Task | Scope (files, dirs) | Tier | Depends on | Gate | Reviewer tier |
|---|------|---------------------|------|------------|------|---------------|
| 1 | SmallManagerImpl unit test | test/features/small/ (new file) | 1 | baseline | test-file; analyze-dir; format | 2 |
| 2 | SectionsBloc unit test | test/presentation/sections/ (new file) | 2 | baseline | test-file; analyze-dir; format | 3 |
| 3 | Error-reporting follow-ups | 3 production files, 2 ARB files, generated l10n | 2 | baseline | analyze-dir x3; scoped tests; l10n; format | 3 |
| 4 | Retire DebugLogger | 8 callers + deleted class | 3 | baseline | analyze-dir x4; scoped tests; legacy scan; format | 3 |
| 5 | Docs sync | backlog, changelog, canon lines, regenerated copies | 2 | 1, 2, 3, 4 | docs-lint; config check; canon scan | 3 |

Cost line: implementers 1 x tier 1, 3 x tier 2, 1 x tier 3; reviewers 1 x tier 2, 4 x tier 3; final review 1 x tier 3.
Integration: branch `run/integration`, worktree `<parent>/run-integration`, staged and uncommitted.
