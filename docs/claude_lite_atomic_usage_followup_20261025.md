# Claude review: Lite atomic quota and Apple authority, 2026-10-25

Baseline: `33474c9f7018d7bc7206d414891e30120865771e`.
No cloud, existing data, native release, store product or ads changed. No material
from a live receipt/account was used. Original evidence and failed runs are kept.

## Scope and evidence

New `Backend/Flask/lite_usage_ledger.py` provides reserve/claim/settle with CAS.
New `lite_subscription_authority.py` derives internal 2/10 grants from a trusted
account binding, current Apple API status and official signature verification.
Neither module is connected to `api_lite` or public HTTP. The old route race remains
until the separate coordinator/cutover work is reviewed and deployed.

Offline tests: **500 unique tests passed after the repairs/environment recovery
below**: 45 new core/verifier/probe tests, 301 existing app/backend tests, 3 existing
HTTP lifecycle tests, 127 review/operations tests, 24 economics/race tests. Counts
do not add duplicate reruns. These are Python tests, not new iOS/Android builds or
remote CI results. Manual Linux CI is updated, not dispatched in this turn.

The same eight concurrent synthetic operations admitted eight model calls in the
old inner-route characterization, versus two execution claims in the new isolated
core at allowance two. These are different integration levels, not a before/after
deployed HTTP load test. Eight repeats of the same operation produce one claim.
Four spawned processes also share the same SQLite limit. Lost GCS-adapter reserve,
claim, initialize and settlement responses retain consistent state using a fake
transport, not live GCS.

Apple tests use the installed official 3.1.2 verifier with real synthetic certificate
chains/JWS and fake status responses. They cover signature/root/identity mismatch,
expiry, refund, owner isolation, restore and grant persistence. Online certificate
revocation, real Sandbox, signed grace renewals and notification delivery remain
untested. An unknown Apple response does not silently become free or paid.

## Failures found and handled

1. Two new malformed-field subcases initially passed through the official model's
   type conversion (numeric transaction ID and Boolean purchase date). Added
   strict raw JSON field checks **after** official signature verification. All 45
   tests now pass; `commerce-core-tests-r1-failed.log` remains in the evidence.
2. One combined regression run raced the GCS SDK's optional asynchronous bucket
   metadata fetch and counted three requests instead of two. A local trace showed
   the extra readonly bucket-metadata GET. The object-read test now disables that
   optional telemetry only inside its fixture and still checks real SDK generation
   semantics. All 151 review/planning tests then passed in one process. Production
   SDK/IAM settings were not changed; deployment should explicitly review tracing.
3. Sandbox denied binding 127.0.0.1 for one existing upload test. The same test plus
   three lifecycle tests passed with local loopback permission. No cloud request
   was substituted, and this is not a live ingress test.

## Cost interpretation

The actual GCS adapter with fake transport makes three conditional writes and
eight reads for a completed new operation (excluding day initialization). A
completed-operation retry makes zero writes, six reads and no model call. On the
prior 10,000 MAU/337,500 attempts/20% retry assumptions and assumed storage operation
rates, the quota-only operations cost about US$8.04/month. It is a **subset** of the
prior conservative 40-write/100-read allowance, not an extra whole-platform cost.
If each of 67,500 repeats otherwise ran five seconds of model compute, the avoided
compute would be US$19.58 under the inherited rate assumptions. Neither number is
an observed bill or revenue; Apple/proof/OCSP/contended CAS costs are not included.
The prior full-retention loss and unproven ad revenue conclusions remain unchanged.

## Review priorities

- Challenge the assumption that service identity is trusted: it is an explicit
  future registry requirement, not satisfied by a client string or research epoch.
- Verify no mutation path can execute on unknown claim result, grant stale paid
  work after a new reservation, reset a migrated balance or take over running work.
- Reservation admission honors in-flight work after expiry/refund; no promise of
  instantaneous revocation. Running unknown work currently needs reconciliation.
- Check the account-owner create-only fence and proof readback. They protect
  application transitions, not against administrator replacement of mutable GCS.
- Verify the no-research service route, 50% cohort and ad provider remain rollout
  work; none is claimed implemented by these backend core tests.
- Review migration freeze/manifest and failure-close protocol in
  `engineering/contracts/lite-usage-v1.md` before proposing live execution.

The handoff bundle contains source, patch, test logs, exact package versions,
JSON probes and SHA-256 manifest. No real research images, credentials, Apple
tokens or raw-depth files are included. This is a request for independent review,
not a claim that Claude has approved this commit.
