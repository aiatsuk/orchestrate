# T1: mechanical unit test

A unit test for a small class (under 100 lines) with no timers or streams, where the repository
already has a test of the same shape to copy from.

Expected tier: 1. Reviewer: tier 2.

What a pass looks like: every branch of every public method has a case; failure paths assert the
observable side effect (state unchanged, error reported once) rather than "does not throw"; the fake
follows the repository's convention; the file mirrors the source path.

What discriminates: whether the agent reads the reference test and the conventions document, and
whether it asserts call counts and error reporting rather than only the happy path.
