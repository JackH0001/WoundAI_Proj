# Closed-account service result inventory and late-writer fencing

Scope: opt-in, local/synthetic validation only. No deployment, real cleanup,
store update, key destruction or public endpoint is enabled by this change.
Parent source: `0dbc0c2128b6ec4548d653322b5e27b8cb8c85fd`.

## Problem and before/after evidence

The account closure root indexed Apple grants, but not service operations. A
completed result remained stored. An already-running request could pass its
active-account check, then commit its mask after closure. The response was
blocked, but that did not dispose of the stored result.

`tools/lite_service_disposition_probe.py` runs unchanged against the parent and
the candidate. It uses generated Apple keys, real signed App Attest HTTP routes,
temporary SQLite CAS, synthetic JPEG/masks and a deterministic pause immediately
before the result CAS. It completes one result and starts one additional result.

| Observation | Parent | Candidate, catalog explicitly enabled |
|---|---|---|
| Result CAS arriving after account close | Wins | Loses to the same-key seal |
| Earlier current mask payload remains | Yes | No |
| Late current mask payload remains | Yes | No |
| Model calls across the two distinct operations | 2 | 2 |
| Retrying the closed operation | 401, no extra call | 401, no extra call |
| Usage ledger / research / registration | Unchanged | Unchanged |
| Original generated capture and saved receipt | Preserved | Preserved |
| Entire account deleted | Not claimed | Not claimed |

The candidate's internal receipt reports `current_sealed`, two inventoried slots
sealed, `account_deleted=false`, `historical_versions_erased=false`, and
`physical_backups_erased=false`. The old in-flight request receives 410 rather
than exposing a mask; later requests are blocked by the account fence.

## Implementation

- `AccountLifecycle(service_catalog=True)` creates an explicitly different v2
  lifecycle configuration and root. A v1 store cannot implicitly upgrade, and a
  v2 store cannot silently downgrade. Default remains false.
- The same root CAS contains the grant and service linked-list heads/counts.
  Closing freezes both atomically; the closure digest binds both inventories.
  Appending a grant preserves the service head, and vice versa.
- A new service operation passes quota preflight, then obtains a durable catalog
  slot before writing its result row. A retry finds the existing member. Quota
  rejection adds no catalog slot. Unindexed existing rows are rejected rather
  than adopted as proof of a complete baseline.
- G3 and explicit-resubmission successors use the same admission rule before
  their create-only write. Thus a close between admission and creation still
  includes the missing slot in the frozen inventory.
- `ServiceResultDisposition` is an injected internal worker, with separate
  state/audit/result/ledger stores and batches of 1–100. A per-account chain has a
  hard 100,000-operation bound requiring explicit maintenance beyond it. Normal
  admission uses membership receipts; there is no global-account scan.
- Audit intent precedes every change. The worker CAS-replaces current v1/v2
  results, or creates a seal at an admitted but missing slot. A v3 seal contains
  no result, mask, image or depth payload. It pins the closure and previous
  revision/hash. It is permanent: deleting it would reopen the generation-zero
  slot and invalidate the late-writer guarantee.
- Current readers treat seals as terminal; old readers reject the unknown schema.
  A writer with a prior revision cannot overwrite a seal. Concurrent cleanup
  workers converge using idempotent per-slot receipts and CAS checkpoints.
  Readback resolves lost acknowledgments; missing original audit, corrupt data
  or uncertain writes do not become success or a reset.
- Result responses recheck the live account state immediately before returning.
  This blocks a closure that occurs during receipt/settlement reads. An HTTP
  response already sent before closure cannot be recalled.
- This worker does not settle, refund, reset or reinterpret billing. An in-flight
  model may already have executed once; its ledger remains an audit record.
  Unknown execution is not re-run or guessed to have failed.

## Validation

- 46 new tests, all pass. These include two real signed synthetic HTTP tests,
  cross-day successor races, two devices, other-account isolation, 125-operation
  batching, concurrent workers, missing slots, stale writers, lost CAS replies,
  audit outages, malformed checkpoints, old retention plans and mode migration.
- Full Lite discovery: **1,528 passed**.
- Local replay of prelaunch: **1,496 executions / 24 steps passed**.
  These suites overlap; do not add them together.
- Actual `GCSStateStore` with a fake generation-aware bucket confirms all writes
  carry generation preconditions, stale and create-only writes lose after a
  seal, and old generations retain their original bytes. This is adapter-level
  validation, not live GCS cleanup evidence.
- No iOS file changed in this increment; no new simulator/device claim is made.
  Remote CI applies only after a separate approved push.

## Remaining account-deletion gates

This is ONE disposition lane, not full account deletion. Native and public
receipts continue to report incomplete disposition. The future coordinator must
combine independently verified lanes and never infer completion from this lane:

| Data lane | Present status |
|---|---|
| Current service-result masks | Frozen inventory + current-slot sealing implemented here |
| Historical service-result generations / backups | Inventory/policy/purge or encrypted-erasure proof still required |
| Apple grants | Existing bounded revoke + opt-in per-account key custody; true KMS remains unprovisioned/unverified |
| Research imagery, depth and masks | Existing withdrawal fences are separate; account/epoch association and authorized disposition still needed |
| Identity, subscription bindings and recovery metadata | Retention/deletion catalog and handling still needed |
| Usage, receipts and retained audit | Preserved; justified retention/access restrictions and final user disclosure still needed |
| Native final completion | Must wait for the complete disposition catalog; not enabled here |

Before enabling on any existing store, inventory all prior account results,
verify/fence and drain every legacy writer, reconcile identities and lost
receipts, and obtain an explicit fixed-source migration/deployment approval.
Do not attach a new empty catalog to existing production records. Fresh startup
must use independently verified empty result/account namespaces. A rollback may
pause new service work; it must never delete seals, reopen a closed root, reset
quota or launch a writer that bypasses the catalog.

## Claude replay

With repository test dependencies installed, from the repo root:

```text
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local python -m unittest -v test_lite_service_disposition
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local python tools/lite_service_disposition_probe.py --output after.json
```

On Windows use semicolons in `PYTHONPATH` and PowerShell `$env:` assignment. The
same probe can be copied outside the checkout and run with the parent's module
paths to produce `before.json`; it detects the older fixture without forcing the
new feature on. Preserve both artifacts. No cloud credentials are needed.
