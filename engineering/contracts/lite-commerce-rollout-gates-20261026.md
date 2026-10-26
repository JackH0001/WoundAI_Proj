# Lite commerce rollout gaps: implementation order and acceptance

Status: local opt-in implementation and simulation, **not enabled or deployed**. This is
separate from the B1 packaging fix and G4 per-request grant reuse. Free2/day,
Plus10/day total, and research consent independence remain unchanged. No daily
balance is reset, no original image/depth/audit is removed by this change.

## G1 — verified cutover, then lazy initialization of new days

1. Freeze every legacy successful-recognition writer for this service identity
   namespace. A route flag alone is insufficient if an old revision/worker can
   still write. Read back deployment routing, writer identity and final baseline.
2. A trusted control operation writes a create-only cutover record containing
   namespace, policy version, UTC cutover day, verified writer-fence evidence
   hash, baseline-manifest hash and account-mapping version. Preserve its intent
   and completion receipts. This is not an App request or self-supplied proof.
3. On the cutover day, use only the verified per-subject migrated baseline.
   Missing or conflicting baselines continue to fail closed; never assume zero.
4. Only for a verified service account on a UTC day **after** the sealed cutover
   day may a missing row be initialized to zero with create-only CAS and the
   cutover record's hash. Concurrent first requests read the same winning row.
   Existing or corrupt rows cannot be replaced; reconsent, reinstall, purchase
   restore and refund must not reset a row.
5. Rollback keeps the reader, namespace and writer fence. Do not reenable the old
   5/day path while the new ledger admits work. If fence status is uncertain,
   stop new admission and keep settled receipts readable.

Acceptance: concurrent first requests on a new day; baseline nonzero on cutover
day; missing/corrupt marker; stale writer still enabled; unknown account; lost
create acknowledgment; clock rollback; refund after use; two devices on one
account. There must be one baseline and used+reserved never exceeds 2/10.

## G2 — bounded lease for never-started work

Candidate policy: 15 minutes from the server's reservation event, unchanged by
polling or retries. This is an implementation proposal, not a deployed promise.

- Expiry applies only to `reserved`, never `running` or unknown execution.
- Expiry cancellation and worker claim must compete on the same daily-row CAS.
  The claim path itself checks the lease; a delayed run cannot bypass a periodic
  sweeper. A bounded scan of at most 30 entries may reclaim expired unstarted
  slots during admission; a scheduler is not required for correctness.
- Retain a cancellation proof, operation ID, image digest and attempt count.
  Release only pending success capacity; do not refund a completed success or
  erase an attempt. Same-operation retries return the cancellation receipt.
- A new attempt uses an explicit persisted successor ID. Unknown/running work
  must be reconciled first, not timed out into a second model invocation.
- Existing reservations need a versioned transition policy; never silently
  reinterpret unversioned historical jobs or change the 24-hour result TTL.

Acceptance: two abandoned free prepares then lease expiry; expiry versus claim
with barriers before CAS; acknowledgment lost after claim/cancel; repeated
expiry scans produce no extra terminal event; running past lease never reruns;
30-attempt cap still applies; prior-day reserved work follows its original ticket.

## G3 — explicit successor after a cross-day provisional failure

The default-off baseline gap is reproducible: operation mapping exists, reservation does not,
then midnight makes the original-day admission invalid. Returning generic 409
with `prepare` as the next action is not a usable recovery contract.

**Do not infer safe absence from a single read.** A pre-midnight reservation
writer can already be paused immediately before its CAS. Before authorizing a
successor, the old daily ledger needs a durable terminal fence even for an
operation with no reservation. This requires a versioned ledger transition;
changing only the HTTP error code is insufficient.

Implemented opt-in order (see `lite-cross-day-recovery-v1.md`):

1. Inspect the original ledger. If a reservation/result exists, recover that
   original ticket; do not create a successor and do not charge the new day.
2. If missing, CAS a terminal unreserved-operation fence on the original row.
   A delayed original reservation must lose CAS and observe this fence.
   Read failures/corrupt rows remain 503; no successor is authorized.
3. Persist an idempotent successor authorization tied to original operation,
   same normalized image hash, account, server day and the fence receipt.
4. Return a specific `new_operation_required` contract. The native journal first
   saves an attempt generation and authorization, then obtains one deterministic
   successor; never rotate identity on generic 401/409/503, app restart or retry.
5. Preserve the prior journal/receipts and photo/depth/mask. The successor may
   submit that same saved photo; no new capture or research reenrollment needed.

Acceptance: failure before and after reservation commit; old writer paused at
CAS across midnight; late worker claim; two concurrent successor requests;
successor response lost; native crash before/after journal save; expired result
and successful old receipt must never become a second paid operation.

## G4 — signed grace and explicit policy transition implemented locally

Implemented now: reuse a verified grant within one prepare request while still
valid. Requery after expiry; existing ticket/receipt lookup needs no Apple call.
This reduces normal new paid-operation status queries from two to one, not to
zero, and does not claim real Apple latency or monetary savings measured.

Implemented locally in the signed-grace continuation:

- Billing-grace status 4 now verifies signed renewal information and grace expiry
  before issuing the existing short-lived Plus grant. Missing/expired or
  contradictory grace evidence and Apple outages do not invent free status.
  Existing usage, owner/account keys, receipts and research state are preserved.
  See `lite-subscription-grace-v1.md` for 34 synthetic regressions and limitations.
  Real Apple Sandbox, configuration and online verification remain untested.

Implemented locally after `137c884`: stable account identity and an approved,
per-account policy transition. Old associations/registration remain immutable.
Unknown policies never silently grant free service. Fresh target Apple proof,
generation-CAS, lost-ack recovery and reader rollback have synthetic tests. No
cross-account/original-transaction transfer. See `lite-subscription-policy-migration-v1.md`.

Still blocked: complete live legacy-policy inventory, real control authentication
and writer-fence provider, Apple Sandbox/product transition, shipping purchase
and account recovery. Synthetic callback success is not live authorization.

Acceptance: active, grace with valid/expired/missing renewal proof, billing retry,
refund, Apple unavailable, supported legacy product, unknown policy, lost
migration acknowledgment, reader rollback. Real Apple Sandbox remains required.

## Existing medical and client review items (ba970b1 identifiers)

| Item | Current stage | Required follow-up and acceptance |
|---|---|---|
| N1 | Open | Distinguish unavailable/corrupt consent from actual withdrawal. Single-record upload returns retryable 503 with truthful audit reason; batch export still excludes uncertain subjects. |
| N2 | Open | Audit withdrawal intent before reading subject state, after basic request/permission validation; corrupt JSON produces fixed 503 and no success claim. Audit failure cannot be ignored. |
| N3 | Fixed locally in this stage | Normalize local inventory keys with `as_posix()`; Windows-formatted mock paths find the same 8 synthetic files as POSIX and retain all source bytes. Native Windows execution is still not claimed. |
| N4 | Open | Deduplicate unchanged per-image cleanup receipts using policy, state and inventory identity, while still scanning for late orphan generations. Keep bounded run summaries; do not delete locked prior receipts. |
| L1 | Open | Previously recorded withdrawal replay acknowledges that intent plus current state; never makes a later restore disappear or turns a superseded restore into new consent. Test both clients' outbox retirement. |
| L2 | Open | Clamp persisted Lite capacity waits once on load, write back that deadline, and preserve legitimate shorter waits. Reopening must not slide the deadline indefinitely. |

These are not the older Lite consent-version N1 which was already fixed. M3/M4,
R3/R4 and the real-device/deployment gates remain tracked in their prior reports.

## Delivery and authority boundary

Implement and replay G1–G3 together before wiring service-only commerce into
native capture/history. Then finish purchase/restore/grace and account recovery,
followed by Sandbox and a fixed-source cloud acceptance proposal. Medical
N1/N2/N4/L1/L2 must be resolved before affected patient-research rollout.

No current authorization is used here to initialize production quotas, change
IAM, migrate storage, enable cleanup, purchase ads or publish a store build.
Local unit tests and characterization are not a substitute for cloud/real-device
acceptance. Remote publication requires approval of the new exact source; latest
approval covered only `137c884`. No merge or deployment is implied.

# Prior implementation checkpoint (38aa206; superseded for G3 below)

The rules below remain deployment gates. `lite_usage_cutover.py` and the optional
`UsageLedger(cutover=...)` now implement the G1/G2 engine for **new v2 days only**.
The pinned cutover receipt records namespace, UTC day, frozen legacy-writer and
baseline hashes, account-map hash, policy and a 900-second unclaimed lease.
New verified-account days strictly after that receipt may be initialized by
create-only CAS. The cutover day still requires its verified legacy baseline.
Existing, corrupt or legacy rows are never reset/upgraded. Live writer-fence
checks must approve new admission/claim; existing receipts and settlement do not
require that live check. A missing/corrupt pinned control receipt still fails
closed. No production adapter or activation is provided.

Only `reserved` v2 entries can expire. Expiry and claim use the same daily-row
CAS. Cancelled entries, attempts and receipt hashes remain; `running` is never
timed out, re-executed or refunded by this mechanism. Readback of an old v1 row
does not give it a lease. A released slot permits another operation, not an
implicit rerun of the expired photograph.

`fence_missing(ticket)` implements **only the old-day exclusion primitive** for
G3. A matching existing reservation returns no successor authority. An absent
operation in an existing v2 day can be fenced on the same CAS row, so a paused
old reservation loses its precondition. Fences count toward the bounded 30-entry
history and retain the original input digest. Missing/v1 days are blocked.
There is no HTTP successor endpoint, authorization mapping, new native journal
generation or automatic retry identity change. G3 remains open end to end.

Acceptance uses real SQLite and the actual GCS generation-CAS adapter over a
fake SDK transport, plus signed synthetic App Attest HTTP requests. The existing
default-off gap probe is deliberately unchanged; its failures still describe
the non-cutover path. Do not interpret test success as production activation.

## Current G3/cutover simulation checkpoint

`lite_service_recovery.py` adds signed HTTP recovery and create-only successor
authorizations. The iOS service journal persists the authorization and original
checkpoint before sending anything under the successor ID. Generic failure,
old completed/pending work and expired results never rotate IDs. A fenced
missing prior-day reservation uses the original G3 authorization. Separately,
verified unstarted lease expiry now supports an explicit user-requested
successor; preflight/unknown cancellations do not. The default-off baseline
probe remains unchanged. See `lite-resubmit-rollout-review-v1.md`.

`lite_usage_rollout.py` now executes a bounded manifest with injected provider
observations, durable phase transitions and append-only intent/completion audit.
Synthetic tests cover successful activation, lost replies, partial baseline,
corrupt control, failed provider closure and failed durable pause. Rollback is
an audited pause with legacy writers kept off and counts preserved. Read-only
active observation failures do not trigger rollback. An exact-generation,
explicitly authorized same-day resume can leave pause without resetting usage;
ordinary execute/retry cannot. A claim already admitted before pause may still
finish once on the original ledger. Runtime admission cannot promise cancellation
of already authorized in-flight work.

The trusted **live cloud provider remains unimplemented**, as do the public
capture/history integration and verified account enrollment/recovery. Signed
grace/policy migration have separate local tests; real Apple acceptance remains
open. This stage is not permission to perform deployment,
production migration or cleanup. See `lite-cross-day-recovery-v1.md` for the
protocol, failure matrix and exact remaining acceptance boundary.
