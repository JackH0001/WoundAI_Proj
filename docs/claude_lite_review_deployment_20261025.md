# WoundAILite independent review deployment preparation — 2026-10-25

Review/preparation only. No cloud mutation, new deployment, migration, cleanup,
OAuth change or store release is performed by this change. The previous bounded
GCS continuation authorization has been used and is not reusable.

## Change for a closed first deployment

`IapVerifier` accepts an explicitly empty role dictionary. It admits **no** human,
even when the Google IAP signature is valid. This allows the actual runtime and
container to start without inventing a Google subject or giving any real user
access before cloud isolation has been checked. Missing, malformed or invalid
role definitions remain startup errors. Both portal and reader independently
reject access before reader calls, source reads or access audit. Two new tests
exercise a valid ES256 assertion at both hops and malformed configuration.

The application source and the separately hashed deployment packet must both be
pinned. Cloud Build may not substitute a branch head. Its source tar contains
only committed `Backend/LiteReview` files; no medical app, model, credentials,
raw image, catalogue, role map or cloud baseline. Build output must be resolved
to an immutable registry digest before it is used by either service.

## Four distinct authorization gates

| Gate | Scope | Evidence required before execution |
|---|---|---|
| A | One build; two new private services and identities; protected empty config; bounded denial probes | Fixed commit/source/plan; successful CI; current baseline; single-run journal |
| B | Owner-only IAP and a bounded synthetic catalogue; exact source grants and separate create-only access audit | Actual Google subject, IAP/OAuth impact review, actual service audience/SA subject, explicit synthetic owner/epoch manifest, effective grants and rollback |
| C | Medical reverse-index discovery and additive migration, separately for MMH and Demo | Protected inventory/manifest; verified ownership; all writers paused and drained; policy/retention treatment; exact apply authorization |
| D | Cleanup dry-run, then one reviewed apply, then paused schedule and later enable | Correct Job identity adapter; approved retention; remaining live race/fault tests; recoverable-version inventory; alert delivery |

A does not authorize B/C/D. There is no allUsers/allAuthenticatedUsers grant on
the new services. Existing Lite candidate entry stays as the user authorized;
MMH/Demo and their IAM, OAuth, traffic and storage remain unchanged. A source-
access denial probe does not prove a successful two-hop IAP review session.

## Least privilege and configuration

The old `woundaiLiteBuildObjects` custom role includes DELETE and LIST. Do not
reuse it for this image. Propose new one-permission GET/CREATE custom roles,
resource conditions for the single source object and build log names, plus
repository-scoped writer only for one bounded build. The build identity is
currently disabled: enable only during that build, remove only this run's
bindings with etag protection, then disable and verify. No project Editor,
service-account keys or new project-wide role grants.

A's portal and reader get only their own Secret Manager resources. Reader also
gets the exact empty catalogue and 32-byte random cursor secret; portal never
gets the cursor or GCS rights. Secret versions are numerically pinned. Portal
invokes only the new reader; neither identity can read any existing bucket in A.
Temporary impersonation for effective denial probes is scoped to those two new
identities and expires within one hour; access tokens request 600 seconds.
ID tokens can remain valid up to their provider expiry: removing a minting grant
is not token revocation. Check remaining resource access separately.

For B, restrict security GET to an explicit finite set of consent-fence,
active-pointer and linked-renewal-audit object names. Never grant broad GET over
App Attest/challenge/credential state. If the list exceeds a reviewed condition
budget, stop and design a narrower data interface; do not silently expand scope.
Media GET similarly uses approved exact object names. No security LIST, source
mutation, training route or raw RGB-D download. Access audit is create-only in
its own bucket; preserve all receipts and disclose that this is not a locked
seven-year WORM epoch. The HTTP runtime never inventories an entire bucket.

## Observed limitations affecting rollout

Read-only metadata on 2026-10-25 found separate Lite/MMH/Demo runtime accounts
and buckets. The proposed review services, accounts and audit bucket have not
been deployed. Lite and MMH media currently have soft deletion disabled; Demo
media has a seven-day policy. A deletion cannot be described as reversible just
because the synthetic smoke bucket had recovery enabled. Do not alter production
bucket policy to make a test pass. Project-level legacy owner/editor bindings on
Lite buckets are an administrative trust boundary, not evidence that every
principal is isolated. New identities need inherited/effective denial checks.

No Cloud Run Jobs were observed in asia-east1. An existing `woundai-warmup`
Scheduler is ENABLED; it is unrelated to this plan and must not be changed.
Project and iap_web settings did not expose a custom OAuth setting; the installed
gcloud settings command does not support resource-type cloud-run. These reads
do **not** prove absence of an existing service-level client. Human access stays
gated: do not auto-generate a project OAuth client and accidentally change MMH.
Google's current documentation describes project-level custom OAuth setup for
out-of-organization users. Any required project OAuth mutation needs its own
review, or an architecture change before B.

## Rollback and original evidence

A is a first deployment; there is no previous review revision to restore. On any
failure, stop the single execution, revoke this run's invoker/secret/probe/build
grants, set the two services' ingress internal, disable their new identities and
the temporary build identity, and verify denial. Preserve partial resources,
source/image/protected config and logs for investigation. Do not delete buckets,
secrets, raw data, original synthetic evidence or audit to make the run look
clean. Record every failed containment step. Etag conflict means stop; never
replace another operator's full policy with an old snapshot. No automatic second
build or automatic restart after a failed run.

B failure also removes only the new source grants/human access and closes the
review entry. Never restore a withdrawn fence or revive an old epoch. Medical
migration failures retain their immutable intent/outputs and continue to require
writer drainage; roll forward only the same reviewed plan or a reviewed repair.
After a purge, never roll back to code that ignores purge claims. Pausing a
schedule does not cancel a running Job; cancel it separately, retain intent and
reconcile the pinned-generation outcome. Deleted data cannot be recreated by
rolling back service code.

## Migration and cleanup readiness

Lite discovery is a catalogue, not medical consent migration. No medical backfill
or cleanup worker may run against Lite epoch storage. Never delete permanent
fence markers or consent audit. A later bounded inventory may publish a catalogue
only after generation/size/hash and owner/epoch linkage checks; the existing
runtime rejects snapshots older than 24 hours. It is not yet a live auto-refresh
management platform.

MMH/Demo migration is additive, with source generation/hash checks and immutable
receipts. Unknown ownership and unsealed legacy events block completion. Publish
the completeness marker last; verify both ownership directions. Do not claim
`--writers-paused` alone drains suspended workers or cross-device requests.
The current Job cannot spoof K_SERVICE to pass the service identity guard.
A reviewed Job profile is still required. Cleanup has no chosen production
retention policy and no apply permission. Proposed eventual schedule is 03:30
Asia/Taipei, one task, parallelism 1, retries 0, 600 seconds, first PAUSED and
read-only. Separate executions may overlap; CAS and generation preconditions,
not scheduler frequency, provide safety. Scheduler uses OAuth to the Job API
with a separate invoke-only identity and no GCS rights.

The preserving GCS smoke source 28c350e passed 12 bounded assertions; original
objects/audit remain. Natural seven-day expiry and live crash/resume, late
replacement, hold/audit-write failure cases remain unverified. Do not present
that result as acceptance of the new portal or formal deletion jobs.

## Claude replay and official references

Run `python -B engineering/phase2/test_lite_review_portal.py` (37 tests).
The deployment packet contains a fixed source manifest, baseline summary,
permission matrix, executable-free approval plan, local checks, CI links and
checksums. No bearer tokens, real catalogue, research media, key files or OAuth
secrets belong in the public PR or Claude ZIP.

- [IAP for Cloud Run](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)
- [Secret mounts and runtime permissions](https://docs.cloud.google.com/run/docs/configuring/services/secrets)
- [IAM condition resource names](https://docs.cloud.google.com/iam/docs/conditions-resource-attributes)
- [Scheduling Cloud Run Jobs with OAuth](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)
- [GCS preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)
- [Soft deletion](https://docs.cloud.google.com/storage/docs/soft-delete)
