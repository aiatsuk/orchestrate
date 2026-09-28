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

## The execution loop as a workflow

- Launch phases 2 to 4 with the `Workflow` tool and `args` = the JSON object printed by
  `scripts/plan.py workflow-args plan.json` (pass the object itself, not a JSON string). Prefer
  `name: "orchestrate-execute"`, the copy `install.sh` saved in `~/.claude/workflows/`: it works even
  when the skill directory is outside what the session may read. The script refuses args from another
  skill version, so a stale copy fails at once (rerun `install.sh`). Without the saved copy, use
  `scriptPath` = the real path of this skill's `workflows/orchestrate-execute.js`; if that is refused
  because the directory is not readable, add it with `/add-dir` or a Read allow rule. The
  invocation of `/orchestrate` is the opt-in the Workflow tool requires.
- `./install.sh` also copies the script to `~/.claude/workflows/`, so it is a saved workflow:
  `Workflow` with `name: "orchestrate-execute"` works, and a user can run `/orchestrate-execute`
  directly with a prepared args file (Claude reads the file and passes its object). The copy is
  refreshed by rerunning `install.sh`; a scriptPath launch always uses the skill's own file.
- Claude Code starts a workflow only from a script the session may read. If the launch is refused
  because the skill directory is outside the working directory, add it with `/add-dir` or a Read
  allow rule. In auto mode the first launch asks once; in `claude -p` add `Workflow` to the allow
  rules or the launch is denied.
- The result arrives as a task notification carrying the script's return value. Save it as
  `execute-result.json` from the task's output file named in the launch message, not by copying the
  notification text, which escapes `&`, `<` and `>`; then run `plan.py verify-result`. Progress per phase and agent is in
  `/workflows`; the run's journal and per-agent transcripts are under the session directory
  (`~/.claude/projects/<project>/<session>/subagents/workflows/<runId>/`).
- Agents run with the roles `orchestrate-implementer` and `orchestrate-reviewer` when installed
  (`plan.py workflow-args` checks `~/.claude/agents`); otherwise the role text is inlined into the
  prompt. The model comes from `args` and overrides the role's pinned model.
- Concurrency: at most min(16, CPUs - 2) agents at a time per workflow
  (`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` overrides it); more tasks queue.
- A workflow agent cannot be resumed, so each rework round and each re-review is a fresh agent that
  gets the spec, the previous report and the defects in its prompt. A resumed agent was 4 to 6 times
  cheaper in the 2026-09-12 run; that is the price of a loop that cannot skip steps.
- Relaunching after a stop or an edit: `Workflow` with the same `scriptPath` and
  `resumeFromRunId`; unchanged agents replay from the journal, within the same session only
  (including after `claude --resume`). A new session starts a new run; the task worktrees keep the
  state, so pass only the unfinished tasks: `plan.py workflow-args plan.json --only <id> …
  --previous execute-result.json` carries passed dependencies over without rerunning them, and an
  already scaffolded worktree is recognised. Prompts carry the spec's sha256 as of `workflow-args`,
  so regenerate the args after editing a spec; a resume with the old args replays the old agents.
  A resume also replays the relayed gate, scaffold and export results (the export's tree check still
  protects the result); after fixing the environment, or recreating or resetting a worktree, start a
  new run instead of resuming.
- The gate, scaffold and export steps are relayed by a utility agent through the Bash tool, which
  allows at most ten minutes per command; the relay prompt asks for that timeout or a background
  run. Keep scoped gates under ten minutes (compare with the baseline duration).
- In `claude -p` the process waits for the workflow but gives up after 10 idle minutes
  (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`); raise it for long autonomous runs. Usage-limit pauses do
  not apply there: affected agents fail and their tasks end BLOCKED.
- Read-only review stays a convention here (the reviewer keeps Bash). The loop checks it: the export
  refuses a staged tree that differs from the one the gate saw, and `verify-clean` refuses unstaged
  or untracked changes.
- Usage after the run: `python3 evals/claude_usage.py --session-dir <session dir>` (per agent, per
  model, cache hit rate), or `--workflow-dir` for one run.
