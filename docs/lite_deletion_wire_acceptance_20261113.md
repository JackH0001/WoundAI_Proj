# Native account deletion wire acceptance — 2026-11-13

This extends the opt-in Swift/Python loopback harness. Production App/backend
sources are unchanged from `089fbb1`; this is additional integration evidence,
not a deployment or a completed account-data erasure claim.

## Scope

The actual `LiteNativeSession.deletion()`, `LiteAccountDeletionStore`, protected
file journal and HTTP service send native JSON to the composed Python routers.
Two independent synthetic attestation keys sign into the same synthetic Apple
identity. One recognized image and an original local photo/depth/contour record
are saved before the destructive confirmation is invoked by the test.

The transport drops the first HTTP202 response **after** backend intake commits.
The App retains its saved operation without a receipt and pauses research. The
backend graph and Swift session are then recreated from their existing stores.
Reloading the deletion journal emits no HTTP. Explicit retry must confirm the
same operation, request time and frozen two-grant inventory, without another
plan or audit mutation. A different deletion operation must be rejected.

Both installations must lose ordinary service access. Only a separate explicit
fixture worker advances fake Apple revocations, at one grant per call. Native
progress polling observes in-progress and confirmed revocation; repeated worker
or status calls must not revoke again. The UI still states that data deletion
is incomplete. It must not infer subscription cancellation from token revocation.

## Evidence requirements

- Exactly seven Release native tests, no failures or skips, and twelve fixture
  tests. Previous six scenarios remain included; these are not a full-App suite.
- The deletion scenario runs the model once and debits quota/attempts once.
  Two intake requests return202 for the same saved operation. Both installations
  remain registered; no replacement identity or interactive login is created.
- Before worker execution, Apple revoke count is zero. Two one-grant calls lead
  to exactly two fake Apple revocations; repeated calls leave the count at two.
- `deletion_readbacks` contains only hashes, counters and lifecycle metadata;
  the original service-result store hash stays unchanged. Original local index
  bytes, wound grouping, JPEG, Float32 depth bits and polygons are preserved.
- The receipt reports research withdrawal **requested**, account_deleted=false,
  subscription_cancelled=false, data_disposition=pending. Individual service
  and key disposition lanes remain not_started. This is not research cleanup.

## Earlier test corrections

The first fixture readback tried to JSON-encode SQLite BLOBs directly. Hashing
now preserves byte types/values and revisions deterministically. Another check
found that evidence quota depended on captured HTTP events; it now reads the
original immutable registration and ledger even after closure, without restoring
service admission. The first native run expected the legacy not_started receipt;
the composed backend correctly returns the v2 pending catalog. The test now
checks pending plus the separate not_started lanes, and still rejects any claim
of completed deletion. Product guards were not weakened; failed logs are retained.

## Boundaries and reproduction

No real Apple token, KMS key, account, research data, physical device or cloud
service is used. A test-only ephemeral bearer protects loopback control routes;
the production HTTP perimeter does not expose the worker. The normal signature
verifiers use synthetic roots. This does not validate TLS/IAM, real Apple
revocation, account-linked research media removal, backup retirement or legal
retention. Existing research withdrawal/rejoin coverage is a separate scenario.

Run `tools/run_lite_native_wire.py` on a dedicated simulator and
`python -m unittest tools/test_lite_native_wire_fixture.py -v` with the repository
backend test dependencies. Output must be a new directory. Original xcresult,
test summary/tree, source hashes and sanitized backend observations are retained
under the review workspace's `woundailite-account-deletion-wire-20261113/`.

Research/measurement JSON journals use iOS file protection and request backup
exclusion; photo/depth sidecars separately use AES-GCM. Earlier acceptance docs'
"encrypted journal/index" wording is corrected with this change.
