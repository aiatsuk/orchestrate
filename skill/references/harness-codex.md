# Codex specifics

- Invocation: `$orchestrate <task>` in the CLI or IDE. `agents/openai.yaml` sets
  `policy.allow_implicit_invocation: false`, so the skill never triggers on its own.
- Non-interactive launch for an autonomous run (the orchestrator must be able to spawn subagents,
  so use a model and effort from the orchestrator tier):

  ```sh
  codex exec --skip-git-repo-check -C <parent dir of the worktrees> \
    --add-dir <run dir> --add-dir <dependency caches the gate writes to> \
    -s workspace-write -c sandbox_workspace_write.network_access=true \
    -c agents.max_concurrent_threads_per_session=8 \
    -m gpt-6-astra -c model_reasoning_effort=high \
    -o <run dir>/final-message.md '$orchestrate Run the brief at <absolute path>. Read the skill first.'
  ```

  `exec` has no approval flag; an action that needs a fresh approval fails, so give the sandbox the
  paths the run writes to and nothing more: the worktree parent (`-C`), the run directory, and the
  dependency caches the gate writes to (for Flutter: the pub cache and the SDK cache). Do not add
  the whole home directory. Run it detached (`nohup ... &`) and watch
  `status.md`; a run of five to six tasks takes about an hour.
- Spawn: `spawn_agent` with `model` and `reasoning_effort` per call; explicit values override
  `agents.default_subagent_model` and `agents.default_subagent_reasoning_effort`.
- Wait: `wait_agent`. Resume: `send_input` per the documentation; some versions expose
  `followup_task` and `send_message` instead. Close: `close_agent` when exposed; otherwise say so.
- Run directory: `~/.local/share/orchestrate/runs/<date>-<slug>/`. Do not put it under `~/.codex/` or
  `~/.agents/`: the sandbox protects both and every write there needs an escalation, which `exec`
  cannot surface.
- Effort: the model catalogue lists `low`, `medium`, `high`, `xhigh` for the tier models. The table in
  `SKILL.md` sets tiers 1 and 2 to `xhigh`, tier 3 to `low` and the orchestrator to `high`; the
  2026-09-12 measurements in `routing.md` were taken with tiers 1 and 2 at `medium` and the
  orchestrator at `xhigh`.
- Named roles: `codex/agents/orchestrate-{explorer,implementer,reviewer}.toml` go to `~/.codex/agents/`
  (user) or `.codex/agents/` (project) as real files; symlinked role files are ignored and the spawn
  returns "agent type is currently not available". Spawn with `agent_type: "orchestrate-<role>"`;
  the built-in types `default`, `explorer` and `worker` also exist. `codex/config.example.toml` lists
  the `[agents]` keys the skill relies on. The reviewer and the explorer are `read-only` at the
  sandbox level.
- `service_tier = "fast"` (or `"flex"`) in the config trades price for latency on versions that
  support it; unverified with this skill, remove the line if the CLI rejects it.
- Rate-limit windows are labelled by their `window_minutes` in the rollout logs; on some accounts the
  primary window is 7 days (10080 minutes) and there is no separate 5-hour window. Read the label the
  usage script prints rather than assuming.
- If the `codex` binary on `PATH` hangs when run without a terminal, try the binary bundled with the
  desktop application; the CLI prints a version string instantly when it works.
- Usage: token counts are not exposed to the orchestrator. Record thread names and session ids in
  `status.md`; attribute usage afterwards with `evals/codex_usage.py --latest` (per thread, per model,
  cache hit rate, rate-limit window delta).
- Install: symlink or copy `skill/` to `~/.agents/skills/orchestrate/` (user scope) or
  `.agents/skills/orchestrate/` in a repo. `./install.sh` does the user scope.

## The execution loop on Codex

- Run phases 2 to 4 with the same workflow script Claude Code runs:
  `node <skill>/scripts/run_workflow.js --script <skill>/workflows/orchestrate-execute.js --args <run dir>/execute-args.json`
  where the args file is `scripts/plan.py workflow-args plan.json` for a plan with `"harness": "codex"`.
  Every implementer and reviewer becomes one `codex exec` process with `--output-schema`: implementers
  in `workspace-write` with the task worktree as root, reviewers in `read-only`, the model and effort
  from `args`, the role instructions from `codex/agents/*.toml`. The gate, scaffold and export steps
  do not become agents: the runtime runs those helper commands itself, so they are mechanical here.
- The runtime needs `node` (18 or newer). It starts its own sandboxed `codex` processes, so launch it
  from a terminal or from an orchestrator session that may run it unsandboxed; a nested sandbox is
  expected to fail. `--max-parallel` caps concurrent `codex` processes (default 4). Every `codex exec`
  runs in its own process group and is stopped after `--agent-timeout` seconds in total (default
  1800) or `--idle-timeout` seconds without an event (default 1800; it must exceed the longest gate
  that prints nothing, since an agent running a gate is silent until it ends); a stopped attempt
  counts as a failed one (one retry, then the task is BLOCKED). Relayed helpers stop after
  `--relay-timeout` (default 1800).
- Start checks: every spec's sha256 and every worktree's identity on disk must equal the args
  (regenerate them after editing a spec or recreating a worktree; implementer prompts carry both, so
  work done in a removed worktree is never replayed),
  and process groups recorded in `<run dir>/agents/process-groups.json` by an earlier run must be
  gone; live leftovers refuse the start unless `--kill-leftovers` is given. One runner at a time per
  run directory (`agents/runner.lock`; a stale lock is taken over). SIGINT, SIGTERM and SIGHUP stop
  every child group and write no result file; stopped agents rerun on the next start.
- Results are journaled to `<run dir>/execute-journal.jsonl`; rerunning the same command replays
  finished agents from the journal and continues where the run stopped. The gate, scaffold and
  export steps are never replayed: they run again against the current worktree. Implementer
  prompts carry the spec's sha256 and review prompts also the staged tree, so a changed spec or
  changed content reruns its agents. To rerun some tasks only, pass `plan.py workflow-args … --only
  <id> --previous execute-result.json`; agents whose spec and content did not change replay from the
  journal, so pass a new `--journal` when you want fresh agents for unchanged inputs. The final result goes to
  `<run dir>/execute-result.json`; check it with `plan.py verify-result` as on Claude Code.
- Structured output: the runtime sends Codex a strict variant of each schema (no extra properties,
  every property required) and validates the answer against the original; an invalid answer is
  retried once with the validation errors, then the agent counts as failed and the task is BLOCKED.
- Per-agent event streams go to `<run dir>/agents/`; usage attribution stays `evals/codex_usage.py`.

