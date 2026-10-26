# PR36 follow-up: cutover, reservation leases and migration paths

Baseline: `4fb1cf3e16966b48da8588d8ef763c3d454164b9`. This is a local, default-off
implementation checkpoint following Claude's PR36 review. It is not a deployment
or a statement that G1–G3 are closed in the released App.

## Before and after

| Item | Baseline / reproduction | Current local result |
| --- | --- | --- |
| Migration Windows keys | Real files with Windows relative-path spelling fail with `migration source disappeared` | POSIX object keys produce exactly the same plan; additive apply preserves every original file hash |
| G1 next UTC day | Missing daily row returns HTTP 503 | With an explicitly sealed cutover and verified account, post-cutover day creates once by CAS and prepare returns 200; unknown baseline/control remains blocked |
| G2 two prepares, no execution | Third prepare returns 429 even after 15 minutes; zero model calls | Opt-in v2 unclaimed leases cancel after 900 seconds; third prepare returns 200, original two attempts/receipts remain and model calls remain zero |
| G3 paused old reservation | Original operation can remain stuck across midnight | Old-day exclusion primitive tested against paused writes; **successor mapping, HTTP recovery and native journal are not implemented**, so the original HTTP retry still returns 409; no silent new operation |

### Scope and invariants

`lite_usage_cutover.py` has no endpoint, credentials, deployment command or live
writer-fence adapter. An operator must verify the old writers are drained,
baseline/account mapping frozen and then seal a create-only control receipt.
Runtime pins that receipt digest. The sealed day itself still needs its original
verified baseline; existing v1/v2 rows are never replaced. Production cutover is
still blocked until the live control adapter and rollout/rollback acceptance exist.

Only explicitly opted-in v2 days use 15-minute unclaimed leases. Expiry and claim
compete on the same daily-row CAS. Lost write replies never authorize execution.
Running/unknown jobs are not timed out or refunded; result/settlement replay never
reruns the model. Attempts remain counted, including cancellations, up to 30.
The same expired operation stays cancelled; releasing its slot does not silently
re-run the photograph. Old v1 reservations do not acquire a new expiry policy.

`fence_missing` stores input-bound exclusion on the **original** day's row before
any future successor can be considered. If an old reservation already exists,
it returns no successor authority. If the fence wins, the paused old CAS loses.
Missing/v1 old rows and exhausted bounded history fail closed. The function is
not an HTTP API and is not enough to close G3. The next implementation must pin a
single successor authorization and persist its generation in the native journal
before changing any retry identity; crashes/response loss must preserve both histories.

The default service path and its gap-characterization tests remain unchanged.
No subscription grace/policy-link migration, medical ba970b1-N1/N2/N4/L1/L2,
client UI, App build number, store package, real account or research data changed.

## Verification

- **89 new recovery executions:** 39 shared scenarios against SQLite and again
  against the actual GCS CAS adapter using fake SDK transport, one additional
  generation-preservation check, and 10 signed synthetic App Attest HTTP cases.
- **755 Lite tests passed** locally on Python 3.12.14, including existing
  packaging, subscription, result-retention and original disabled-path probes.
- **723 tests passed** in 11 locally replayed prelaunch shell steps. This overlaps
  the 755; it is not an additional unique total and is not remote CI.
- **182 targeted tests passed:** the 89 recovery executions plus 93 medical
  consent-order/rollout/operations tests, including the Windows migration fix.
- Initial sandbox runs could not bind local loopback sockets. Those logs are
  retained; final runs used 127.0.0.1 with socket permission and passed. An optional
  system-Python 3.9 run lacked Google test dependencies and is not counted as a pass.
- The Windows-path test substitutes platform path spelling while reading actual
  local files; it does not establish a native Windows execution. Python 3.11,
  remote CI and physical-device acceptance for this source remain pending.
- No new iOS/Android build was run because this stage changes no native source.
  Previous 4fb1cf3 CI success is historical, not attributed to these commits.

Replay from a checkout containing these commits, with the test dependencies in
`.github/workflows/lite-prelaunch.yml` installed (PowerShell):

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = 'tools;engineering/phase2;Backend/Flask;.'
python -m unittest -v test_lite_usage_recovery test_lite_usage_recovery_http test_medical_consent_operations
python tools/lite_usage_recovery_probe.py --output "$env:TEMP\lite-recovery-$([guid]::NewGuid()).json"
```

The focused replay is **131 tests** (89 recovery + 42 migration/cleanup operations).
The probe explicitly reports `release_ready: false` and separates opt-in results
from the unchanged legacy route. It does not contact Apple/GCS or run a real model.

## PR consolidation inventory

Read-only GitHub metadata and local ancestry agree: #29 and #30 both target main,
and #29's head is already an ancestor of #30. #31 targets #30, #32 targets #31,
#33 targets #32; all five heads are ancestors of the current commerce branch.
#29 changes 5 files; #31 is 2 documents, whereas #30/#32 are much broader.

Proposed consolidation: review #29, then refresh #30's comparison to updated main;
continue #31 → #32 → #33 → #34 → #35 → #36 with exact-head CI and review of the
remaining diff after each base change. Preserve ancestry or explicitly reconcile
children after squash/rebase; do not merge duplicate patches blindly. Earlier
heads predate later consent safety fixes, so merging them is not permission to
deploy medical research services. Main/publish workflow effects must be checked
at the actual merge point. This inventory is not a complete new code review of
all older PRs; no PR base, draft status, branch or merge was changed.

## Next review gates

1. Review the new schema validation and CAS boundary cases, especially late
   reservation/claim responses, clock changes and original-day exclusion.
2. Implement the G3 durable successor/native flow without bypassing identity,
   expiry, 30-attempt limits or retained-result 410 responses.
3. Complete trusted live cutover/baseline verification and fail-closed rollback;
   never reactivate an unfenced legacy allowance path on rollback.
4. Address subscription grace/policy migration and remaining medical findings.
5. Obtain scoped authorization before pushing these new commits/triggering CI,
   and separate deployment approval before any cloud activation. Prior permission
   covered only the single 4fb1cf3 test-fix push.
