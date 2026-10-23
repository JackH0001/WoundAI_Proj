# Bounded synthetic GCS acceptance runner — 2026-10-23

This adds a runnable **first subset** of the broader operations acceptance plan.
It has only been tested offline. No cloud resources, identities, policies, jobs
or schedules have been created by this change. Existing research data is outside
its scope. Preparing a manifest does not authorize executing it.

## Fixed disposable scope

- Project `woundai-jackh001` (number `421209514056`), region `ASIA-EAST1`.
- New buckets `woundai-consent-smoke-20261023-media-421209514056` and
  `woundai-consent-smoke-20261023-audit-421209514056`.
- Prefix `consent-ops-synthetic/20261023`; reject reuse of any bucket containing
  live, noncurrent or recoverable objects, even outside the prefix.
- Dedicated executor `woundai-consent-smoke@woundai-jackh001.iam.gserviceaccount.com`.
  The run requires service-account/impersonated ADC naming exactly that account;
  user ADC, MMH/Demo application configuration and Cloud Run environments fail.
- Buckets require uniform IAM, public-access prevention enforced, versioning,
  soft deletion **604800 seconds**, no retention lock/policy, lifecycle deletion,
  default event hold or export logging. These are proposals for **new** resources,
  not modifications to existing MMH/Demo/Lite buckets.
- Exact custom roles `projects/woundai-jackh001/roles/consentSmokeMedia` and
  `.../consentSmokeAudit`, one binding each to the executor. Both need
  `storage.buckets.get`, `storage.buckets.getIamPolicy`, `storage.objects.get`,
  `storage.objects.list`, `storage.objects.create`; only Media adds
  `storage.objects.delete`. No bucket update/delete/IAM or object update rights.
  Preflight also probes effective permissions to catch inherited grants for the
  listed operations. This is not an inventory of every project permission.
- No irreversible audit lock: receipts are application-immutable and the run
  identity cannot delete them, but an administrator still can. **Not WORM.**

Provisioning these resources/roles, granting temporary impersonation to the
approved operator, and the scoped test-media deletion require new authorization.
No service-account key file is needed. Review existing role/account collisions;
do not repurpose an existing identity with unrelated grants. Revoke only this
run's temporary impersonation grant when it ends; retain all data/audit. No
automatic whole-bucket cleanup or policy changes are included.

## Execution contract

1. Commit a clean checkout. `--prepare <new-file>` writes a mode-0600 manifest
   with a random run ID, exact commit/file hashes, fixed scope and 24-hour expiry.
2. Review the manifest and its canonical SHA-256; get authorization for that run.
   The CLI digest is an operator confirmation, **not an authorization signature**.
3. After resources/credentials are approved, use the explicit execute form:

   ```sh
   python tools/medical_consent_gcs_smoke.py --execute \
     --manifest <approved-manifest.json> --expected-sha256 <canonical-digest> \
     --evidence-dir <new-protected-evidence-directory>
   ```

4. Two independent storage clients use a shared local budget and scoped SDK
   capabilities. Before any fixture write, both clients check metadata, IAM,
   effective permissions and complete bucket emptiness (including soft versions).
   A create-only audit claim with the exact run manifest arbitrates two different
   runs that might pass the initial empty check concurrently.
5. Audit writes are create-only. Media replacement requires a previously observed
   generation; deletion requires an inventoried generation and known media path.
   Staging-binding inventory is read-only. There are no bucket/IAM update methods.
   Permission/policy checks run again before every write or deletion.
6. Limits: 1,000 attempted mutations (uploads **and generation deletes share this
   cap**, including failed attempts), 16 MiB attempted upload bytes, 20,000 SDK
   operations/list pages, 1,000 entries per listing, 10-second request timeout,
   no SDK retry and a 20-minute budget checked before calls (and manifest expiry).
   Authentication/transport setup or an in-flight call can exceed the checkpoint;
   the approved operator must also enforce a 20-minute process timeout. The
   reported SDK counts are **not exact HTTP request counts or a currency cap**.
7. An error preserves all fixtures, partial progress and receipts; exit 2. No
   auto-retry, cleanup or failed-bucket reuse. A successful bounded run returns 0
   with `bounded-scenarios-passed-expiry-pending`, never “all cleanup accepted”.

A completed bounded run also writes `object-readback.json`: names, generations,
byte counts and SHA-256 of every remaining synthetic live/noncurrent object.
Soft-deleted versions are inventoried without restoring or reading their bodies.
All evidence stays local; no patient media is copied into the review package.

## Implemented scenarios

- Restore on the second client before cleanup: media retained; stale restore 409;
  same intent returns the same sequence/revision with the replay flag.
- Competing restore/purge requests based on the same withdrawal revision:
  exactly one sequence winner, followed by the corresponding media disposition.
- Concurrent cleanup workers: one purge sequence, generation-pinned cleanup of
  two versions, and truthful `recoverable_media_pending`.
- Renewed consent after purge: new synthetic media survives another old cleanup.
- Read-only migration plan and additive apply/readback, preserving the audit
  run claim; already present index markers are not removed to manufacture history.
- Same completed cleanup run returns its receipt without adding consent events.

**Deferred live tests:** forced process crash/resume, late external generation
write, object holds, permission loss during a write, policy-zero transition and
natural soft-retention expiry. Several are covered by offline production tests;
that does not make them live-service passes. A follow-up needs a scoped plan and
must not silently change the first-run policy. Soft-deleted versions are expected
to remain for seven days; no mocked clock substitutes for a real expiry check.

## Formal cleanup Job identity

`validate_job_candidate()` is an **offline structural checker**, not runtime
identity verification. It requires separate proposed worker and Scheduler SAs,
an image digest, approved-policy hash, one task, no retries, at most 600 seconds,
paused schedule, dry-run and the existing `locked-seven-year` audit mode. It
rejects MMH/Demo unlocked modes and service identity substitution. No new Job
adapter is added to production. Actual runtime identity, image/IAM, locked audit
epoch and alert delivery remain predeployment gates. Existing MMH validation
buckets have not been shown to meet that formal epoch requirement.

## Independent review

Run `python -m unittest discover -s engineering/phase2 -p test_medical_gcs_smoke.py`.
The complete runner also executes against the offline SDK model. Review the
literal bucket/prefix/identity checks, no-ADC test guard, inherited-permission
probe, audited run claim, lazy pagination and missing-permission behavior. The
synthetic adapter is not in `Backend/Flask`, the Lite build allowlist or any
application `get_store()` mode. Do not call its successful unit tests a GCS run.

References: [GCS preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions),
[soft-delete retention](https://docs.cloud.google.com/storage/docs/soft-delete),
[Cloud Run Job scheduling with OAuth](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).
