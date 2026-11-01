# Account disposition composition — local rehearsal

Parent: `1186dfd1a8320902de4b08445177f0f389fec270`. Date: 2026-10-31.

## Gap and behavior

The preceding private components can seal current service/research payloads,
revoke indexed Apple grants, schedule the account grant key, and remove exact
historical generations. They did not share a durable dispatch cursor. The old
`DispositionProgress.advance()` invokes the research lane first: a research
exception prevents unrelated result/revocation/key work during that call.
Service history also relied on a caller retaining its catalog position and
revisiting unfinished operations.

`AccountDispositionWorker` explicitly composes the actual six components, with
matching frozen account/closure inventories and independent state/audit scopes.
It dispatches one bounded component per checkpoint, in this order:

1. current service results;
2. indexed Apple grant revocation;
3. account grant-key disposition, only after confirmed revocation;
4. current research media and its admission closure;
5. service-result history;
6. research-media history.

A component failure records that lane as blocked and moves the saved selector
to the next lane. Failed lanes are attempted at most once per `run`; pending
lanes have at most three dispatches per invocation. Unexpected programming
exceptions stop the run rather than being reported as provider unavailability.
Provider-side idempotency and exact generation conditions stay in the original
components; the outer checkpoint does not authorize broader deletion.

`ServiceResultHistory.advance_account()` now retains the frozen cursor until
that operation's old active generations and original audit batch are confirmed.
It advances one operation/batch per call. Soft versions and unavailable soft
inventories are separately counted; they do not prevent scanning other verified
operations and are never reported as permanent erasure.

## Explicit controls and read semantics

- `run(person)` is read-only. `apply=True` is required for dispatch.
- A run permits at most 100 component dispatches and 1–20 component items per
  dispatch. `max_seconds` is 0–300 seconds, exclusive at zero.
- **The deadline is cooperative.** Cancellation, time and durable pause are
  checked before a dispatch and after it returns. An already-running component
  or RPC is not forcibly killed. Individual components retain their existing
  RPC limits. A production supervisor and propagated end-to-end deadlines still
  need acceptance; this is not a hard latency guarantee.
- `set_paused` requires the observed control revision. Pauses survive restarts;
  missing control/anchor evidence fails closed instead of defaulting to active.
  Pausing cannot undo an RPC already submitted, restore removed generations,
  reopen an account or cancel a subscription.
- Plan, dispatch intent, component checkpoints and observations precede the
  corresponding outer CAS. Lost acknowledgment reads back the same work. CAS
  losers do not overwrite a later checkpoint. Missing original audit is never
  recreated to justify existing progress.
- `status` reads only account/checkpoint evidence. It does not list media,
  invoke Apple/KMS, run inference, or initiate cleanup. The private status may
  contain internal resource references and must not be exposed as a public API.
- `observed` means the component finished its recorded observation, **not**
  complete erasure. Unresolved grants, soft data, backups and other retention
  lanes remain explicit. All receipts retain `account_deleted=false`.
- History is not automatically rescanned. `request_rescan` persists the next
  round before dispatch; a lost response resumes that same round. Mid-scan
  reset is refused. Idle completed runs add no worker observations/provider calls.

## Public/native compatibility

The worker records service/key progress through the existing validated v2
`DispositionProgress` contract. Original operation, local records and quota
remain unchanged. Research/history detail remains in the private checkpoint;
the public v2 pending-lane list is unchanged. No new native completion claim is
introduced, and the old helper has not been silently replaced or enabled.

This is not yet a production queue or worker entrypoint. There is no added
route, scheduler, environment activation, IAM permission, image allowlist or
deployment. The private runtime composition, public/native detailed progress,
orphan-history reconciliation, identity/subscription/audit retention, backups,
hard deadlines and true provider/device acceptance remain release work.

## Rehearsal and independent replay

Run the same `tools/lite_disposition_worker_probe.py` against the parent and
candidate module paths. It generates the capture and labels, uses real SQLite
CAS and real GCS-state adapter calls against version-aware provider doubles,
and uses fake Apple/KMS transports. Parent drives its existing current-media
composition; candidate recreates the new worker after each run. Both preserve
the original capture and quota/identity rows; inference executes once. The
candidate additionally advances both historical-generation lanes without a
caller selecting individual objects. The exact counts and full logs are in
the accompanying evidence packet. No live cloud/key/media deletion is performed.

Tests include research outage while Apple continues, Apple outage without key
destruction, independent history outage, two workers, checkpoint/audit response
loss, persisted pause and missing pause evidence, deadline/cancel boundaries,
bounded retries, soft history, explicit rescan response loss, cross-scope aliases,
and idle no-growth behavior. Tests overlap broader suites; do not add totals.

Claude review priorities: validate the same frozen inventories throughout the
composition, original checkpoint/audit availability, rescan round recovery,
failure isolation, exact-generation preservation, and the explicit difference
between a cooperative dispatch deadline and a hard provider deadline.
