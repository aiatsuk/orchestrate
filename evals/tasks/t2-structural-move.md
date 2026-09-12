# T2: structural move

Move a type between layers or retire a legacy class, updating every import or call site (8 to 12
files), with the compiler and existing tests as the check.

Expected tier: 3. Reviewer: tier 3.

What a pass looks like: the moved content is byte-identical or the migration is behaviour-preserving;
every site is updated with no other edits; import ordering satisfies the repository's lints; the
report lists stale documentation lines without editing them unless the spec allows it.

What discriminates: tool-call economy, obedience to a scope ban, and whether the agent finds sites
the spec missed (a barrel export, a test helper) and reports them.
