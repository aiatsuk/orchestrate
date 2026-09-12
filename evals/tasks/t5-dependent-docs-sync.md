# T5: dependent docs sync

Update the repository's documentation after several code tasks have passed: status tables, a
changelog or memory entry, and canon lines that still describe the old layout.

Expected tier: 2. Reviewer: tier 3.

What a pass looks like: every statement is true against the code present in the worktree, history
lines may name removed files in the past tense, canon lines do not point at removed paths, generated
copies are regenerated through the repository's tool rather than edited by hand.

What discriminates: the orchestrator more than the agent. The worktree must be scaffolded on every
patch the docs describe, and "must not mention" checks must be scoped to canon paths; otherwise the
reviewer correctly rejects claims the worktree cannot support.
