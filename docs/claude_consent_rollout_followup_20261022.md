# Claude follow-up: P1–P4 deployment review of 5c8e414

Baseline: `5c8e4143cf3315f75c5dedac2d206c31b1f12d46`, draft PR #35.
Candidate code only; no cloud deployment, real-data deletion, TestFlight or
public release in this round. No automatic schedule or bucket policy changed.
The earlier report remains historical evidence, not this revision's results.

## Findings and changes

**P1 — logical withdrawal bypassed quarantine cleanup: confirmed.** Added a
policy-driven, dry-run-default maintenance worker. An immutable conditional
purge claim arbitrates against restore; it preserves the old withdrawal/audit
and prevents old media being re-admitted. Media inventories cover RGB, staging,
quarantine, masks and raw/PNG depth, including versioned GCS objects. Deletions
are generation-pinned, failures resume, and later reconciliations catch orphan
writes. Verified care staging also records both directions of media ownership before its final admission guard and first image write. This covers unannotated staging and a writer crossing a withdrawal snapshot; the cleanup claim includes registered media, not only the annotation queue. Six additional tests cover registration failure, invalid care and those races. Soft-deleted recoverable versions stay pending. Retained annotation,
consent/audit metadata, backups, exports and models are explicitly outside the
media-absence receipt. Missing legacy timestamps or corrupt histories require
review, not invented ages. Both mobile messages, manuals and the IRB draft now
distinguish exclusion from physical cleanup.

This is NOT a deployed automatic-destruction service. The IRB retention policy,
real storage/soft-delete behavior, worker schedule/monitoring and cloud races
must be approved/tested before patient research. Existing study records were
not processed by the worker. The tool cannot reset a legal retention hold.

**P2 — all-subject scans on individual requests: confirmed.** Protected reverse
image references are published before consent events. Individual guards read
only related subjects. Existing pre-index candidates require a writes-paused,
verified backfill before the completeness marker is published. No runtime
"assume index complete" fallback or cross-request stale consent cache exists.
Bulk stats/exports intentionally enumerate subjects with one legacy snapshot.

Independent synthetic before/after replay for an eligible image (1 / 200
subjects, same history shape):

| Storage interface calls | 5c8e414, 1 | 5c8e414, 200 | candidate, 1 | candidate, 200 |
|---|---:|---:|---:|---:|
| get_json | 8 | 1202 | 6 | 6 |
| list_keys | 4 | 402 | 3 | 3 |
| read_lines_fresh | 2 | 400 | 1 | 1 |

For a blocked image the candidate is 5 / 3 / 1 in both populations. These are
LocalStore interface counts, not measured GCS requests/latency. GCS get_json
currently performs existence plus download; do not report the table as actual
network bills or throughput. Legacy queue length and per-subject history still
matter. This fixes the cross-subject amplification, not all service capacity.

**P3 — one corrupt history stopped other subjects: confirmed.** Known corrupt
roots/events/sequence gaps exclude that subject and referenced images. Missing
roots remain excluded through image-index evidence, including bulk exports.
Stats expose consent_fault_count. Unrelated subjects continue. Cleanup reports
faulted subjects and continues others. Locked evidence is never deleted or
replaced to unblock consent. A damaged subject remains blocked pending a
separately reviewed additive recovery/migration; automatic WORM repair is not
claimed. Storage-wide errors or unattributable corruption of the shared legacy
log still fail closed rather than guessing which patient is affected.

**P4 — invalid legacy queue image ID prevented withdrawal: confirmed.** The
code-level withdrawal is recorded; valid image siblings remain covered.
Malformed string/object/number/non-finite IDs are recorded only as counts and
hashes, and never become object paths. The original queue is unchanged.
Explicit malformed IDs supplied in the new API request are still rejected.
Unresolvable historical media references need operator review for cleanup.

## Verification on this candidate

- Medical server/storage/guards: **131 passed**, 83 subtests, no failures.
  This includes **28 new rollout tests**, separately from the previous 22
  consent-order tests.
- Lite backend: **452 passed**, 463 subtests, no failures.
- Build/contract/RGB-D/retention tools: **62 passed, 4 skipped** (PowerShell
  unavailable locally), 37 subtests. Do not count subtests as independent tests.
- Synthetic localhost HTTP consent lifecycle: **0 failures**.
- Ten isolated regression scripts passed: record scope, resubmission, store,
  RBAC, depth, tissue dataset/export, datachain, manuals and backend URL parity.
  Manual consistency: **107 checks passed**.
- Mobile changes in this round are withdrawal explanation strings only; the
  prior 262/234/166 XCTest and 141/143/63 Android counts are NOT rerun evidence
  for this commit. Check the final-head CI for compilation/tests separately.

Early failure logs are retained: a test fixture used the wrong synthetic org;
a maintenance wrapper briefly shadowed its import; local socket restrictions
required isolated localhost tests with approved execution permissions; a tool
suite command initially named a nonexistent file. Final commands use corrected
paths/fixtures, and no true cloud resources were used by tests.

## Reproduction / review focus

```sh
python -B -m unittest discover -s engineering/phase2 -p test_medical_consent_rollout.py
python -B -m unittest discover -s engineering/phase2 -p test_medical_consent_order.py
python -m pytest engineering/phase2/test_lite*.py -q
python tools/windows/run_backend_http_test.py --out /tmp/medical-http --timeout 180
python tools/run_medical_consent_maintenance.py index-backfill --root <isolated-copy>
python tools/run_medical_consent_maintenance.py media-cleanup --root <isolated-copy> --policy <reviewed-policy.json>
```

The last two commands default to dry run; do not add --apply against real media
without the separate retention/deployment approval. In cloud deployments the
packaged entry point is `python -m medical_consent_maintenance`.

Review medical_consent_order.py, medical_media_cleanup.py,
medical_consent_maintenance.py and scoped api_flywheel guards. The new
[operations contract](../engineering/contracts/medical-consent-operations-v1.md)
is a required companion to the original protocol. Backfill, mixed writers,
pre-purge rollback, physical lifecycle and real GCS acceptance are rollout gates.

Local evidence:
`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-area-upload-acceptance-20261021/consent-rollout-fixes-20261022/`.
The handoff contains changed code/docs/tests with hashes, not raw research media.
The two additional low-severity findings mentioned in the supplied review were
not described; no completion claim is made for them. M3/M4/R3/R4 and prior open
deployment/device acceptance items also remain open. Only the latest stable
build remains the planned allowlist; live version policy was not changed.
