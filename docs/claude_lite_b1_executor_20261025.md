# B1 bounded controller and failure rehearsal

Source builds on `62edbe6`; the retained application/image are still `065c328` / `23a38262…d07`. No application, storage, IAM, OAuth, migration, cleanup, quota or store changes have been executed in this work.

## What is implemented

`tools/lite_review_executor.py` implements a **transport-independent** B1 transaction controller. It accepts only the empty-owner-login phase, immutable image/source/CI/evidence hashes, zero source grants and an exact plan authorization hash. The thirteen typed forward operations cannot be replaced by arbitrary shell commands or URLs. A private append-only hash-chained journal is fsynced before writes; a process lock prevents concurrent operation. A second start is forbidden, including after a partial failure. Restart allows containment only, even after the original authorization expired.

A write with an unknown outcome is read back once and never automatically repeated. Every intended change, including an uncertain write, triggers ordered containment: revoke owner, restore prior closed portal/reader traffic, remove the added IAP agent, restore scoped IAP settings, and revoke audit grants. Failure in one closure does not skip the remaining closures. New buckets, roles, OAuth clients, secret versions and audit entries are retained, never deleted. Effective denial must be confirmed before marking closure successful. A corrupted journal requires manual containment; the tool does not pretend to recover missing evidence.

`remove_owned_grant` is a pure IAM delta function. It requires a fresh etag, refuses to remove a baseline grant, matches conditions exactly and retains third-party members. Only ASCII Google user email case is normalized; service subjects and conditions are not rewritten.

## Test evidence and limits

`python3 -B tools/test_lite_review_executor.py`: 28 tests, including 26 injected partial states (13 steps × write not applied / write applied but response lost). Additional checks cover stale authorization, source mismatch, forbidden data capability, forged owner/audit failure, process restart, deadlines, concurrency, journal corruption and third-party IAM preservation. These are offline tests with a fake adapter, not a cloud rollback claim.

The existing human plan tests and real-signature/two-Flask-hop tests are retained. The latter verify that an empty owner view requires audit success and cannot read source objects; they do not establish a live Google login.

The controller's limits are **32 forward adapter calls and 20 closure calls**, 30 minutes and at most five operator-triggered owner probes. They are not yet counts of provider HTTP requests: an adapter operation can need polling/readback. The current deployed portal does not enforce a five-page browser limit. A live plan must either implement that hard limit or explicitly describe it as an operator stop condition.

## Not complete or authorized

This module has **no network transport and no deployment CLI**. It cannot create an OAuth client or perform GCS/Cloud Run changes. The prior 20-case operator matrix is not relabeled as fully passed: collision/ownership receipts, actual provider propagation, unknown secret version discovery, IAP binding and cross-environment drift still need the real adapter and its read-only preflight tests.

Before a new executable authorization request:

1. Bind the typed operations to exact Google APIs and a redacted per-request manifest. Cloud Run v2 uses top-level `iapEnabled`; IAP settings use `accessSettings.oauthSettings.clientId/clientSecret` with a precise field mask. Never log client secrets or use MMH's client.
2. Complete the dedicated OAuth Console creation/handoff procedure and safe lost-response recovery. Preserve shared branding and test-user settings.
3. Pin protected baseline, numeric secret versions, prior closed revision names and exact audit bucket policy. Count all HTTP calls, provider-operation polling and cleanup reserve separately.
4. Fault-inject the **real request adapter** using a fake transport: etag conflict, IAM propagation, operation timeout, lost creation response, partial revision, process death and offline containment. An offline controller does not replace these checks.
5. Verify source CI and generate the final fixed-source/hash authorization packet. No previous one-shot cloud authorization is reused.

## Next phases remain separate

- B1: owner login to an empty catalogue, metadata-only role, dedicated access audit; zero research reads.
- B2: finite isolated synthetic image/depth fixture with exact-key grants, view/audit/withdrawal/reconsent tests. No adoption of real records.
- C: medical index manifest and additive migration with writer fencing; not Lite epoch migration. First dry-run only.
- D: cleanup dry-run and stale-worker/race verification before any bounded deletion or scheduler authorization. Preserve audit and original data under the approved policy.

## Provider references

- [Cloud Run v2 service schema](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services)
- [IAP settings and OAuth fields](https://docs.cloud.google.com/iap/docs/reference/rest/v1/IapSettings)
- [Cloud Run direct IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)

These references establish API fields, not successful operations on this project.
