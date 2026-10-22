# Claude follow-up: medical server ordering and R1/R2

Baseline: `de187cc52c6b1381cd3af991586377c0af1a0882`, PR #35.
This is the next candidate source, not a deployment or patient-research approval.
The previous [safety report](claude_safety_review_followup_20261022.md) remains
the historical record for that baseline.

## Changes for review

* **Medical in-flight/cross-device consent ordering:** added an immutable storage
  sequence and conditional restores. A stale restore arriving after a withdrawal
  returns 409. Concurrent instances cannot both publish different events into
  the same slot. Replaying an old intent does not create a new consent event.
* **iOS and Android:** opening the consent form first observes one server
  revision. Confirmation pins that already-observed revision; login delays and
  retries cannot fetch a newer base afterwards. This also covers another device
  withdrawing while the form is open. Missing preflight shows a local-only
  notice; a conflict or missing observation requires reopening and confirming
  the form. Local latest-intent checks remain before sending and after response.
* **R1:** encrypted research jobs with excessively distant retry/capacity dates
  are bounded to one hour on load and saved once. Reopening does not slide the
  deadline. Payloads, raw depth references and receipts are retained.
* **R2:** Android jobs whose local image reference was cleared are retired as
  `media_unavailable`, separately from `superseded_revision`. Existing receipts
  and original queued content remain; retirement does not bypass capacity waits.

The medical [protocol and rollout limits](../engineering/contracts/medical-consent-order-v2.md)
are part of this change. In particular, withdrawals now use logical exclusion;
new ordered receipts cannot safely be read by a pre-v2 rollback image. No live
data were migrated or deleted, no cloud permissions/settings were changed, and
no app was distributed in this round. Lite's latest-only build 38 policy remains
a tested candidate, not an applied live policy.

## Verification on this source

| Suite | Passed | Skipped | Failed |
|---|---:|---:|---:|
| Medical iOS Release XCTest | 262 | 1 | 0 |
| Lite iOS Release XCTest | 234 | 1 | 0 |
| MMH iOS Release XCTest | 166 | 1 | 0 |
| Android Debug JVM | 141 | 2 | 0 |
| Android MMH validation JVM | 143 | 0 | 0 |
| Android MMH emulator, five relevant classes | 63 | 0 | 0 |
| Medical server/storage/guard regression selection | 103 | 0 | 0 |
| Lite backend regression | 452 | 0 | 0 |
| Build/contract/RGB-D tool selection | 58 | 1 | 0 |

Subtests are not counted as independent tests (medical selection: 79; Lite: 463;
tools: 50). One tool test requires PowerShell, unavailable in this local environment.
iOS skips cover device file protection / system folder-picker acceptance; Debug
JVM skips are MMH-only tests that pass in the MMH build. The emulator selection
is not the entire instrumentation suite. Seven isolated legacy script suites
also passed, including 107 manual consistency checks; the synthetic HTTP
consent lifecycle passed. The new ordering suite runs 22 tests via the CI
unittest entry point, including the cross-org state-read regression.
These are synthetic/local tests, not a cloud race test.

During regression, concurrent LocalStore temporary files were initially mistaken
for a ledger gap; the reader now ignores only unpublished LocalStore temporary
names while still rejecting missing or corrupt committed slots. Generated
duplicate DEX files in the existing build directory required a fresh isolated
build output. A parity check caught dynamically constructed Android consent URLs;
the client now lists the two supported endpoints explicitly, without relaxing
the checker. Earlier failed logs are retained alongside the final evidence.

The export-reader audit also found that overriding only the legacy withdrawal
JSONL could omit the new ledger. This incomplete override now fails closed when
ordered receipts exist; two tests cover the configured store and a separate
local source. Normal exports use `effective_queue` / `classify_queue`; remote
exports obtain an authenticated manifest. Snapshot expiry, independent bucket
copies and production export policy still require rollout verification.
Five relevant export/record script suites passed after this guard. The tissue
export CLI also needed its backend import path initialized before its environment
guard; the existing end-to-end export test caught this and then passed all 17
checks. This fix changes import setup, not mask geometry or export eligibility.

## Reproduce and inspect

```sh
python -B -m unittest discover -s engineering/phase2 -p test_medical_consent_order.py
python -m pytest engineering/phase2/test_medical_consent_order.py engineering/phase2/test_archive_flywheel_queue.py engineering/phase2/test_native_wound_mask.py engineering/phase2/test_endpoint_guards.py engineering/phase2/test_research_format_guards.py engineering/phase2/test_medical_revision_assets.py engineering/phase2/test_p0_4_staging.py -q
python -m pytest engineering/phase2/test_lite*.py -q
python tools/windows/run_backend_http_test.py --out /tmp/medical-consent-http --timeout 180
```

Review `Backend/Flask/medical_consent_order.py`, `api_flywheel.py`, both mobile
`BackendClient` implementations, `ConsentSync.swift` / `ConsentWithdrawal.kt`,
and both research outboxes. Key tests cover delayed restore, competing instances,
response-loss replay, obsolete withdrawal replay, immutable storage conflict,
legacy-history mutation, corrupted/missing events, pinned retry revisions and
preserved old deadlines/media receipts.

Local evidence (not in the public repository):
`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-area-upload-acceptance-20261021/server-consent-ordering-20261022/`.
It contains XCTest bundles, Gradle XML, pytest XML/logs and isolated HTTP output.
The final preconfirmation run additionally checks that even a newly submitted
restore with no pre-confirmation observation sends neither a state GET nor a
restore POST. The conflict fixture changes server state between opening and
submitting the form; both attempts retain the original revision.
The separate Claude handoff contains changed source/tests/docs and hashes only,
without research media or device identities. CI must be checked against the
new commit, not inherited from `de187cc`.

## Still open

M3 catalog-version migration and M4 clinical-field consent disclosure remain
open; those fields must stay disabled. R3 pending renewal across a consent-text
upgrade, R4 recovery reason-string coupling, PUSH and other low-severity UI
items are not claimed fixed. New device acceptance and medical server deployment
remain pending. All medical readers/exporters and rollback safety must be
verified before patient research. Parent repository PR #13 synchronization is
pending review; this draft does not merge or publish it. Volume research remains
in WoundAI3D. No additional captures are requested for these software regressions.

## Subsequent operational review

P1–P4 findings on this baseline and the next candidate are recorded in
[claude_consent_rollout_followup_20261022.md](claude_consent_rollout_followup_20261022.md).
This report retains its historical test counts and must not be treated as deployment approval.
