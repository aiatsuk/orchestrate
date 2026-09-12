# Brief for the orchestrator: canon backlog, wave of four plus docs

## Repo facts
- Repository: <repo path>, a Flutter app in `mobile_app/`, `just` recipes at the repo root.
- Base branch for every worktree: `feature/canon` at commit `<sha>`. Do not edit the existing checkout;
  create worktrees with `skill/scripts/prepare_worktrees.sh --repo <repo> --base <sha> --parent <parent> --prefix run --deps "just get"`.
- Gate recipes (from a worktree root): `just analyze`, `just analyze-dir <dir>`, `just test-file <path>`,
  `just format-check`, `just docs-lint`. Full suite `just test` takes about a minute; run it once on the
  integrated tree.
- Forbidden for subagents: `just verify`, `just codegen`, `just format`, bare SDK commands.
- Baseline on the clean base: analyzer exits 0 with 4 pre-existing info-level issues. Re-measure and record.
- Routing and conventions: `docs/model-routing.md`, `docs/testing.md`, `docs/error-handling.md`.
  Doc rules: `docs/doc-maintenance.md` makes doc updates part of the definition of done.

## Tasks (write the specs; suggested tiers in brackets, justify the final choice in plan.md)
1. [tier 1] Unit test for `SmallManagerImpl` (`lib/features/small/small_manager.dart`, 70 lines, no timers or streams).
   Form reference: `test/features/other/other_manager_test.dart`.
2. [tier 2] Unit test for `SectionsBloc` (`lib/presentation/sections/bloc/sections_bloc.dart`). BLoC reference:
   `test/presentation/profile/profile_bloc_test.dart`. Every test must fail if the behaviour it names is removed.
3. [tier 2] Error-reporting convention follow-ups in three files: pass the caught error instead of a string literal,
   add the component name, replace a user-visible `error.toString()` with a localized generic message.
4. [tier 3] Retire the legacy `DebugLogger` class: migrate its eight callers to `AppLogger`, then delete it.
   Behaviour must not change; scoped tests must stay green.
5. [your call] Per the doc rules, a dependent docs task after 3 and 4.

## Constraints for this run
- No commits except a temporary scaffolding commit by path on a throwaway branch for a dependent task. No push, no pull requests.
- Integration worktree `<parent>/run-integration` on branch `run/integration`; leave it staged and uncommitted.
- Run directory: `<run dir>`. Keep `status.md` updated after every event; write `final-report.md` at the end.
