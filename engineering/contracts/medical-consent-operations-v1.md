# Medical consent operations — candidate, not deployed

This extends medical-consent/2 after review of 5c8e414. Lite enrollment epochs
and raw RGB-D formats are unchanged. No existing research media are deleted by
the code-update task. Cleanup below is exercised only against synthetic data.

## Scoped reads and index completeness (P2)

`receipts/medical_consent_images/<image_id>/<subject_hash>.json` contains a
create-only code/org reference. All known image references are published before
an intent's sequence slot; a writer interrupted before the slot can leave an
extra reference but cannot leave a committed withdrawal missing its reference.
Image/annotation guards consult related codes only, plus queue/legacy bindings.
They do not enumerate the root namespace or read other subjects' histories.
No cross-request consent cache is used; each admission guard refreshes state.

`receipts/medical_consent_index/v1.json` seals completion. An empty store may
initialize it before its first event. Existing v2 roots with no marker fail
closed for image lookup/write until an offline backfill completes. Pause every
old/new writer, verify all chains, backfill references, verify unchanged roots
and tips, and only then publish the marker. Runtime never lazily declares an
old partial index complete. A root referenced by the index but missing is a
fault, not new/restored consent. Index access errors block that image.

Bulk export/statistics still enumerate subjects. They load the legacy snapshot
once and isolate subject errors. Large legacy logs, long per-subject chains,
GCS latency and whole-service load require separate deployment acceptance; the
synthetic read-budget test is not a cloud latency/load benchmark.

## Subject fault isolation (P3)

Malformed JSON, gaps, scope/hash mismatch or a changed sealed baseline exclude
the affected code and known image siblings. Image-index references preserve
that exclusion even if the root is unreadable. Bulk readers map corrupt root
filenames back to queue/legacy subject hashes and expose a fault count. Other
subjects can continue. Failed cleanup subjects are reported and do not stop
other cleanup jobs. A storage-wide outage still fails closed.

No locked object is overwritten/deleted to "repair" consent. The original
bytes and fault evidence remain for review. A corrupted subject is NOT silently
restored: it requires operator investigation and a separately reviewed additive
recovery/migration, including evidence of the last valid withdrawal. This
candidate isolates faults; it does not provide an automatic consent reset or
claim to recover arbitrary corrupted WORM histories.

## Invalid historical image references (P4)

A malformed image ID in a legacy queue is not an object path and cannot stop
code-level withdrawal. Valid siblings are indexed; rejected values are counted
and hashed in the immutable event and intent audit. The original queue is not
rewritten. Explicit malformed client image IDs remain 400. Unknown/unresolvable
legacy references require operator disposition; their media are not guessed.

## Cleanup with retained evidence (P1)

Withdrawal receipt means access/export/training are excluded. It does NOT mean
media have been moved or physically destroyed. Mobile UI, manual and IRB draft
use that distinction. Approved policy is supplied as JSON `{id,
retention_seconds}`; there is no implicit production retention period. 30 days
is a synthetic test/example, not an assertion about MMH configuration.

An operator worker runs `python -m medical_consent_maintenance media-cleanup
--policy <approved-policy.json>` (dry run), then separately authorized `--apply`.
The repository wrapper is `tools/run_medical_consent_maintenance.py`.
For an expired withdrawal it CAS-appends an immutable `purge` event against the
current tip. A restore winning first makes the claim conflict without deleting
bytes. A purge winning first permanently excludes its old image IDs, including
after later explicit reconsent. New media from renewed consent remain possible;
old image content cannot be silently revived. API callers cannot submit purge.

The worker records inventory intent, deletes only allowlisted media keys, then
reads back inventory and records outcome. It covers images, quarantine,
staging, tissue/wound masks, raw/PNG depth and metadata, including GCS noncurrent
generations with generation-pinned deletion. Audit/consent/annotation ledgers,
account objects and unknown references are never deletion targets. Local tests
also protect other subjects. Failed/partial deletion resumes from the immutable
claim. Every run rechecks old claims, catching late in-flight orphan writes.

GCS soft-deleted versions are checked separately: while recoverable, outcome is
`recoverable_media_pending`, never a destruction-complete claim. Once absent,
`media_versions_absent_at_check` describes only that checked store and moment;
it does not cover backups, exports or trained models. Retention holds block
apply. Unsupported SDK/policy/inventory failures remain pending. Old events
without trusted server timestamps are `blocked_missing_timestamp`, not assigned
an invented deletion age. Corrupt subjects are `blocked_fault`.

## Deployment gates (still outstanding)

- Review legal/IRB retention periods and retained annotation/audit disclosures.
- Verify exact media prefixes/versioning/soft-delete/holds on each service.
- Enable a scoped scheduled worker, monitor nonzero exit/pending/faults and
  last-success age, test interruption/retry/restore races with real GCS.
- For existing candidates, pause writers and complete index backfill; include
  whole-store inventories and unresolved legacy references in review evidence.
- Roll out all API/training/export readers and a recovery image supporting this
  extension together. No mixed pre-update writers or pre-purge rollback.
- Exercise a current and older delayed mobile intent end to end, confirm no
  cross-subject impact and read back all receipts before patient research.

No scheduler, IAM, retention policy, cloud image or app distribution changed in
this revision. Code tests do not complete these deployment gates.
