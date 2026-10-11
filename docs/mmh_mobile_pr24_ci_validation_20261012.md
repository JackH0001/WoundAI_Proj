# MMH mobile PR #24 CI follow-up — 2026-10-12

Approved remote head: `fdc54315f8e7cf5740b8c93e8941a760363e56af`.
Draft PR: https://github.com/JackH0001/WoundAI_Proj/pull/24
Base: `b7f9dc2b28bc4a270c2df531838df18c69cfff07`.

## Finding and bounded fix

Gitleaks 8.28.0 reported three `woundai-generic-pw-assign` findings in
`iOS/WoundAITests/InstitutionIAPTransportTests.swift` at lines 228, 234 and 240.
They are fixed literal synthetic credentials in an in-memory HTTP transport test,
not production account credentials. The source fixtures are unchanged.

The rule now requires BOTH the exact repository-relative test path and the exact
matched colon assignment with either of the two synthetic values. Matching uses
`regexTarget = "match"`, not the captured field name. Other values, assignment
forms and files retain detection. Existing default rules/global exclusions are
unchanged. No production runtime behavior, IAM, deployment or release change.

## Local verification

- Python regression suite: **8/8 passed**, including four new allowlist boundary
  tests; warnings treated as errors.
- Official gitleaks **8.28.0**, matching CI: downloaded Darwin ARM64 archive SHA-256
  `d942f3ad147250c9edbaab3fed9e482f98d3b59ba10ae97b8d75647e3ade492c`,
  verified against the release checksum manifest.
- Actual gitleaks Git scans in disposable repositories: **12/12 passed**.
  Two exact fixture values allowed; the same values in two other files detected;
  different/prefixed/suffixed values, passwd/pwd and equals assignments detected.
- PR commit range from the base above through the approved head: **0 findings**
  using the corrected configuration. This is a range scan, not an assertion about
  all historical repository contents or external artifacts.
- `git diff --check`: passed.

## Remote state at this checkpoint

Android build/unit tests, disposable signing, audit contract, backend image and
integrity passed. iOS was still running; Android emulator instrumentation was
skipped because the PR is Draft. Gitleaks remains failed on the approved remote
head until this separate local fix is authorized and pushed. No merge/deployment
was performed. Local checks do not replace remote CI or store-installed testing.
