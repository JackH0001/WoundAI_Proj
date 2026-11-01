# Claude review: stable Lite subscription policy transition

Baseline `137c884515ba4bb90361bc5b58145ebac60b6a80`. Local continuation only;
default-off, no startup/public route/native source or build-number changes.

## What changed

- `lite_subscription_migration.py`: stable account slot, explicit inventory plan,
  trusted authorization/writer-fence callbacks, fresh target Apple verification,
  immutable intent/completion, exact-generation pointer CAS and retry recovery.
- `lite_service_subscription_links.py`: preserve the original v1 schema/key but
  honor a stable-account guard; legacy reads/writes must not bypass migration.
- `lite_subscription_authority.py`: optional bounded SERVER product catalog;
  default still accepts only the existing product. Signature/type/owner/current
  product verification remains exact. All migration policies share ownership store.
- Prelaunch adds the 36-case migration group and signed HTTP probe. The existing
  packaging/startup check still passes with commerce disabled.

## Verified before / after

The separate frozen `137c884` module replay uses a deliberately wrong positive
app ID (a changed policy fingerprint, not an actual Apple product migration).
Before: old policy-key lookup misses the existing association, returning None;
ServiceAccounts then yields free limit 2. After importing into the stable reader,
the same app-ID typo raises StateUnavailable; the old association is preserved.
This is a synthetic configuration regression, not evidence about actual Apple
Sandbox product transitions. Supported new-product behavior is tested separately
using signed `com.woundai.lite.synthetic.next` fixtures.

## Local validation

- Full Lite Python suite: **969 passed** (previous 933 + 36 new).
- Prelaunch commands replayed locally: **937 executions / 14 unittest commands**
  (previous 901 + 36). Full Lite and prelaunch overlap; do not sum them.
- New group: **32 state/authority/concurrency tests + 4 HTTP probe assertions**.
- Focused new + existing association/authority/grace: **118 passed**, included in
  the broader suites. Legacy isolated packaging/startup remains **10/10**.
- Initial sandbox attempt denied local 127.0.0.1 socket binding, producing five
  environmental errors in the full suite. Logs were retained; full suites then
  passed with loopback execution allowed. No test was disabled or relabeled passed.
- No native source changed. No new local iOS/Android build is claimed. The previous
  137c884 remote CI success does not verify this newer source; publication/CI needs
  authorization for the new exact commit.

## Signed HTTP probe

1. Existing subscription: one completed operation and one reserved operation.
2. Import old association, then explicitly transition to signed synthetic product.
   Ledger and operation rows remain byte-identical throughout both transitions.
3. Lose completion response after durable commit, retry original plan: same receipt,
   **zero Apple queries**, no extra quota or model work.
4. Original reserved operation runs once on its original ticket. A third distinct
   operation under the new policy and repeated readback uses **one Apple query**.
   Final totals **3 distinct model calls / 3 successes / 0 pending**.
5. Reduced-catalog reader rollback: old receipt 200/identical; new admission 503,
   no Apple queries and ledger/results unchanged. No free fallback.
6. Refund under restored reader: new operation 429, used stays 3. Old links/audit,
   registration and research state remain unchanged; research_stored stays false.

The tests include real SQLite threads/CAS, official Apple verifier with synthetic
certificates and the actual GCS adapter over fake transport. They do NOT exercise
live GCS, Apple Sandbox/OCSP, billing, store products or a production fence.

## Suggested independent review

- Inspect incomplete guard/pointer/completion states and crashes at all four write
  boundaries; lost replies must reuse the original plan. Expired uncommitted proof
  requires a NEW reviewed plan, never mutation of retained evidence.
- Competing/stale plans cannot overwrite a newer pointer. Historical plan replay
  returns a historical acknowledgment, not a current paid grant or pointer rollback.
- Unknown policy remains bound for old receipt lookup but cannot grant new work.
- Shared ownership store, fixed subject/token/original and retained registration
  prevent policy migration from minting a new account or allowance.
- Audit policy inventory is bounded; the runtime never scans every account. The
  control callback must prove COMPLETE historical policies and a real external
  writer fence. Hash syntax and local callback True are not proof of either.
- The in-code v1 guard cannot stop a previously deployed process past its check;
  the actual writer fence is a deployment prerequisite, not solved by this patch.

## Remaining gates

Live inventory/fence/authentication provider and deployment/rollback rehearsal;
real Apple Sandbox, renewal/product transition and online verification; verified
human enrollment/account recovery, native purchase and shipping capture/history
integration; device/cloud acceptance; bounded state/audit retention. Medical
ba970b1-N1/N2/N4/L1/L2 remain unchanged. Commerce stays disabled. No merge,
deployment, data cleanup, store update, advertisement or purchase is authorized.

Protocol: `engineering/contracts/lite-subscription-policy-migration-v1.md`.
Reproduce with the pinned dependencies in `.github/workflows/lite-prelaunch.yml`.
