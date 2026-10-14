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
skipped because the workflow only runs instrumentation on `refs/heads/main`
(not because of Draft status). Gitleaks remains failed on the approved remote
head until this separate local fix is authorized and pushed. No merge/deployment
was performed. Local checks do not replace remote CI or store-installed testing.

## Subsequent authorized push and completed CI

The user subsequently authorized and pushed `57c6d0bfc296364b47999d713a11e828f14673af`.
All seven executed check types passed (nine successful runs including duplicate
push/PR events). Medical XCTest: 147 passed; Lite XCTest: 150 passed, one skipped
because the simulator lacks the device file-protection key. All three iOS Release
builds succeeded unsigned. PR #24 remains Draft and unmerged.

## Android instrumentation follow-up

On Android 17/API 37 ARM64 with 16 KB pages, the initial Debug run passed 46
cases with one skipped. Running the same suite with the MMH validation profile
exposed six loopback HTTP receipt-test failures: the production gateway correctly
rejected clients lacking Google entry verification before they reached the mock
server. This was a test-transport mismatch, not evidence of a cloud failure.

The public BackendClient constructor retains its original guarded transport.
An internal transport constructor allows the MMH receipt fixtures to inject an
isolated local HTTP client. Separate tests now use the public constructor and
confirm that no socket is opened without a Google session or when a locally
verified synthetic session targets loopback instead of the fixed MMH origin.
No production destination, credential check, timeout or redirect policy changed.

Fresh results after this fix:

| Suite | Passed | Skipped | Failed |
| --- | ---: | ---: | ---: |
| MMH validation instrumentation | 48 | 1 | 0 |
| Debug instrumentation | 46 | 3 | 0 |
| MMH JVM tests | 64 | 0 | 0 |

Counts come from JUnit XML, not Gradle's console completion total. Both profiles
skip SampleValidationTest because the standard-photo fixture is absent; Debug
also skips the two MMH-only transport tests. These results do not establish AI
measurement accuracy, camera capture behavior or Play-installed end-to-end login.
The JVM task actually executed; it was not reported UP-TO-DATE or FROM-CACHE.

The constructor/fixture follow-up is local pending separate push authorization;
remote PR #24 still points at 57c6d0b. Test XML, initial failure and successful
Gradle logs are preserved under the external evidence folder
`mmh-iap-update-20261011/store-and-members/android-instrumentation-20261012/`.
