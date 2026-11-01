# Claude handoff: explicit expiry resubmission and rollout observation

Base: `8c7e44707aabb296a682760afa380f8a624bb45e`, draft PR #36.
This follows the findings pasted by the owner; the full referenced Claude report
was not attached locally. No merge, deployment, live bucket operation, original
data deletion, research upload, billing or store change occurred. The original
branch remains default-off for the new service flow.

## Findings and status

| Review item | This checkpoint | Remaining boundary |
| --- | --- | --- |
| §2-1 expiry vs rejection | Explicit reasons, signed expiry-only resubmit, native durable user intent/adoption, reusable button component, fresh quota check | Public UI activation / real-phone-cloud acceptance still pending |
| §2-2 active observation rollback | Read-only inspection, unavailable vs contradictory, advancement-only automatic rollback, exact-pause audited same-day resume | Trusted live provider/operator authorization enforcement pending |
| §2-3 cache pressure | Formal immutable-manifest and short-TTL live-observation specification | No cache or live adapter implemented; no performance claim |
| §2-4 cutover limits | Documented limits plus direct tests for day/list/v1 rows and midnight containment | Live operator runbook must enforce them before approval |
| G4 / medical follow-ups | Preserved as open | No grace/policy migration or medical N1/N2/N4/L1/L2 fix claimed |

## Before / after

An isolated snapshot of the baseline and the current source were exercised with
the same synthetic actions (the local evidence packet contains the script/logs):

| Scenario | 8c7e447 | New local source |
| --- | --- | --- |
| Unstarted reservation expires | `cancelled`, no reason; resubmit signing path unavailable | `reservation_expired`; explicit resubmit returns one retained authorization |
| Preflight rejection | Generic cancellation UI | Rejection explanation; no resubmit option or server successor |
| Active provider observation throws | `execute()` rolls back to paused and disables new provider | `ObservationUnavailable`; phase remains active, no provider mutation; new admissions fail closed |
| Complete but conflicting observation | Generic failure | `ObservationContradiction`, separately reportable |
| Paused rollout recovery | Always rejected by ordinary execute | Still rejected by ordinary execute; separate exact-generation audited same-day resume preserves counts |

The signed HTTP probe asserts one model call and one success charge after an
explicit expiry retry, including replay. It does not promise one *attempt*:
the abandoned attempt remains in the ledger, and the successor has its own
attempt/quota checks. Tests cover quota exhausted after authorization, competing
late claim CAS, lost replies, account/image/model mismatches and preserved old
generations. Unknown/running/completed/preflight-rejected work cannot resubmit.

## Local verification

| Suite | Result |
| --- | --- |
| New service resubmission | 33 passed |
| New observation / explicit resume / limits | 31 passed |
| Synthetic Python/native fixture parity | 2 passed |
| Complete Lite Python discovery | 899 passed |
| Local replay of prelaunch workflow | 867 executions passed, 13 shell steps successful |
| iOS Release full WoundLite | 279 passed, 1 documented simulator-only skip, 0 failed |
| iOS Release focused service / signing | 49 passed, included in full Release suite |
| iOS Debug focused service / signing | 49 passed, 0 skipped / failed |
| Mobile static logic / declared parity / release contract | All three commands passed |

Suites overlap and must not be added. The new 66 Python executions explain the
prelaunch change from 801 to 867; the 11 new XCTest cases explain 268 to 279.
The simulator skip remains exactly
`HealthDataBackupTests/testDeviceFileProtectionClassSurvivesAtomicReplacement`
(no simulator protectionKey). Native transport uses fixtures, not live Apple
attestation. Android source is unchanged and was not rebuilt. New-source GitHub
CI has not run at this local checkpoint; old 8c7e447 success is not reused.

The initial complete Python run had five failures because the sandbox denied
binding localhost test ports. The identical source passed after allowing the
loopback test. Those failure logs are retained separately. No real cloud service
was opened for these runs. Baseline comparison imports use an isolated archived
source tree so the old code cannot silently import current modules.

## Review map

- `Backend/Flask/lite_usage_ledger.py`: separate active receipt; proof derived
  from actual retained v2 lease cancellation. Existing read-only receipt retained.
- `lite_service_operations.py`: additive cancel reason without rewriting receipt.
- `lite_service_recovery.py`: immutable v2 explicit-resubmit authority; v1 G3
  remains supported. No random replacement on status or generic network failure.
- `lite_service_http.py`, `lite_attest_http.py`, `lite_attest_request.py`: strict
  input/owner/body/signing allowlists, opt-in path only.
- `lite_usage_rollout.py`: inspect/advance separation; paused generation/history
  validation; append-only intent; same baseline/ledger on explicit recovery.
- `iOS/WoundLite/LiteServiceOperation.swift`: explicit choice before dispatch,
  original receipt checkpoint, durable successor before prepare, crash recovery.
- `iOS/WoundLite/LiteServiceStatusView.swift`: opt-in button; disabled during
  action; calls the journaled client; no public capture integration or auto action.
- `engineering/phase2/test_lite_service_resubmit.py`,
  `test_lite_rollout_review.py`, `test_lite_resubmit_review_fixture.py` and
  `iOS/WoundLiteTests/LiteServiceOperationTests.swift`: independent failure cases.
- `tools/lite_resubmit_review_fixture.py`: deterministic synthetic HTTP fixture
  and executable one-call/one-charge + observation/resume assertions.
- `engineering/contracts/lite-resubmit-rollout-review-v1.md`: protocol, complete
  operator resume procedure, future cache gates and explicit launch blockers.

## Review questions

1. Can any non-expiry cancellation authorize a successor, including an old row
   or an age-only inference? Does expiry still compete with claim on the same CAS?
2. Is explicit user choice persisted before network dispatch, and is the returned
   successor persisted before prepare? Inspect failure/restart/double-tap tests.
3. Can an active read error or drift mutate provider/control state? Can a stale
   pause authorization reset usage or resume across midnight?
4. Does the proposed adapter distinguish immutable evidence caching from mutable
   admission observations? Do not mistake the specification for implementation.

Preserved limits: native successor chain at most eight; server only per-parent
uniqueness plus existing request/day bounds; result window unchanged; unknown
worker completion stays pending. Authorization hashes are evidence references,
not an operator access-control mechanism. No production activation is ready.
