# Task 4: Retire the legacy DebugLogger

## Goal
`DebugLogger` is a legacy adapter that predates `AppLogger`. Eight production files still call it.
Migrate every call to `AppLogger.instance.event(...)` per the error-handling document and delete the
class. Log format may change; behaviour must not.

## Repo and location
- Repo: <repo path>
- Branch: run/retire-logger
- You work in your own worktree: <parent>/run-retire-logger. Do not cd outside it. Do not touch other checkouts.
- Read first, in this order:
  1. <worktree>/docs/error-handling.md (the reporting table: which call for which situation)
  2. <worktree>/lib/shared/logging/app_logger.dart (the `event` signature)
  3. <worktree>/lib/shared/analytics/debug_logger.dart (the class to retire)
- Copy patterns from: <worktree>/lib/features/config/config_manager.dart (already on AppLogger)

## Change
1. `rg -l "DebugLogger" lib test` from the worktree root; the spec expects eight files, report any difference.
2. For each call, map `logError(tag, action, error, stackTrace, payload)` to
   `AppLogger.instance.event('<category>.<action>', level: error, component: '<ClassName>', fields: payload, error: e, stackTrace: st)`
   and `logState(...)` to the same call at level info. Keep every payload key.
3. Remove the barrel export in `lib/shared/analytics/analytics.dart`.
4. Delete `lib/shared/analytics/debug_logger.dart`.
Interfaces that must not change: public method signatures of the eight callers.

## Constraints
- Do not edit files other than the eight callers, the barrel file and the deleted class.
- Do not commit, push, create pull requests, upload, or edit files outside the scope above.
- Do not put AI or tool names into anything you produce.
- Forbidden recipes: `just verify`, `just codegen`, `just format`.

## Definition of done
- [ ] `rg -n "DebugLogger|debug_logger" lib test` returns nothing.
- [ ] Every migrated call keeps its payload keys (list them per site in the report).
- [ ] `just analyze-dir lib/data`, `just analyze-dir lib/features`, `just analyze-dir lib/shared` report no new issues (baseline: 4 pre-existing infos in status.md).
- [ ] `just test-file test/features/stash` and `just test-file test/data` pass.
- [ ] `just format-check` passes.

## Finish
Run `git add -A` inside your worktree so new files show in `git diff --cached`. Do not commit.

## Report (mandatory, in this order)
1. Files changed, as a list with one line each on what changed.
2. Gate output: the last 30 lines of every gate command; filter log noise and say so.
3. What you could not do, and why.
4. Open questions for the orchestrator.
