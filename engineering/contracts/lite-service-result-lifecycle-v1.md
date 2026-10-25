# Lite service-result lifecycle v1

2026-10-26: local implementation and synthetic tests only. No startup/HTTP route,
scheduler, GCS operation, IAM change or production data mutation is introduced.
Public native UI and new service admission remain disabled pending other gates.

## One exact operation, explicit stores

`ServiceResultRetention` receives the existing service coordinator, a separately
injected audit store and an explicit 64-hex deployment-scope digest. It has no
credential discovery, list/delete method, bucket creation, research dependency
or network factory. `plan(subject, operation)` is read-only. It records scope,
usage namespace, original revision, complete source hash, original quota receipt,
action and a 600-second execution window. The serialized plan contains opaque
service identifiers and hashes, not JPEG, depth or result polygons.

The control-plane caller must authorize the exact plan digest and prove store
identity/IAM separation. This library is not authentication, cloud approval or
a public client-controlled cleanup endpoint. Object aliases must be verified by
the deployment executor; different Python objects alone do not prove isolation.

## States supported

All mutable actions require the 24-hour result read window to have elapsed.

| Current state | Action | Preserved |
| --- | --- | --- |
| Result saved and settled | Compact current result payload | Original ID/day/input/model/result hashes, terminal quota receipt |
| Result saved, settlement reply missing | Settle the exact result then compact | Same outcome/hash, no model call or second charge |
| Reserved, never claimed | CAS-cancel then compact | Attempt count, original grant/ticket and cancellation proof |
| Previously cancelled in preflight | Compact without replacing cancellation | Original cancellation receipt |
| Running, no durable result | Block | Original pending state; no timeout-based refund or takeover |
| Mapping without quota entry | Block | Mapping; a reservation may be racing |
| Unexpired/missing/corrupt/inconsistent | Block | Source and existing evidence |

Expiration is a prerequisite, not evidence that execution failed. The routine
never marks a running job failed. A separate bounded operator resolution policy
with worker/result evidence remains required before general activation.

## Commit order and recovery

1. Read and validate the exact planned generation/hash and receipt. No rebase.
2. Create-only audit intent, then exact readback. Failure stops before payload
   mutation. Time is checked again before ledger and payload writes.
3. For a known result, settle it with its existing worker/hash. For unstarted
   work, use ledger CAS cancellation. A simultaneous claim/cancel has one winner;
   if claim wins, cancellation stops and the original result mapping stays.
4. Replace the exact source generation once with v2: `result=null`, immutable
   original binding fields and a `retention` terminal receipt/plan hash/time.
5. Read back the terminal and original ledger receipt, append create-only
   completion evidence. No completion is claimed on an unknown write outcome.

A failed invocation stops. The exact same plan may be explicitly resumed within
its window: previously committed intent, cancellation/settlement, compaction or
completion is read back, never reapplied as a new operation. Same-plan concurrent
calls converge to one terminal and two audit objects. A different plan cannot
replace an existing terminal. An expired plan requires a new bounded operational
decision; the library does not grant itself further retries or authorization.

The plan pins the result generation; the quota receipt may only change in the
specific expected settlement/cancellation transition. Corrupt evidence and
unknown writes fail closed, leaving an intent that explains the incomplete job.

## Reader compatibility and erasure limits

The service reader accepts legacy v1 and terminal v2. New work is still v1.
Once compacted, prepare/run/status returns existing 410 semantics; it never
returns 404 unknown or recreates the operation on the next day. The native
client's existing expired-result behavior retains local work and stops retry.
No Swift or public UI change is needed for this state.

v2 deliberately keeps original image hash, result hash, worker and receipt for
anti-replay/audit; those are pseudonymous metadata, not anonymous data. A separate
retention policy must define their lifetime. Do not delete the ID mapping as a
shortcut, since that may turn a retry into new work on another day.

**Current-version compaction is not full physical erasure.** GCS versioning,
soft delete, retained generations, backups or locked audit stores may retain old
payloads. The adapter probe explicitly preserves and verifies those generations.
The current result can even grow for tiny masks because the terminal receipt
is larger than the original polygon. Two new audit objects also add cost.
Neither storage savings nor full deletion is claimed.

Rollback must keep a v2-capable reader. An old v1-only reader fails closed on v2;
do not restore old result payloads, reset quota or re-enable the old five-call
allowance as rollback. No live migration/rollback has been executed here.

## Future deployment boundary

Before cloud execution: identify dedicated service/account/usage/result/audit
namespaces, least privilege, exact plan digest and source, per-run job/byte/write
limits and deadline, current/historical generation inventory, durable completion
review, stop/containment and v2-reader rollback. Dry-run must distinguish blocked
jobs, already-compacted jobs and eligible jobs. Permission for current-object
compaction does not authorize historical generation deletion or research cleanup.
No live execution plan is proposed yet because account/UI, legacy quota cutover,
unknown-work resolution and retained-generation policy are still unfinished.

## Reproduce

With pinned dependencies from `lite-prelaunch.yml` and
`PYTHONPATH=tools:engineering/phase2:Backend/Flask:.`:

```sh
python -m unittest -v test_lite_service_retention test_lite_service_retention_probe
python tools/lite_service_retention_probe.py --output /tmp/retention-probe.json
```

38 retention tests plus 2 probe assertions. SQLite provides real durable CAS and
thread contention. GCSStateStore is the actual adapter against fake transport;
there is no GCS call or physical-device validation in this stage.
