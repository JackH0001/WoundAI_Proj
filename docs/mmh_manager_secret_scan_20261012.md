# Management source pre-push scan — 2026-10-12

The separate management head `3e96f8aba5b00d187fec073f792a67879c7ad882`
was scanned with official gitleaks 8.28.0 before publication. One finding was
`woundai-generic-pw-assign` in `Backend/AccessManager/test_sessions.py:143`.
It uses a fixed synthetic value in an in-memory login-concurrency test.
No application credential or runtime behavior was changed.

The fix is restricted to that exact repository-relative file AND the exact
matched password assignment with the synthetic value. It uses `regexTarget`
`match`; other files, field names, values and colon assignments still fail.
Default rules and global exclusions are unchanged.

Verification:
- Live-config regression tests: 8/8 passed, warnings treated as errors.
- Actual gitleaks Git scans of disposable fixture repositories: 12/12 passed.
- Management session tests: 16/16 passed with locked dependencies.
- Management commit range from `b7f9dc2` through `3e96f8a`, corrected config:
  zero findings. This is not a claim about all history or external artifacts.
- `git diff --check`: passed.

The earlier 167 management tests remain historical evidence, not a claim of a
full rerun in this scan-only change. No source push, PR, IAM, deployment,
account migration or traffic change is included in this local check.

Both this branch and mobile PR #24 edit the generic rule's scoped allowlist and
its regression tests. Integration must preserve both independent path/value
pairs (without widening to a path/value cross-product) and rerun the boundary
checks. Do not resolve those configuration changes by keeping just one side.
