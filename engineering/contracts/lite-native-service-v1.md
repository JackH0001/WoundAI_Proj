# Lite native service journal and recovery v1

Status: local implementation/tests, not enabled by app startup or the measurement
UI. Backend service routes remain opt-in and undeployed. No build-number change,
TestFlight upload, live identity registration or real payment in this stage.

## Flow

The caller must first retain its normalized JPEG in the existing encrypted image
store and assign a stable capture ID (32 lowercase hex). `enqueue` checks JPEG
dimensions/orientation and stores only the hash, dimensions and identity binding;
it does not claim to have saved the photograph or a clinical/history record.
The capture ID is the service operation ID. Reusing it with different image bytes
or installation is a conflict, not a new attempt. Existing research IDs/epochs
and existing history storage are not migrated, cleared or repurposed.

`LiteServiceClient` explicitly receives the origin, signed transport and journal.
`LiteSignedServiceTransport` uses only an existing App Attest identity, explicit
service permission and the three exact service paths; it never enrolls another
identity on failure. Research transport still requires research consent. The
production caller must provide real service permission/account scope; passing a
test permission closure is not production consent/account implementation.

Before every network call the journal durably changes its next action to status.
The data returned to UI is confirmed only after its receipt/result is validated
and the new local state is durably written. A failed local write throws, rather
than presenting success or sending another operation.

| Original situation | Next explicit resume |
| --- | --- |
| New local operation | prepare, then run only when ready |
| Timeout or app restart after dispatch | status first, without reading JPEG |
| Status says completed | verify/persist result, no image/model retry |
| Status says ready | load original matching JPEG, send same operation |
| Status unknown; no pinned receipt | prepare same ID/hash, never generate ID |
| Status unknown; pinned receipt exists | stop for reconciliation |
| Pending | status only, no automatic model retry |
| 429 | preserve work, distinguish quota/capacity, honor bounded wait |
| 401/403/503 | preserve work, query later, no new enrollment/anonymous fallback |
| 409/410/invalid result | stop, retain local work, explain reconciliation/expiry |
| Missing/mismatched original image | stop before run; retain journal |

One resume performs at most three calls (status → prepare → run). No background
poller or automatic launch call is added. Waits are clamped to 1–3600 seconds;
old excessive saved deadlines are shortened on resume. Concurrent calls to the
same client return busy; the shared signing actor continues to serialize actual
assertion counters. Journal CAS plus a nonblocking OS file lock prevents two
journal instances from replacing each other's state. The same capture ID is
also used server-side, so it is not converted into a second billed operation.

## Result and presentation contract

- Match operation, original day, request digest, admitted grant and outcome.
- Check exact `result_json` UTF-8 bytes against result SHA-256, then decode and
  compare with `result`. This avoids Swift/Python float reserialization drift.
- Match image dimensions to original normalized JPEG; bound polygon count,
  points, coordinates, confidence field and result/body size.
- `confidence` remains the existing mask occupancy field, not a calibrated
  accuracy probability. This work does not validate area/depth accuracy.
- Success message: 「辨識結果與回執已核對；尚未存入量測紀錄或研究庫。」
- Waiting message differentiates server work, capacity, exhausted quota,
  authentication/network, missing image and expired/inconsistent result.

These messages are tested presentation values. They have not been wired into
the public screen; no screenshot/physical UI acceptance is claimed.

## Storage

`LiteServiceFileJournal` is a new, explicitly supplied directory. It never scans
or edits the existing history, consent journal or image store. Each bounded JSON
record contains public identity references, hashes, state and possibly polygons,
so it is still sensitive local health-derived data. It uses `HealthDataFiles`
atomic writes, file protection and backup exclusion; symlinks are rejected.
The simulator verifies exclusion/CAS/preservation, not actual device protection.
No retention deletion is performed in this stage.

## Remaining activation conditions

1. Verified service-account registration/restore/paid activation and explicit
   service-use permission, separate from research choice.
2. Production singleton/client factory and UI integration after old-quota
   freeze/baseline migration. Old `/segment` must not add another allowance.
3. History save and optional research annotation/depth linkage to the verified
   result, without another model call; native research choice still independent.
4. Backend result-cache lifecycle and unknown-work reconciliation below; real
   Apple Sandbox, bounded cloud acceptance and TestFlight workflow acceptance.

## Lifecycle progress, no cloud cleanup executed or approved

The explicit single-operation compactor is implemented with synthetic tests in
`lite_service_retention.py`; see `lite-service-result-lifecycle-v1.md`. It now
covers terminal payload removal, known-result settlement and unstarted
cancellation with exact generation/hash plans and intent/completion evidence.
The table below continues to define the broader rollout requirements; no
cloud scheduler, generation purge or unknown-worker resolution is enabled.

The current 24-hour read window is NOT physical deletion. Do not delete the
whole operation mapping: that can let an old ID become a new operation/day.
The reader now supports a v2 compact terminal tombstone
with original operation/day/input/result hashes and audited outcome, separate
from payload availability. Retention duration and user disclosure remain to be
approved before activation. Existing v1 results remain readable; only an
explicit authorized compactor invocation writes v2.

| State at expiry | Required decision and race test |
| --- | --- |
| Result saved, settlement unconfirmed | Reconcile the same hash/outcome first; no new model execution |
| Terminal, verified settled | CAS-strip polygons only after durable audit intent; retain tombstone; retry safely after lost acknowledgement |
| Prepared but never claimed | CAS-cancel reservation and audit before compacting; concurrent claim must make only one branch win |
| Claim exists, no result | Do not infer failure from age; require worker/result evidence and an audited resolution policy |
| Mapping exists, no quota entry | Confirm no reservation is racing, fence the mapping before compaction |
| Research withdrawal/rejoin in parallel | Service cleanup must not read, delete or restore research buckets/epochs |

Each executor needs an exact service namespace, bounded object/byte/deadline
limits, expected generations and hashes, dry-run manifest, append-only evidence,
and failure containment. Existing media/audit buckets remain out of scope.
No scheduler, GCS purge, TTL policy or cloud IAM is created here. The pending
resolution policy must be completed before users can face indefinite waiting;
the present client intentionally makes no false completion or refund promise.

## Replay

Python generator uses actual signed Flask fixture replies. Only its random
installation identifier is replaced with a fixed synthetic native owner; reply
fields and result hashes are unchanged. Separate byte fixtures cover the three
App Attest paths. iOS uses those checked-in fixtures; Python verifies them against
the current backend. Native network is simulated; Python has real localhost HTTP
coverage. This is not a phone → Apple → deployed-cloud end-to-end test.

Run `test_lite_native_service_contract` with the Linux workflow dependencies.
Run `WoundLiteTests/LiteServiceOperationTests` on iOS simulator in both Debug and
Release; manual CI also includes the existing signer/research business tests.
