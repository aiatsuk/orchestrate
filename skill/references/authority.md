# External authority protocol

`workflows/orchestrate-execute.js` is the one execution loop for this skill and for coordinators
that keep their own records, such as Delivery Harness. Without `args.authority` the loop is the
orchestrate loop of `SKILL.md` (helpers in `scripts/task.py`). With it, the same loop drives the
implement, gate, review and rework cycle, while an external authority owns every record and every
decision to continue. This file is the contract such an authority implements.

## Arguments

Everything in `plan.py workflow-args` output applies, except `scripts` (not needed) and these
differences:

- `authority`: `{name, helper}`. `helper` is an argument array; the loop appends a subcommand and
  its options, single-quotes every word, and a relay agent runs the command once and returns the
  one JSON object it prints.
- `schemas.step`: the schema of the prepare, dispatch, collect, rework, review-open and review-close
  results. `schemas.gate` and `schemas.finish` keep the shapes of `task.py gate` and `task.py finish`.
  `schemas.report` and `schemas.verdict` are the authority's own report and verdict shapes.
- `formats` (optional): `report` and `verdict` replace the loop's output instructions, `brief` is
  appended to every implementer's hard limits.
- Per task: `worktree`, `branch`, `start_head` and `spec_sha256` may be null; the preparation step
  supplies them. `chain` has one step: the loop never escalates under an authority. A step or lens
  without `model` inherits the host's configured model. `scaffold_from` may be empty.
- `integration` (optional): `{worktree, base_sha, acceptance, requirements?, gate?, brief?, lenses}`
  reviews an integrated diff after the tasks (`tasks` may be empty).
- `limits.rework_rounds` bounds a runaway loop only; the authority's budget decides first, and
  reaching the bound blocks the task.
- `schemas.verdict` must keep `verdict` (PASS or FAIL) and `defects` (objects with `file`, `kind`,
  `severity`, `summary`, `scenario`); the loop quotes them into rework prompts.

Under an authority a task ends PASS, BLOCKED or SKIPPED, never ESCALATE.

## Steps

Each subcommand prints one JSON object. Any result may carry `blocked`: a non-empty reason ends the
task as BLOCKED with that reason, for example an exhausted budget or a non-converging finding.

| Subcommand | When | Result |
| --- | --- | --- |
| `prepare --task T` | once, after every dependency passed | `{exit_code, output, worktree, branch, head, spec_sha256?, worktree_id?, resume_at?}`; without `worktree` and `head` the task blocks; `resume_at` is described below |
| `dispatch --task T` | before each implementer or rework agent | `{exit_code, output, dispatch}`; the implementer must return it as `dispatch_id`, and no other result may carry that key, or the authority would see two results for one dispatch |
| `collect --task T` | after the implementer returned or failed | `{exit_code, output, accepted}`; the authority reads the result from the host journal, and when it refuses it, ends that dispatch itself |
| `gate --task T --label L` | after an accepted result | the `task.py gate` object |
| `rework --task T --reason R --key K …` | after a red or dirty gate | `{exit_code, output}`; keys are `file|kind` |
| `review-open (--task T \| --integration) --lens L …` | before review | `{exit_code, output, tokens: {lens: token}}`; each reviewer returns its token as `review_token` |
| `review-close (--task T \| --integration) --lens L …` | after every reviewer returned | `{exit_code, output, verdict}`; the authority imports the verdicts from the host journal and its verdict wins; a FAIL returns the task to rework on the authority's side |
| `finish --task T` | after a PASS | the `task.py finish` object for the reviewed patch |

### Resuming at the gate

`resume_at: "gate"` in the prepare result says the authority already holds an accepted result for
this task, for example when an implementer reported before an earlier loop stopped. The loop then
skips the dispatch, implementer and collect steps of the first round and starts at the gate; the
review, rework and finish steps follow exactly as usual, and later rounds dispatch as usual. The
reviewers of that first round get no implementer claims and verify the staged diff alone. Without
the field, or with `null`, nothing changes; any other value, the empty string included, blocks the task.

A non-zero `exit_code` from dispatch, rework, review-open or review-close blocks the task with
`output`. The loop still refuses a moved HEAD, changes outside the scope after a gate, and an
exported tree other than the gated one.

## Identity and runtimes

Agent identity comes from the host, never from the loop: the authority reads the Claude Code
workflow journal (`<session>/subagents/workflows/<runId>/journal.jsonl`), where each agent's result
line is written before any agent that depends on it starts. Labels are `impl:T:L0` and
`rework:T:L0r<n>` for implementers and `review:T:r<n>:<lens>` or `review:integration:<lens>` for
reviewers. `scripts/run_workflow.js` refuses authority arguments: it writes no host journal, so on
Codex the authority dispatches through its own native-agent path.
