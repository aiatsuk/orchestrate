# Claude Code specifics

- Invocation: `/orchestrate <task>`. The skill is user-invocable only (`disable-model-invocation: true`);
  the task text arrives as `$ARGUMENTS`.
- Spawn: the `Agent` tool with `model` set to the tier's model name, `run_in_background: true`, and a
  `description` carrying the task number so notifications map back to the plan. The agent's final
  report is not shown to the user; relay it.
- Wait: a completion notification arrives per agent. Do not poll; do other independent work.
- Resume: `SendMessage` to the agent by its id. The agent continues with its context intact, which is
  what makes rework and re-review cheap.
- The tool's own worktree isolation needs the session's working directory inside the repo and does
  not fetch dependencies, so prepare worktrees with `scripts/prepare_worktrees.sh` instead.
- Concurrency: the tool allows about 20 concurrent subagents; launch a wave in one message.
- Usage: the completion notification reports tokens, tool uses and duration per agent; record them
  in `status.md`, they tune the routing table.
- Run directory: `~/.claude/orchestrate/runs/<date>-<slug>/`.
- Named roles: `claude/agents/orchestrate-{explorer,implementer,reviewer}.md` go to `~/.claude/agents/`
  (user) or `.claude/agents/` (project) and are spawned with `subagent_type`. The reviewer and the
  explorer have no Edit or Write tool but do have Bash, so they can still change files through the
  shell; read-only is a convention here, not a sandbox. After a review, run `integrate.sh
  verify-clean` on the worktree to confirm the staged diff is unchanged.
- Install: symlink or copy the `skill/` directory to `~/.claude/skills/orchestrate/` (user scope) or
  `.claude/skills/orchestrate/` in a repo. `./install.sh` at the repository root does the user scope.
