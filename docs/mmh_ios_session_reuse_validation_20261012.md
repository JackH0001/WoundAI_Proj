# MMH iOS session reuse — local validation, 2026-10-12

## Scope and delivery state

Local changes on `codex/mmh-ios-session-reuse-20261012`, based on `dd74ee6183c76d5da35cf5470a1fdc41b804b883`. Not pushed, installed, or released. Requires the independent backend session contract implemented at `50cc5d78c7914d927625f523457a4041404e8a91`; the approved latency builds from `85775e8613b8a4680f8df2d0eca2e292dd383fd9` do **not** include that endpoint. Do not install this client against the current MMH service: a missing endpoint deliberately blocks acquisition.

## Behavior

Institution settings, measurement, record review and clinical upload outbox share a process-local session pool. Concurrent acquisition shares one authentication task. Each subsequent acquisition validates current account revision and permissions through a read-only endpoint instead of repeating password login. No Google or WoundAI token persistence is introduced. Account, backend URL and Google transport changes invalidate old clients before dispatch and after suspended responses. No upload or other write is automatically retried by this pool.

A failed validation clears the displayed identity. HTTP 401/403 clears the cached JWT; a later explicit acquisition can log in again. Transient validation failure does not silently reauthenticate or upload. Malformed, mismatched, expired and legacy responses fail closed. Existing request-side and backend permission guards remain necessary; this does not promise instant reactive clearing of every already-open screen when an account is changed elsewhere.

## Actual validation

- Signed iOS 26.5 simulator, iPhone 17 Pro, WoundAIInstitutionTests: **77 tests passed, 0 failures**.
- Ten sequential synthetic measurement requests: one password login, ten live session checks and ten distinct image requests/results (fixture, not cloud performance).
- Concurrent acquisitions: one login and one validation shared by both callers.
- Account/Google changes during authentication, stale-client dispatch, revoked session, temporary server failure, malformed contract and missing endpoint reject safely.
- Second measurement validation failure sends no second image and leaves no stale result/identity.
- Existing encrypted outbox, depth provenance, institutional export and authentication tests passed in the same run.

Command: `xcodebuild -project iOS/WoundMeasurementApp.xcodeproj -scheme WoundAIInstitution -destination 'platform=iOS Simulator,id=C4251C4B-31A7-42CB-BC5E-BC14A899DEA3' -derivedDataPath /private/tmp/woundai-mmh-session-DD20261012 -clonedSourcePackagesDirPath /private/tmp/woundai-google-sdk-DD20261010/SourcePackages -only-testing:WoundAIInstitutionTests test CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=-`

Evidence log: `/private/tmp/mmh-ios-session-final-tests.log`; result bundle: `/private/tmp/woundai-mmh-session-DD20261012/Logs/Test/Test-WoundAIInstitution-2026.10.12_18-10-11-+0800.xcresult`.

An earlier unsigned simulator run failed eleven existing keychain/depth tests with keychain entitlement error -34018; rerunning with simulator signing enabled passed all 77. The earlier failure is not treated as a product regression or omitted from this report.

## Remaining gates

Backend/mobile integration review and source publication; paired candidate deployment; physical Google/WoundAI login, repeated capture and queued-upload acceptance; measured end-to-end timing; Android equivalent session contract alignment. No UI or real-device tests were run for this patch. No live latency reduction, medical accuracy or complete release readiness is claimed.

Final fixture-only adjustment replaced both static test passwords with runtime UUIDs. The affected six login/capture tests passed again (`/private/tmp/mmh-ios-session-fixture-tests.log`, result 18-11-12). The iOS directory secret scan returned zero findings; production credentials were never involved.
