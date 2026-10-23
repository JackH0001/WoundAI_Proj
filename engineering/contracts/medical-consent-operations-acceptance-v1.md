# Medical consent operations acceptance v1 — review only, 2026-10-23

This is a deployment plan, **not permission to execute**. It extends
`medical-consent-operations-v1.md` and `medical-consent/2`. No existing cloud
object, IAM binding, service, schedule, retention policy or App release is
changed by generating this plan. All committed fixtures are synthetic.

## 1. Migration protocol

`medical_consent_migration.py` creates a deterministic, read-only manifest:

- Hash every historical consent/care/promotion/binding source and the legacy
  withdrawal snapshot. Local media are byte-hashed; GCS media are identified
  by immutable generation, size and recoverable/noncurrent disposition. No raw
  research image is downloaded merely to create the GCS manifest.
- Derive index and ownership entries from sealed consent chains, valid protected
  promotion receipts, or a care receipt with a verified subject HMAC binding.
  Ownership does not imply consent. A historical care receipt expiring is not a
  new research grant. Missing verification keys are blockers, never bypassed.
- Verify existing derived rows against independently supported mappings. Reject
  unsupported/contradictory rows, orphan events, uncommitted roots, corrupt
  chains, unresolved media, and legacy-only subjects lacking a sealed v2 chain.
  This tool deliberately does not invent institution/timestamp or repair WORM.
- A scope containing any blocker needs a separately reviewed additive legacy
  disposition. Do not label it migrated or start the cleanup schedule.

Example in a disposable **local synthetic copy**:

```sh
WOUNDAI_STORE=local python tools/run_medical_consent_maintenance.py index-backfill \
  --root /tmp/medical-synthetic --output /tmp/migration-plan.json
WOUNDAI_STORE=local python tools/run_medical_consent_maintenance.py index-backfill \
  --root /tmp/medical-synthetic --apply --writers-paused \
  --plan /tmp/migration-plan.json --expected-sha256 <reviewed-plan-sha256> \
  --output /tmp/migration-result.json
```

The second command is **not approved for deployed data**. The operator must
actually pause **and drain all** old/new upload, consent, export/training writers
and cleanup executions. `--writers-paused` is an operator assertion; snapshots
cannot replace a maintenance window or stop a suspended old process. A storage
read-only credential is sufficient for planning; apply needs additive writes
only to reviewed index/ownership/migration receipt namespaces, never media delete.

Apply re-plans and compares the exact approved hash, writes an immutable intent,
adds both directions, verifies sources and every output, writes verified receipt,
and publishes the completeness marker last. It never resets an existing marker,
rewrites an event or changes raw media. A partial apply resumes with the same
manifest; generated outputs are verified but excluded from source hashes, so
own writes do not make retries stale. Any unrelated source/media change stops
completion. If a marker already existed, an interrupted ownership backfill still
requires operator readback: that older marker alone is not proof of this new
migration. Restore writes only after the verified receipt and manifest agree.

Plans/results contain identities: local `--output` creates mode-0600 files and
refuses overwrite; production evidence goes into protected storage. Do not put
real plans in a public PR, stdout logs, screenshots or Claude handoff ZIP.

## 2. Cleanup execution and scheduling proposal

The CLI `media-cleanup` defaults to read-only, requires an explicit policy JSON,
and has no implicit 30-day retention. Policy `{id, retention_seconds}` must be
approved together with IRB/participant wording and retained metadata treatment.
A zero-delay policy is used **only in synthetic tests**.

For apply, `--run-id` or Cloud Run's `CLOUD_RUN_EXECUTION` is required. Immutable
execution intent binds policy and subject bound; result records every checked or
blocked subject. A completed same-ID retry is read-only. Interrupted execution
can resume; a new execution scans old purge claims again to catch late media.
A pending result requires investigation/new execution, not silent success.

`--max-subjects` defaults to 2,000 (hard input maximum 10,000). Exceeding it stops
before deletion rather than starving later subjects. This bounds the mutation
batch, **not the initial listing cost or network time**; a large deployment needs
reviewed sharding and GCS latency testing. Tasks timeout is a separate backstop.

Proposed Cloud Run Job settings (not created): one task, parallelism 1, retries 0,
600-second task timeout, 1 CPU, 512 MiB, initial command in dry-run mode. Proposed
Scheduler: 03:30 Asia/Taipei daily, **initially paused**. Resource/image names and
production policy stay unset until exact target review. Scheduler POSTs to the
Cloud Run Jobs API using OAuth, with a separate job-invoker service account.
[Google scheduling documentation](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).

Parallelism 1 limits tasks **within** one execution; it does not serialize separate
executions. Consent create-only CAS and generation-pinned deletes provide safety
for overlap. Scheduler delivery is not treated as exactly-once. Independent
executions retain independent receipts; no mutable global lock controls consent.

Permissions to review, scoped to exact buckets/prefixes:

| Identity | Needed operations | Excluded operations |
|---|---|---|
| Scheduler SA | invoke the selected private Job | media/audit access, deploy |
| Worker SA | media list/get/generation-delete; bucket policy read; consent/index/audit list/get/create | audit delete/update, bucket policy changes, account administration |
| Migration operator | scoped source read, reviewed index/owner/receipt create | media delete, arbitrary WORM repair |

Worker audit admission must match the selected deployment. The current MMH and
Demo validation adapters enforce an exact **service** identity. A Job must not
spoof `K_SERVICE` to pass that guard. A separately reviewed exact Job profile or
an appropriate locked production audit epoch is a preactivation gate; this
revision does not relax those adapters or create an irreversible retention lock.

Alert on: nonzero exit; missing/invalid result receipt; `needs_attention`;
`blocked_fault`; `blocked_missing_timestamp`; overdue `recoverable_media_pending`;
subject bound exceeded; or no successful inventory check for >26 hours. A success
heartbeat means worker/readback succeeded, not all backups/models were erased.
Test alert delivery and recovery before enabling a schedule.

## 3. Race/disposition rules

| Interleaving | Required outcome |
|---|---|
| Restore wins before purge CAS | purge gets conflict; no media deleted |
| Purge wins before renewed consent | old image IDs stay excluded; new consent can accept new media |
| Two workers claim same withdrawal | one immutable sequence slot; equivalent replay; pinned deletion safe |
| Worker dies during deletion | immutable claim retained; next execution resumes without new consent intent |
| Same finished execution repeats | no new mutation; receipt replay only |
| Old upload writes after cleanup | new execution finds old claimed image; no return to training |
| Object replaced after inventory | old generation only deleted; remaining generation keeps result pending |
| Corrupt unrelated subject | that subject blocked, healthy subject still processed; overall needs attention |

No ordinary execution can delete consent/audit/annotation ledgers or unknown
paths. Original research data outside separately approved disposal remain intact.
These statements do not promise deletion of exports, backups or trained models.

## 4. GCS acceptance — separate authorization required

`tools/plan_medical_consent_operations.py --output <new-file.json>` binds a clean
commit and critical file hashes. It generates review data only, no SDK calls,
credentials, deployment commands, or scheduler activation.

Candidate isolated scope: project `woundai-jackh001`, region `asia-east1`, new
media/audit buckets `woundai-consent-smoke-20261023-{media,audit}-421209514056`,
prefix `consent-ops-synthetic/20261023`. Names are proposed, **not observed existing
resources**. No copy from MMH, Demo or Lite. Abort on collision/nonempty bucket or
unexpected IAM; do not reuse another service's synthetic data. Proposed single
run limit: 20 minutes, at most 1,000 generated objects, 16 MiB uploaded payload.
Budget/actual GCS request counts must also be reported; these are not dollar caps.

Before execution, separately approve bucket/identity creation, scoped writes and
synthetic generation deletions, normal charges and failure containment. Read back
IAM, public-access prevention, versioning, soft deletion, retention and holds;
archive pre/post metadata. No allUsers entry, production deploy, secret rotation,
7-year test-data lock, or blanket cleanup.

A smoke-only adapter, patterned after the repository's existing GCS audit smoke
harness, must verify the exact empty disposable bucket tuple and run manifest
before testing protected writes. That adapter is **not implemented or approved in
this revision**; production GcsStore still requires its existing audit policy.
The offline double models a locked bucket but does not create one. Review this
remaining implementation before any live run; never monkeypatch the live runtime
or reuse MMH/Demo environment variables to get around admission.

Real-service acceptance must run two independent clients and preserve:

1. Read-only plan, additive migration, interruption/resume, source generations
   unchanged; unknown owner and old unsealed subject block completion.
2. Same-slot create-only CAS returns 412/conflict for a different event; response
   loss returns equivalent receipt; stale restore is 409.
3. Both purge/restore race orders, overlapping cleanup, crash/resume, new media
   retained after reconsent, unrelated subject unchanged.
4. Multiple live/noncurrent generations; pinned deletion; late replacement not
   confused with inventoried generation. Record every generation and readback.
5. Soft-deleted versions still pending after the *current* soft policy is disabled.
   Disabling does not shorten an already deleted object's original retention.
   This cannot be declared passed merely by a mocked clock. If the live retained
   duration exceeds the acceptance window, retain evidence and leave expiry as
   pending for a separately arranged follow-up; do not promise 20-minute expiry.
6. Object hold / missing permission / audit-write failure remain pending; release
   only synthetic test holds authorized in that run; all audit receipts survive.
7. Export a synthetic-only manifest + hashes + commands/tool versions + request
   counts and sanitized errors. Keep fixtures and audit until separately approved
   teardown; no automatic bucket deletion after a failure.

Google references: [preconditions and pinned generations](https://docs.cloud.google.com/storage/docs/request-preconditions),
[versioning](https://docs.cloud.google.com/storage/docs/object-versioning),
[soft deletion and changes to retention](https://docs.cloud.google.com/storage/docs/soft-delete).
Current code now always queries soft-deleted objects, even when the current policy
reports 0. An unsupported SDK/list failure is pending, not destruction complete.

## 5. Evidence boundary and rollout

Offline tests run production GcsStore calls against `medical_fake_gcs.py`, with
no ADC/network and fake retention metadata. They validate caller logic and API
arguments; they **cannot validate real IAM, SDK/server semantics, retention expiry,
latency, load capacity or Scheduler execution**. Do not substitute them for item 4.

After isolated acceptance and independent Claude review: bind the actual container
image digest and approved policy, review exact worker/IAM scope, pause/drain old
writers, migrate, bring up consistent API/training/export readers, run current and
late mobile intents, and only then consider cleanup apply/scheduling. Rollback
must remain purge-aware; rolling back to pre-consent-order/pre-purge readers can
re-admit withdrawn media. Stop workers/writers and inspect rather than resetting
receipts. WoundAILite remains latest-stable-build-only; no build allowlist, mobile
version or TestFlight/App Store state changes in this stage.
