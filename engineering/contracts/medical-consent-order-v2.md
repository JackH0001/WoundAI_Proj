# Medical consent ordering v2 — candidate, not deployed

This protocol is for the medical app. Lite's enrollment/withdrawal epochs remain
separate. It does not change RGB-D, annotation, mask or measurement formats.

## State and intent

An authenticated `GET /api/v1/consent/state?code=...` returns
`protocol=medical-consent/2`, code, org, sequence, revision (SHA-256), status and
known image IDs. The endpoint requires `patient.manage`.

A newly confirmed restore observes this revision once and persists it with its
stable intent ID before POST. Background retries reuse that exact pair:

```json
{"protocol":"medical-consent/2","code":"WD-SYNTH","intent_id":"restore-example-01","consent_revision":"<64 lowercase hex characters>"}
```

POST `/api/v1/consent/restore` compares the observed revision with the current
tip. A different tip returns 409; clients must not refresh it automatically.
A fresh explicit confirmation is required. An offline restore that never
observed a revision cannot be silently converted into an online restore later.
Unversioned legacy restore requests return 428. Withdraw remains available to
legacy clients; new clients also send a stable intent ID.

POST `/api/v1/consent/withdraw` may advance over a competing restore. Whichever
request initially wins the storage race, that competing withdrawal remains the
final state. A later explicit restore may succeed only against its observed
current revision. Replaying a superseded intent returns 409 and has no effect.
An unchanged current-intent replay returns the same revision/sequence.

## Durable ordering

The legacy JSONL baseline is sealed into a create-only root under
`receipts/medical_consent/<subject SHA-256>.json`. Events occupy contiguous,
create-only slots under `receipts/medical_consent_events/<subject>/<sequence>.json`.
Each event binds its predecessor hash, subject, org, actor, action, intent and
known images. GCS uses the protected receipt bucket and `if_generation_match=0`;
LocalStore uses its exclusive immutable publication primitive. Process locks
and wall clocks are not the ordering authority.

Readers validate the chain and the sealed legacy baseline. Unknown/corrupt
events, gaps, changed legacy history and storage errors fail closed. Chains are
bounded at 4096 events; reaching the bound requires an explicit migration.
LocalStore's unpublished `.immutable-*` files are excluded from enumeration.

## Media, audit and migration limits

New withdrawals use logical exclusion: original bytes remain, while API access,
training eligibility and statistics consult the ordered state. Existing
quarantined bytes may be copied back during a conditional restore, never moved
or deleted; that copy does not itself grant access. Legacy JSONL and historical
receipts remain unchanged. Intent audit is written before mutation; the durable
event is the authoritative outcome even if the HTTP response is lost.

Do not run mixed pre-v2/v2 medical writers. After the first v2 event, rolling
back to a pre-v2 image is unsafe because it cannot read the new ledger. A rollout
must first prove every serving/training/export reader honors the ledger, then
use a v2-aware recovery image. Direct bucket exports must not bypass eligibility
checks. The old local queue-archive tool refuses to archive a store containing
v2 consent roots; it needs a separate version-aware migration.

This implementation scans fresh state; large-subject/long-history performance,
real GCS multi-instance behavior and device-to-cloud acceptance are not yet
validated. Local races and a mocked GCS create-only storage contract are not
production deployment evidence. Revision ordering for already in-flight medical
annotation uploads is a separate problem; this protocol orders consent only.
