# T4: async test

A unit test for a class with a stream subscription or a timer: initialization that subscribes,
an event that must be skipped, an event that must trigger work, and disposal that cancels.

Expected tier: 2 with the discriminating-test criterion in the definition of done; tier 3 without it.
Reviewer: tier 2 or 3.

What a pass looks like: for each behaviour there is a test that fails if that behaviour is removed.
The classic failure is a "skips the first event" test whose first event would not trigger work
anyway, so the test passes with or without the skip.

What discriminates: assertion strength. A reviewer that reasons "what if the skip were deleted"
catches it; a reviewer that only re-runs the tests does not.
