# Lite deletion-only identity recovery — 2026-10-29

Status: local candidate, opt-in deletion coordinator and injected native account view only. No deployment, public startup change, store build change, account/media deletion, real Apple login or purchase. This is **not complete account deletion**.

## Problem and behavior

At parent `f46335ffccc6314cd9484673e4c8c5a2744fa438`, a deletion intent saved on the phone but not accepted by the server could remain stuck when the ordinary session expired. Repeating the original request returned 401. The previous release deliberately did not reset the account or pretend it had deleted data.

The same intent now has an explicit “核對身分並繼續原刪帳要求” action. Only this action opens Apple identity verification. The app saves the recovery attempt before requesting a challenge, saves the challenge before opening Apple UI, and saves the returned ID token in the existing protected journal before confirmation. Cancellation retains the original deletion operation. The pending ID token is removed from the current checkpoint after acknowledgment or explicit rejection/expiry. No new authorization code or refresh token is requested for custody or exchanged. The existing Apple grant inventory remains the revocation worker's responsibility.

The server checks a fresh Apple-signed identity, its nonce, audience, issuer, lifetime, issue time, and the existing installation's HMAC-bound subject. A different Apple account cannot take over the request. An attested installation alone cannot confirm it. No new account, subscription binding, ordinary session, quota row, model execution, or research reconsent is created.

## Contract and sequencing

All four deletion routes use the existing attested installation and `withdraw` purpose, separately budgeted from ordinary recognition. No wildcard route bypass was added.

- `POST /api/v1/lite/account/deletion/request`: original operation, explicit `delete_account`; normal session required unless a matching durable deletion-only proof already exists.
- `POST /api/v1/lite/account/deletion/status`: same accepted requester's original operation; no session renewal.
- `POST /api/v1/lite/account/deletion/reauth/challenge`: original operation, unique recovery attempt, expected account digest, explicit `continue_deletion`. Five-minute challenge; same attempt returns identical nonce. A new attempt can replace only an expired, unverified challenge.
- `POST /api/v1/lite/account/deletion/reauth/confirm`: original operation, recovery attempt, expected account digest, state and signed ID token. Authorization-code fields are rejected. Success is the existing 202 pending-disposition receipt, not a login session.

The immutable attempt binding and challenge precede the mutable CAS checkpoint. Confirm CAS-commits `verified` with a token digest and verification timestamp, writes immutable proof audit, then closes the same account/operation through the existing lifecycle. If the proof or receipt reply is lost, the ordinary deletion request can finish from the durable proof even after its JWT expires. This proof cannot renew service, change account or authorize another deletion ID. Audit failure leaves the operation pending; it never claims completion. Full JWTs, Apple subject/email and authorization codes are absent from backend recovery audit.

A locally expired or 410 challenge needs an explicit new verification action. Reload/retry never opens Apple UI or generates a new deletion ID. Invalid or changed checkpoints fail closed. Local CAS prevents a late response from overwriting another task's newer state. Same-device reinstall, lost App Attest keys, account transfer and cross-installation deletion recovery are **not** covered by this increment.

## Before / after replay

The same `tools/lite_deletion_identity_probe.py` ran with the four changed admission/coordinator modules from parent f46335f, then with this candidate. Generated Apple signatures, real App Attest verification and SQLite CAS were used; no Apple/GCS network calls.

| Check | Parent | Candidate |
|---|---:|---:|
| First request after ordinary session expiry | 401 | 401 |
| Explicit same-account recovery available | no | yes |
| Proof committed, closure interrupted, then retry after JWT expiry | unavailable | 503 then 202 |
| Original deletion ID and repeat receipt identical | unavailable | yes |
| Additional code exchanges / refreshes / model executions | 0 / 0 / 0 | 0 / 0 / 0 |
| Service results, quota, research state, registry and grant-vault bytes unchanged | yes | yes |
| Account deletion / data erasure claimed | no | no |

## Validation

- New backend recovery tests: **28 passed** (includes generated signatures, wrong-account/nonce/audience, expired attempt, stale attempt, two concurrent confirmations, failed/audit-lost CAS, response loss, owner and privacy-purpose enforcement).
- Full Lite Python suite: **1,441 passed**, 0 failures.
- Local replay of `lite-prelaunch.yml`: **1,409 test executions**, 22 steps, all passed. The sets overlap; do not add them.
- Focused iOS tests: **102 passed**, 0 failures.
- Full iOS Release simulator: **401 passed**, 0 failures, **1 existing simulator file-protection skip** (402 total). Compared with parent 382 passed, 19 new XCTest cases.
- XCTest summary and tree were validated using `tools/verify_xctest_results.py`, with only the documented simulator protection skip permitted.
- Existing opt-out packaging/startup checks remain in prelaunch; the default Lite image does not import the opt-in deletion implementation.
- No new Android code, remote CI, physical-device Apple login, actual deletion or new StoreKit purchase was exercised in this increment.

Reproduce from repository root with the project test environment:

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=tools:engineering/phase2:Backend/Flask:.
export WOUNDAI_STORE=local
python -m unittest -v test_lite_deletion_identity test_lite_account_deletion
python tools/lite_deletion_identity_probe.py --output /tmp/lite-deletion-recovery.json
python -m unittest discover -s engineering/phase2 -p 'test_lite_*.py'
```

Use the workflow's pinned dependencies. The local environment adds the pre-existing test-dependency directory to PYTHONPATH; no new dependency was added. iOS: XcodeGen, WoundLite scheme, Release, iOS 18.6 simulator; `CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- ENABLE_TESTABILITY=YES`.

## Remaining launch gates

1. Complete data disposition catalog/executor, late-writer fencing, erasure receipts and retention disclosure. This receipt still reports `data_disposition=not_started`, `account_deleted=false`.
2. Per-account key custody/KMS and destroyability of retained encrypted copies; protected identity-key backup/rotation and recovery procedure. No KMS was created or invoked.
3. Real Apple login/Sandbox, production composition, physical-device journal protection, fresh user confirmation and failure recovery acceptance before rollout. The candidate UI remains unconnected to default startup.
4. Previously tracked medical `ba970b1` findings and original L1/L2 probes remain separate; this change does not claim they are fixed.

Apple permits reauthentication to verify account deletion intent, but an account-deactivation-only flow is insufficient and token revocation remains required. These references guide the design, not an App Review approval claim:

- [Apple account deletion guidance](https://developer.apple.com/support/offering-account-deletion-in-your-app/)
- [Verify a Sign in with Apple user](https://developer.apple.com/documentation/signinwithapple/verifying-a-user)
- [TN3194: account deletion and token revocation](https://developer.apple.com/documentation/technotes/tn3194-handling-account-deletions-and-revoking-tokens-for-sign-in-with-apple)
