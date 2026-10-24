# Lite renewed consent after completed withdrawal

Implementation candidate: iOS 1.0 (38). This document does not assert deployment,
real-device acceptance, IRB approval, or external release.

## User-visible behavior

After the server has confirmed removal of current live media, Settings offers
“閱讀說明並重新加入研究”. The user reviews a separate disclosure and explicitly
chooses renewed consent. Cloud confirmation and a durable local audit must both
succeed before the research-upload preference can turn on. Declining or a network
failure leaves media uploads off. No installation/key reset is used.

Renewal applies only to future captures. Existing local records, masks, pending
revision payloads and historical acknowledgements are retained. Previously
withdrawn records say they are local only and have no retry action. Deleted media
is not restored, and reopening or editing old records does not re-upload it.
The user may continue using local measurement/history without joining research.

## Signed protocol

`POST /api/v1/lite/research/rejoin` requires App Attest over the exact body, with
no extra fields: `anon_id`, `research_consent: true`, `previous_epoch`, `new_epoch`,
`consent_version: 2026-10-16.1`, `disclosure_version: 2026-10-21.rejoin.1`.
The existing authenticated installation must own the request. `legacy` is the
original research scope; a new scope is a random 32-character lowercase hex ID.

The previous scope must have a durable withdrawn fence, zero active writers,
and exclusively sealed empty media objects. The server records the original
fence, previous audit digest, explicit consent/disclosure versions, request digest
and timestamp before CAS-activating the new epoch. Concurrent renewal, an unknown
write outcome, or a missing/corrupt audit fails closed. Retrying the same intent
returns the same receipt. A retry cannot reactivate an epoch withdrawn later.

The receipt binds the owner, previous/new epoch, versions and audit SHA-256 and
states `scope: future_captures_only`, `historical_media_restored: false`.
The app preserves the exact previous withdrawal bytes and the receipt in its
protected local audit. Until acknowledgement, retry retains the same intent.
If the response was lost, the server may have accepted an empty new epoch; choosing
to remain offline does not assert cancellation of that unknown server outcome.
It still never enables media uploads or invents a different identity.

New multipart captures and JSON revisions include signed `consent_epoch`; no
field means legacy. Replies echo the epoch, which the app validates. The deletion
path for a renewed scope is `DELETE /api/v1/lite/data/<owner>/<epoch>` with an empty
signed body. Legacy deletion remains supported and confined to legacy data.
An old delayed upload/revision cannot enter the current scope; an old delayed
deletion cannot delete the new scope. Quota/attestation identity is unchanged.

## Storage, provenance and 3D consumers

Legacy physical names remain `lite_fenced/v1/<owner>/<kind>/<id>`.
Renewed names are `lite_epochs/v1/<epoch>/lite_fenced/v1/<owner>/<kind>/<id>`.
Fence journals are separately keyed by epoch. Never reopen old fences or remove
tombstones. Cross-epoch write tickets/pins are rejected.

The immutable RGB-D bundle format and source bytes remain unchanged. The epoch
belongs to the surrounding binding/manifest, not the depth samples. Consumers
must carry `(service, owner, consent_epoch, image_id, revision, digest)` together
and check current authorization before research use. A historical checksum match
or prior cloud receipt is not ongoing permission. A WoundAI3D diagnostic import
does not grant training admission or validate geometric accuracy.

The Lite candidate does not expose an administrator/reviewer console. This change
does not claim an epoch-aware management UI, global historical-media purge,
backup deletion, model unlearning, or restoration of withdrawn datasets.

## Historical consent versions and latest-build policy

Completed audits validate their own consent/disclosure strings and exact receipt
bindings, independently of today's consent text. An old completed audit remains
readable and withdrawable; it does not authorize uploads under a newer consent.
New renewal still requires current consent. Replaying the exact completed request
returns the original receipt/versions, not a rewritten historical receipt.
Malformed versions or request/receipt mismatches still fail closed.

The current rollout decision is latest-verified-build only, not a 35–38 allowlist.
Build 38 is a candidate until deployment and device acceptance complete. A valid
assertion from the upgraded app can retain its enrolled key/identity; obsolete
builds are refused without resetting identity or deleting audit/history.
See [Claude follow-up](../../docs/claude_safety_review_followup_20261022.md)
for test counts, remaining medical findings, and unvalidated network races.

## Verification and release gate

Synthetic signed HTTP tests cover exact RGB-D readback, mask revisions, duplicate
retry, old request isolation, multiple renewal/withdrawal cycles, concurrent
intent conflicts, corrupt audit, failed writes and unknown responses. iOS tests
cover journal persistence, exact signed payload/path, receipt mismatch, concurrent
taps, unchanged pending bytes, local-only display and absence of implicit consent.
Previously deployed registration-recovery behavior and tests are retained.

Deploy the compatible backend before requesting real-device renewed consent.
After the first renewal is committed, do not roll back to an epoch-unaware image:
retain the compatible privacy handler and stop media admission if necessary.
Before any renewal, the previous revision is a valid rollback. Never reset the
phone's journal, identity or cloud audit to make an acceptance test pass.

Actual TestFlight acceptance still requires the user's explicit renewed consent,
one synthetic future capture, a same-image mask revision and cloud readback.
Verify no old binding/pending item is transmitted and no RGB-D media is uploaded
again during revision retry. Public release waits for completion of that stage.
