# T3: multi-file behaviour-preserving refactor

A convention fix across three to five files, for example catch clauses or error-reporting calls,
where some listed sites turn out to be traps: code inside a string literal, a site that is already
compliant, or a helper whose signature must be checked before the call changes.

Expected tier: 2. Reviewer: tier 2 or 3.

What a pass looks like: every real site converted, every trap reported instead of edited, control
flow such as rethrow and mounted guards preserved, and a per-site assessment of what the change may
now mask.

What discriminates: judgment on what not to touch, and the quality of the open questions.
