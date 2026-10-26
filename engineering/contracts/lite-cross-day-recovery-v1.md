# Lite G3 cross-day recovery and cutover rehearsal v1

Scope: opt-in implementation, signed synthetic HTTP, native simulator and
SQLite/GCS-adapter simulation. No cloud provider, deployed route, production
account provisioning, public UI activation, migration, cleanup or store update.
The free 2 / Plus total 10 successes and separate 30 attempts are unchanged.
Service use and research enrollment remain independent.

## Missing reservation is not a missing result

Baseline `38aa206`: a mapping written just before midnight, followed by failure
before reservation CAS, stays mapped to the old day. Retry returns 409 forever
within the read window. A new date or random operation from the client is unsafe:
an old writer may be paused immediately before successfully reserving.

The new ordering is:

1. Resolve the verified account and original immutable mapping. Enforce its
   existing 24-hour read window. Existing reservation/result returns the original
   ticket, including running/unknown, completed or cancelled work.
2. On the original v2 daily row, CAS an input-bound exclusion fence. It competes
   with the paused old reservation on **the same row and generation**. Missing,
   corrupt or v1 days block recovery; they are never interpreted as zero usage.
3. Create one immutable authorization in a dedicated recovery store, then create
   its successor operation. Read back both. IDs are server-generated once and
   replayed, not derived anew on retry. The account, normalized JPEG SHA-256,
   model digest, parent day and fence bind the authorization.
4. Return `new_operation_required`. The native client preserves its old journal
   checkpoint and atomically saves the new identity before sending `prepare`.
5. Initialize only an eligible verified post-cutover day, obtain a fresh ordinary
   entitlement and reserve. `run` alone may claim once, invoke the model, persist
   the result and settle. Recovery itself does not invoke or charge the model.

The new authorization hash is an audit reference; the client cannot recompute
the private server account/fence state from the response. Server-side validation
and authenticated transport remain required. No public re-registration or
research-consent change is introduced.

## HTTP contract

`POST /api/v1/lite/service/recover` accepts exactly `anon_id`, `operation` and
`image_sha256`. It uses the same challenge/assertion/counter/owner checks and
4 KiB control-body bound as `prepare`. Startup remains default-off; even with
commerce injected, recovery requires a separate recovery store and cutover.

Status can return `recovery_available` with `retry_action=recover`, or replay an
already persisted authorization. This status read may finish creating its
already-authorized successor mapping, but never reserves or runs inference.

`new_operation_required` retains the parent `operation` and `day`. `recovery`
contains `version=1`, `parent_operation`, `parent_day`, `parent_request_digest`,
`image_sha256`, `fence_sha256`, `successor_operation`, `successor_day` and
`authorization_sha256`. It contains no image, depth, mask, research epoch or
entitlement. The successor's ordinary prepare/run still applies all quotas.

## Failure matrix

| Failure / competition | Required result |
| --- | --- |
| Reservation committed but reply lost before midnight | Use original ticket/day; no new-day success charge |
| Old reservation paused before CAS | Original reservation or exclusion fence wins; never both |
| Fence or authorization committed, reply lost | Read durable state; same successor and no model call |
| Successor creation reply lost | Read back the same immutable mapping; no ID rotation |
| Concurrent recovery / run calls | One authorization, one successful execution claim, one charge |
| Result saved but settlement reply lost | Settle that same hash; do not invoke model again |
| Running/unknown execution | Pending, no takeover or invented refund |
| Cancelled / expired original | G3 `recover` retains cancellation / 410. Only verified unstarted lease expiry can use the separate explicit `resubmit` action; preflight rejection cannot. |
| Image/model/account mismatch or successor collision | Block, preserve every old row |
| Local write fails before adoption | Never dispatch successor; parent status recovers authorization |
| Local write fails after adoption | Restart reads successor, status first; original checkpoint retained |
| Admission paused or new-day quota full | Keep authorization and photograph; no quota bypass |

Each operation retains its original TTL. A valid unreserved recovery creates a
new 24-hour successor window; it cannot extend access to an expired or completed
old result. Repeated cross-day missing reservations can form a chain: the native
journal stops at eight generations; server per-parent mappings are unique and
existing request/day bounds still apply. No global server chain-depth limit or
deletion policy is claimed here. Retained authorizations must be included in the
service-state retention inventory before launch; they are not image storage.

## Cutover and rollback simulation

`UsageRollout` is an injected-provider executor, not a cloud CLI. It validates a
fixed namespace/manifest and 1–1000 unique account baselines, checks their hash,
and persists ordered phases: `prepared → frozen → sealed → active`; any rollback
ends at `paused`. Transition intent and completion are append-only CAS records.
Existing baseline/control/audit rows are preserved. A changed plan cannot replace
an existing rollout in the same namespace.

The provider must independently verify all legacy writers are disabled, their
in-flight work has drained to zero, and baseline/account-map evidence is fixed.
A route flag or a caller-supplied boolean is insufficient in a live provider.
After baseline installation and seal readback, enable the new route; runtime
admission additionally requires durable `active` state and matching live evidence.

Rollback attempts both provider closure and durable pause even if one fails.
It never resets counters, deletes rows, reopens legacy 5/day writers or silently
reactivates after pause. Only confirmed provider closure, durable pause and audit
allow a success report. Otherwise the result explicitly requires reconciliation.

Pause is **not atomic cancellation of previously admitted requests**. A claim
that passed admission before pause may win its quota-row CAS afterwards. The
barrier test demonstrates that it can complete exactly once on the same ledger,
with its count/receipt retained. New admission fails, and the old allowance is
still closed. Unknown work remains pending. A real executor must inventory these
in-flight operations and must not promise they disappeared at the pause time.

## Prerequisites before any live execution

1. Implement the trusted cloud provider and independently collect routing,
   revision, scheduler/worker, IAM and frozen-account evidence. Drain all legacy
   writers, not just the current process. Define timeout and failure scope.
2. Provision a verified stable service account; App Attest alone is not a person.
   Complete account restore, subscription grace/policy migration and public
   capture/history integration. Preserve separate research permission.
3. Fix exact source/image/namespace/day/baseline hashes; prepare bounded dry-run,
   object inventory and access policy. Retention cleanup requires separate scope.
4. Obtain specific deployment/migration authorization. Test pause/readback with
   the live provider before enabling traffic. No authorization is inferred from
   these local tests. Keep uncertain work pending for explicit reconciliation.
5. Run TestFlight same-photo recovery and cloud readback, including disconnected
   device restart and original depth/mask linkage. The simulator cannot establish
   on-device file protection, Apple attestation or real cloud availability.

SQLite and GCS-adapter cases here exercise actual stores/CAS logic over a fake
GCS SDK transport; they are not real bucket tests. Existing default-off G1–G3
gap probes intentionally continue reproducing the old route for comparison.

## Follow-up to the 8c7e447 review

`lite-resubmit-rollout-review-v1.md` specifies the separate user-requested lease
retry, read-only observation API and explicit paused recovery. It also records
the same-UTC-day / 1000-account / v1-ledger limits and future live-adapter cache
requirements. These additions do not silently retry cancelled work or activate
the shipping UI or production provider.
