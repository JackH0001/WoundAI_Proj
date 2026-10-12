# Medical session validation foundation — 2026-10-12

## Purpose and boundary

Repeated password login before each iOS analysis/submission adds an audited write and waiting. This backend foundation adds `GET /api/v1/auth/session` for a live, read-only check of the calling medical account. It does not implement or deploy mobile session reuse, alter IAP, or make the admin-only `/api/v1/users/session` available to physicians.

The new MMH endpoint requires a signed, unexpired, revision-bound credential, the exact institution and subject, a currently enabled account with matching role and account revision, and the existing `measure.sample` capability. It returns only the caller identity, role, permissions and expiry with contract `account-revision-v1`; it never returns a bearer, password, account hash or revision. All responses, including JWT middleware failures, use `Cache-Control: no-store`. No audit write or token refresh occurs.

New institution password logins and OTC exchanges carry the full stored account's canonical revision. Every normal protected endpoint's shared institution guard checks that revision when present. Password changes, disabling/re-enabling and role changes/restoration invalidate these credentials; CAS nonce prevents the old token becoming valid again after an apparent restoration. OTC exchange rechecks its second account read so a race cannot upgrade a stale credential.

Legacy tokens retain the previous live role/disabled check for compatibility and cannot use the new session-reuse endpoint. This patch does not claim immediate password-reset revocation for already-issued legacy tokens. An unbound legacy service keeps its original claim shape, and the reuse endpoint is unavailable there.

## Actual local validation

- Medical session tests: **21/21** — route tests with real JWTs and isolated synthetic account storage plus real Flask password-login, global-guard and OTC flows.
- Existing admin management session: **7/7**.
- Institution isolation/bootstrap: **13/13**.
- Account CAS: **9/9**.
- Initial integration run failed four cases because the Flask logger tried to write in the read-only checkout. Tests now use a dedicated temporary runtime directory; the corrected full run passed.
- Authentication tests do not load clinical models or verify segmentation/accuracy. Optional model warnings are expected in this fixture.

The shared global guard can reject unavailable account state before the endpoint and return the existing HTTP 401. The mobile client must not present every 401 as a wrong password or automatically repeat writes. Explicit endpoint lookup failure is HTTP 503. Permission checks remain server-side on every operation; a successful status check does not authorize a later operation after revocation.

## Remaining integration and release work

Bind shared in-memory mobile sessions to origin, credential generation and Google transport/subject generation; invalidate stale async responses after logout or switching. Add single-flight login, read-only live revalidation, fail-closed role transitions and tests for ten captures, concurrent work and expired/unavailable sessions before installation. Do not persist bearer tokens or retry timed-out writes merely to hide waiting.

This is an independent local change, not part of PR #26. No cloud build, deployment, account mutation or phone acceleration is claimed. Backend changes follow the user's requested MMH latency/login fixes; no Windows deployment script was changed or claimed tested.

## Integration with deployed latency baseline

Merged fixed deployed source `85775e8613b8a4680f8df2d0eca2e292dd383fd9` locally on 2026-10-12. Combined medical-session, admin-session, institution, account CAS and unlocked-audit suites passed **74/74** with `PYTHONPATH=engineering/phase2 python -B -m unittest tools.test_mmh_medical_session tools.test_mmh_management_session tools.test_mmh_institution tools.test_mmh_account_versions engineering.phase2.test_mmh_unlocked_audit`.

The first invocation omitted the audit suite's sibling-fixture import path: the 50 authentication/account cases passed, but audit import failed. Adding the existing fixture directory to PYTHONPATH produced the complete passing run; no product change was required. Log: `/private/tmp/mmh-session-merged-backend-tests.log`.

Local iOS and Android reuse implementations are now prepared, separately tested, and not installed. This integrated backend source is not pushed, built or deployed; the live latency revision still has no medical-session reuse endpoint.
