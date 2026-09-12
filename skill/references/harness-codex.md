# Codex specifics

- Invocation: `$orchestrate <task>` in the CLI or IDE. `agents/openai.yaml` sets
  `policy.allow_implicit_invocation: false`, so the skill never triggers on its own.
- Non-interactive launch for an autonomous run (the orchestrator must be able to spawn subagents,
  so use a model and effort from the orchestrator tier):

  ```sh
  codex exec --skip-git-repo-check -C <parent dir of the worktrees> --add-dir <home> \
    -s workspace-write -c sandbox_workspace_write.network_access=true \
    -c agents.max_concurrent_threads_per_session=8 \
    -m gpt-6-astra -c model_reasoning_effort=xhigh \
    -o <run dir>/final-message.md '$orchestrate Run the brief at <absolute path>. Read the skill first.'
  ```

  `exec` has no approval flag; an action that needs a fresh approval fails, so give the sandbox every
  path the run writes to (`--add-dir`) and network access. Run it detached (`nohup ... &`) and watch
  `status.md`; a run of five to six tasks takes about an hour.
- Spawn: `spawn_agent` with `model` and `reasoning_effort` per call; explicit values override
  `agents.default_subagent_model` and `agents.default_subagent_reasoning_effort`.
- Wait: `wait_agent`. Resume: `send_input` per the documentation; some versions expose
  `followup_task` and `send_message` instead. Close: `close_agent` when exposed; otherwise say so.
- Run directory: `~/.agents/orchestrate/runs/<date>-<slug>/`. Do not put it under `~/.codex/`, the
  sandbox protects that directory and every write needs an escalation.
- Effort: the model catalogue lists `low`, `medium`, `high`, `xhigh` for the tier models; the table in
  `SKILL.md` gives the measured defaults.
- If the `codex` binary on `PATH` hangs when run without a terminal, try the binary bundled with the
  desktop application; the CLI prints a version string instantly when it works.
- Usage: token counts are not exposed to the orchestrator. Record thread names and session ids in
  `status.md`; attribute usage afterwards from the local session logs.
- Install: symlink or copy `skill/` to `~/.agents/skills/orchestrate/` (user scope) or
  `.agents/skills/orchestrate/` in a repo. `./install.sh` does the user scope.
