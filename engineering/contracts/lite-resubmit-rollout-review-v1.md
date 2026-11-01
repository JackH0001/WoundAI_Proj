# Lite explicit resubmission and rollout observation v1

Follow-up to Claude's review of `8c7e447`. Scope: default-off service modules,
signed synthetic HTTP, local iOS simulator and injected-provider rehearsal.
No deployed route, public UI activation, real Apple billing, cloud adapter,
production migration, cleanup, store update or research-data mutation is enabled.

## 1. Terminal reasons and explicit user choice

The cancellation receipt schema and proof are unchanged. `cancel_reason` is an
additive response field derived from retained ledger evidence:

| Reason | Evidence | User action |
| --- | --- | --- |
| `reservation_expired` | v2 terminal cancellation proof, correct original lease deadline and retained history | Offer `重新送出`; no automatic successor |
| `preflight_rejected` | Original image-bound preflight proof | Explain rejection; do not resubmit this operation |
| `cancelled_unknown` / absent field | Other/legacy cancellation | Explain that the reason is unconfirmed; preserve record |

`active_receipt()` can CAS-expire only an unstarted v2 lease. Ordinary
`receipt()` remains a read-only API. Claim and expiry compete on the same daily
row; a running, completed, unknown-execution or missing-reservation operation
cannot use this resubmission path. The server never infers expiry just from age.

`POST /api/v1/lite/service/resubmit` accepts exactly `anon_id`, `operation` and
`image_sha256`, under the existing signed challenge/assertion, owner and 4 KiB
control-body checks. A separate opt-in recovery store/cutover is required.

1. iOS shows the expiry reason and obtains an explicit button action. Persist
   `resubmitRequested` before sending anything. Merely reopening/resuming/status
   checking a cancelled record must not request a successor.
2. Revalidate account, original input/model, original 24-hour read window and
   terminal expiry proof. CAS-create one immutable authorization per parent.
3. Persist/read back the server-generated successor mapping before returning it.
   Authorization v2 includes `reason=reservation_expired` and may be same-day;
   original G3 v1 remains prior-day missing-reservation recovery only.
4. iOS checks the retained receipt proof and parent request digest, preserves the
   original checkpoint and atomically adopts the successor before prepare/run.
5. Prepare rechecks entitlement and quota. Run needs a unique execution claim.
   Lost authorization/result responses and concurrent retries replay the same
   work. Retain attempted operations and receipt history; do not refund attempts.

No terminal result is resurrected, no history is deleted, and no original RGB,
depth or measurement record is rewritten. Native authorization history remains
bounded to eight generations. The server retains per-parent uniqueness and
existing daily/request bounds; a global chain-depth/retention policy remains a
separate launch requirement. An authorization hash is an audit reference, not a
standalone cryptographic signature; authenticated transport remains required.

## 2. Observation, advancement and paused recovery

`inspect()` is read-only. It reports phase plus:

- `confirmed`: complete values match the pinned plan and expected enabled state.
- `unavailable`: exception, missing fields or malformed observation; no claim of drift.
- `contradictory`: complete observation conflicts with the pinned plan or state.

Corrupt durable control is an error, not a healthy observation. New admission
fails closed when observation cannot confirm safety. Inspection and the active
branch of `advance()`/compatibility `execute()` never call rollback on observation
failure. An alert/operator decision is needed for active-state drift; this code
does not silently mutate the provider. Actual advancement failure still attempts
provider closure and durable pause independently and reports unconfirmed closure.

Explicit operator recovery procedure (local interface only, no live executor):

1. Read the exact paused generation and its SHA-256. Obtain separately approved
   authorization evidence; its hash is a reference, not proof of permission.
   A future operator adapter must enforce IAM/approval scope, not accept arbitrary
   user-supplied hashes as authorization.
2. Independently confirm the new provider is disabled, legacy writers remain
   disabled/drained, and baseline/account-map/fence hashes match. Require the
   original UTC cutover day. Preserve namespace, baseline, counts and all rows.
3. Append a resume-intent audit; CAS only the expected pause to prepared, retaining
   the entire ordered history; read it back and append completion evidence.
4. This step does not enable traffic. Explicit `advance()` revalidates frozen
   state and existing baseline rows before advancing. A partial install initializes
   only missing rows; it never resets usage or changes grants.
5. If an acknowledgment is lost, inspect first; do not repeat authorization with
   a guessed pause. A later pause needs its own exact-generation authorization.
   Cross-midnight recovery requires a separately reviewed baseline/plan.

History is bounded to 32 transitions. Resume is refused when insufficient room
remains for advancement and containment. Old v1 control records remain readable;
v2 adds audited paused-to-prepared transitions. In-flight previously admitted
claims may still complete once on their original ledger; pause is not atomic
revocation of every distributed request.

## 3. Formal adapter caching specification — not implemented

There is no live provider in this change. Do not optimize the current synthetic
provider by pretending cached data is independently observed cloud state. The
future adapter must satisfy these gates before activation:

- Cache only the immutable validated cutover manifest per process, keyed by
  namespace plus pinned SHA-256 and immutable object generation. A new plan/hash
  is a new cache key. Missing, corrupt or mutable phase data is not cacheable as
  a valid manifest. An immutable manifest must never contain live `active` state.
- Live provider observations and mutable rollout state need a short, explicit
  monotonic TTL (initial engineering candidate: at most 2 seconds). No stale-
  while-revalidate admission. Once expired, failed refresh blocks new admission;
  existing committed work can still settle on its original ledger.
- Single-flight refresh may combine simultaneous reads within one process. It
  cannot imply cross-process consistency or globally immediate rollback. Document
  TTL plus request-in-flight exposure; add a shared admission fence if stronger
  guarantees are required.
- Freeze/enable/disable/seal/resume must bypass caches and read fresh observations
  before and after mutations, invalidating local cached observations. Cache keys
  must isolate namespace, provider revision and account-map version.
- Test stale enabled observations after pause, process restart, cross-instance
  cache divergence, clock movement, contradictory vs unavailable refresh,
  single-flight failures and an in-flight claim. Record provider read counts and
  fail-closed behavior. No performance gain is claimed before these tests exist.

## 4. Cutover-day execution limits

The operator runbook must display and require these conditions before approval:

| Limit | Required behavior |
| --- | --- |
| Frozen baseline population | 1–1,000 unique verified accounts; reject larger/ambiguous lists before any writes |
| Cutover-day account outside that list | 503 / uninitialized day; do not assume zero prior use |
| Existing v1 day row | No v2 lease expiry (G2) or missing-operation fence (G3); never silently upgrade/reset |
| UTC cutover day | Freeze, baseline install, seal, enable and explicit resume finish on the same UTC day |
| Midnight during enable | Do not mark active; contain/retain the partial rollout for reconciliation |
| After cutover day | Only verified eligible accounts may create a new v2 daily row under the pinned policy |

The same-day bounds and 1,000-account limit are now directly regression-tested.
This is a deliberately bounded pilot procedure, not a general global cutover.

## 5. Reproduction and remaining gates

Use the dependencies and PYTHONPATH from `.github/workflows/lite-prelaunch.yml`:

```text
python -m unittest -v test_lite_service_resubmit test_lite_rollout_review test_lite_resubmit_review_fixture
python tools/lite_resubmit_review_fixture.py --report resubmit-rollout-probe.json
```

The first command has 66 executions (33 service, 31 rollout, 2 fixture checks),
including SQLite and the real GCS CAS adapter over a fake SDK. The probe requires
one inference and one successful charge, unchanged original mapping, read-only
active observation failure and same-usage explicit paused recovery. It performs
zero cloud calls. Native fixture parity plus journal/crash/UI-action tests run
under `LiteServiceOperationTests` in Debug/Release. The reusable SwiftUI status
component is compiled but not activated in the shipping capture/history UI.

Still required: real provider/cache adapter; verified-account enrollment and
restore; G4 Apple grace and policy migration; public service-flow integration;
physical TestFlight/cloud readback; bounded state retention; deployment approval.
Medical `ba970b1`-N1/N2/N4 and L1/L2 are not changed by this Lite-only checkpoint.
