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
