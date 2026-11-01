# Exact-generation service-result history disposition

This stage extends the local, default-off deletion worker. It does not activate
an HTTP route, schedule cleanup, deploy a provider, change the App or perform
live GCS deletion. Full account deletion remains incomplete.

## Problem and behavior

The prior current-slot seal correctly prevents a late inference result from
being written back. With object versioning, however, the older generations can
still contain the service mask. Clearing the live slot alone cannot prove those
versions gone.

`ServiceResultHistory` only accepts the actual indexed disposition component,
its injected GCS result namespace, an explicit bucket/prefix policy and separate
checkpoint/audit stores. Each operation must belong to the account's frozen
closure inventory. Its current permanent seal and original seal intent/receipt
must agree before any history is listed or removed. It does not list all users
or accept arbitrary object names from an App.

- Dry-run is the default. A call handles one exact object, at most 50 versions;
  account paging handles at most five operation slots. Retry pending operations
  even after the membership cursor reaches the end.
- The exact selected generation list is persisted and audit-bound before the
  first delete. On restart, missing outcome receipts are reconciled using that
  list even if deletion succeeded and the version disappeared from listing.
- Deletes use the selected generation **and** `if_generation_match`, with no
  automatic SDK retry. Unknown responses are checked against that same version.
  The permanent current seal is never removed. Replayed inference cannot create
  or overwrite it. Model count, quota receipts and original captures are not
  changed by this worker.
- A page is not complete just because it is short or empty: continuation tokens
  are checked. Each bounded pass starts listing at the beginning to avoid cursor
  skips while removing entries. A completed listing must contain the known live
  seal, and the live seal is separately rechecked.
- Retention/holds, corrupt or missing audit, unexpected objects/generations and
  unknown policy metadata stop processing. No automatic repair or reset of
  evidence is performed.
- Soft-deleted versions remain pending until a fresh listing confirms absence.
  If GCS cannot enumerate soft-deleted versions (including policy-disabled 400),
  status is **unverified**, not erased. Backups are always explicitly unresolved.

## Status scope

`active_history_pending`, `audit_batch_pending`, `soft_history_pending`,
`soft_history_unverified`, and `listed_history_absent_at_check` are internal
observations. The last only means the scoped version listings were clear during
that check. All results retain `account_deleted=false` and
`physical_backups_erased=false`. Identical idle observations deduplicate.

This worker is deliberately not connected to the public progress catalog yet:
research data, retained identity/subscription records, backups and live-provider
acceptance still require separate disposition contracts. The previous native
progress interface therefore continues to show historical versions/backups as
pending; this stage does not falsely clear that whole lane.

## Verification and reproduction

Use the repository's pinned test dependencies. Set `PYTHONPATH` to
`tools:engineering/phase2:Backend/Flask:.` (semicolon on Windows),
`PYTHONDONTWRITEBYTECODE=1`, and `WOUNDAI_STORE=local`.

```sh
python -m unittest -v test_lite_service_history
python tools/lite_service_history_probe.py --output history.json
```

The same probe outside the checkout can run against parent `ca72302`. It uses
the real lifecycle/operation/ledger code and GCS state adapter, with a generated
JPEG/mask and a local generation-aware bucket double. Before: the current seal
works, but old mask-bearing generations remain. After: active old generations
are removed, all batch outcomes are retained, and the current seal survives.
The soft-delete variant remains pending. Both run inference once and preserve
quota/identity rows and the original generated capture.

Additional tests inject lost delete/checkpoint/outcome responses, restart with a
smaller batch, concurrent workers, more than 50 versions, foreign accounts,
corrupt evidence, object holds and short/empty paginated responses. The installed
Google Storage SDK 3.13.1 is also exercised through an in-memory HTTP transport
to verify actual query parameters, typed metadata, error handling and bounded
requests. No credential discovery, network or real storage is used. Optional
OpenTelemetry bucket metadata prefetch is disabled for those wire tests; a live
executor must pin its telemetry setting and account for that traffic explicitly.

The new workflow step runs these tests and saves the probe. Full Lite and
prelaunch counts overlap and must not be added. No native changes are made in
this increment; native tests from `ca72302` are prior-stage evidence only.

## Before any live execution

Prepare a fixed-source/digest plan with bucket, prefix, immutable closure and
operation inventory, dedicated checkpoint/audit storage, operation/time/byte
limits, least-privilege identity, retention/soft-delete readback, observability
and permission rollback. Use a dedicated synthetic namespace first. This module
is not that operational executor: a queue/scheduler with per-call budgets and
durable pending-operation traversal remains to be built and rehearsed.

Minimum conceptual access is bucket policy GET, scoped object LIST/GET and
generation-specific DELETE on the dedicated service-result namespace; separate
checkpoint CAS and append-only audit access. It needs no MMH/Demo/raw-media
permission. Bucket list IAM cannot by itself express all object-prefix intent;
deployment must verify actual storage IAM semantics and isolation. No permissions
are granted by this commit. No real deletion or live canary is authorized here.

Stopping after failure preserves checkpoints and audit; it cannot undo completed
deletions. Rollback means stop the worker/withdraw its permissions, retain the
seal and receipts, and resume the original scoped operation only when authorized.
Never restore old masks into the live slot as an application rollback.

## Primary references

- [GCS objects.list](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/list):
  version ordering, continuation behavior, softDeleted/versions separation and
  the disabled-policy error.
- [GCS objects.delete](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/delete):
  generation preconditions; soft-deleted objects are not permanently deletable
  through this API and expire according to their retention time.
- [Python Bucket API](https://docs.cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.bucket.Bucket):
  interface reference; wire tests pin the repository version rather than assuming
  the latest documentation is the exact installed SDK.

No numerical accuracy, clinical validity, full erasure, production throughput
or legal compliance claim follows from these synthetic cleanup tests.
