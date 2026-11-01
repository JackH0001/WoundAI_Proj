# Claude review: G3 native recovery and cutover/rollback simulation

Baseline: `38aa2064595e31e67acc12048aa219e6d49c6c92` on PR #36. This is a
local opt-in successor to that checkpoint. **No deployment, live GCS operation,
store update, original-data deletion or production quota reset occurred.**
The shipping UI/startup remains unchanged and commerce stays disabled there.

## Before and after

| Situation | Baseline | This opt-in implementation |
| --- | --- | --- |
| Mapping exists, pre-reservation write failed before midnight | Initial 503; old retry/run 409 and status `awaiting_reservation` | Original-day exclusion, immutable authorization, durable native adoption, then one successor prepare/run |
| Reservation committed but reply lost | Must not treat it as absent | Original ticket completes: one model call, original-day charge, zero new-day success charge |
| Eight concurrent recoveries and four runs | No authorized successor flow | One successor, one execution claim, one success charge, one attempt |
| Authorization/successor reply lost | Native journal cannot adopt | Replay same mapping; restart status recovers it without requesting another photograph |
| Native disk failure before/after adoption | No generation protocol | No successor dispatch before durable adoption; afterwards status uses that same successor |
| Cutover baseline already used 1 success / 3 attempts | Core manifest only; no transition executor | Baseline preserved across activation and pause, including partial/lost-reply scenarios |
| Failure while enabling or closing new route | No provider/phase rehearsal | Try provider closure and durable pause independently; unconfirmed closure/audit is reported, never claimed successful |
| Claim admitted just before pause, CAS later | In-flight boundary untested | It may finish exactly once on the original ledger; no legacy allowance is reopened and no counts reset |

The actual signed HTTP probe reports `g3_model_calls=1` and
`g3_success_charges=1`. The original mapping remains byte-for-byte equivalent
in the tested store. Fake GCS tests verify pre-existing generations remain
unchanged and no delete calls occur. Photos/depth/history stores are outside
these components; only synthetic input was used. This does not substitute for
a future physical phone/cloud readback of those original measurement records.

## Source map

- `Backend/Flask/lite_service_recovery.py`: create-only per-parent authorization,
  original-day fence readback, unique successor, same account/image/model.
- `lite_service_operations.py`, `lite_service_http.py`, `lite_attest_http.py`,
  `lite_attest_request.py`: opt-in status/recover and signed endpoint admission.
- `Backend/Flask/lite_usage_rollout.py`: bounded frozen baselines, durable phases,
  audit intent/completion, injected provider, terminal pause and no ledger reset.
- `iOS/WoundLite/LiteServiceOperation.swift`: backward-compatible v1 reader,
  v2 recovery checkpoint and atomically persisted successor before dispatch.
- `iOS/WoundLite/LiteAttestRequest.swift`: exact signed recover path.
- `tools/lite_cross_day_fixture.py`: signed synthetic Flask responses for Swift
  replay plus cutover baseline/pause evidence; no real model or cloud calls.
- `engineering/contracts/lite-cross-day-recovery-v1.md`: protocol, bounds,
  failure matrix and activation requirements. Existing contracts link this stage.

## Verification, local only

| Suite | Result |
| --- | --- |
| New G3 cases | 41 executions: 20 scenarios on SQLite and GCS adapter, plus generation preservation |
| Cutover/rollback | 35 executions: 17 scenarios on both stores, plus generation preservation |
| Python → native fixture parity / retained baseline | 2 passed |
| All Lite Python tests | **833 passed** |
| Local replay of prelaunch workflow | **801 passed**, 12 shell steps succeeded; overlaps the 833 |
| iOS Release full WoundLite target | **268 passed, 1 skipped**, 0 failed |
| iOS Debug focused service/signature tests | **38 passed**, 0 failed |
| iOS Release focused service/signature tests | **38 passed**, included in full Release coverage |

The one skip is exactly
`HealthDataBackupTests/testDeviceFileProtectionClassSurvivesAtomicReplacement`:
the simulator supplies no protectionKey. The repository's xcresult verifier
confirmed that reason and reports physical file-protection acceptance pending.
Xcode 27.1 / iOS 18.6 simulator; Python 3.12.14. Android source is unchanged and
was not rebuilt. New-source GitHub CI has **not** run; previous-head success is
not attributed here. The local workflow reuses recorded dependencies rather
than redoing checkout/setup/install/upload actions.

Initial development runs exposed a missing new-day ledger when a successor
mapping had already been created. `prepare` now performs eligible create-only
day initialization before receipt lookup. The new full regressions include that
case. Earlier failure logs are retained separately, not counted as final passes.

## Review focus and limits

1. Old reservation and fence compete on one row. Missing or corrupt state is
   not proof of absence. Existing running/settled/expired work cannot rotate IDs.
2. A response must not expose an unpersisted successor. A local write must finish
   before the client sends that successor. Check before/after crash tests.
3. Result cleanup and recovery retain mappings/authorizations. No physical
   erasure/TTL scheduler is introduced. Authorizations need a retention policy
   before launch; the native chain is bounded to eight generations, while the
   server has per-parent uniqueness and existing request/daily bounds rather
   than a global chain-depth cap.
4. The live provider is **not implemented**. Synthetic observations are not cloud
   evidence. Production must fence every old revision/worker, drain in-flight
   legacy work and independently verify baseline/account-map hashes.
5. Pause cannot atomically revoke previously admitted work. Preserve its original
   ledger and settle it once. If provider/durable pause/audit is unconfirmed,
   report the failure and reconcile; never return success based only on intent.
6. Public capture/history integration, stable account enrollment/restore,
   Apple grace/product-policy migration, real App Attest and device acceptance
   remain gates. Medical ba970b1-N1/N2/N4/L1/L2 are unchanged by this stage.

## Replay for Claude / Windows

Use the workflow-pinned Python dependencies, including
`engineering/phase2/requirements-commerce-test.txt`, in an isolated environment.
The offline bundle requires baseline `38aa206` already present; verify its
commit and the packet SHA-256 manifest before applying to a separate checkout.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = 'tools;engineering/phase2;Backend/Flask;.'
python -m unittest -v test_lite_service_recovery test_lite_usage_rollout test_lite_cross_day_fixture
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
python tools/lite_cross_day_fixture.py --report "$env:TEMP\lite-g3-$([guid]::NewGuid()).json"
```

The focused command runs **78** tests. Windows path spelling is portable here,
but this stage did not execute a Windows host or Python 3.11. iOS needs Xcode:
generate `project.institution.yml`, test WoundLite Release and the focused Debug
`LiteServiceOperationTests` / `LiteAttestRequestTests` on an installed simulator.

No remote push/PR update is included in the prior exact-source authorization.
The local review packet contains the fixed commit, patch, Git bundle, changed
sources and evidence so review can proceed without a deployment or data upload.
