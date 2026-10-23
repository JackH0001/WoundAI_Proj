# Claude handoff: consent migration and cleanup operations, 2026-10-23

Baseline: `ba970b1ca86f0c059bcdca7e460b6f16b9e683be`, PR #35
(`codex/lite-reconsent-20261021`, stacked draft on #34).
The exact reviewed candidate is the commit containing this document. The external
handoff manifest pins that commit and every included file; no patient evidence
or credentials belong in the package.

## Changes since the previous P1–P4 implementation

1. **Manifest-bound additive migration.** `medical_consent_migration.py` inventories
   sealed histories, care/promotion/binding evidence, local raw hashes or GCS
   generations (including noncurrent and soft-deleted media), and legacy history.
   Apply requires the previously reviewed digest and paused/drained writers,
   re-verifies sources, adds both reverse/forward ownership and consent indexes,
   reads them back, and publishes the completion marker last. No source object is
   rewritten or removed. Partial apply resumes with the same plan. Unknown media,
   contradictory derived rows, corrupt/uncommitted roots and unsealed legacy
   subjects block completion rather than receiving guessed consent/ownership.
2. **Audited worker execution.** `medical_consent_maintenance.py` records a run ID
   bound to policy and subject bound. Same finished run replay is read-only;
   interrupted runs resume; new runs reconcile prior purge claims. Faults and
   recoverable media remain `needs_attention`; separate subjects proceed. The
   subject cap refuses the entire mutation batch, not a silently incomplete tail.
3. **Actual additional bug fixed: older soft-deleted versions.** The prior worker
   checked soft versions only when the current bucket policy was nonzero. Turning
   the policy off does not shorten already deleted objects' retention. It now
   queries soft versions regardless of current policy. Failed/unsupported queries
   stay pending. This does not change the bucket policy or delete original data.
4. **Scheduling/GCS plan and packaging.** A deterministic review-only plan pins
   source hashes; it cannot execute cloud commands. Candidate schedule stays
   paused/dry-run with separate worker/invoker identities. New module added to the
   explicit build allowlist; new tests are in `p0-4-audit` CI and path triggers.

## Verification actually run on this candidate

| Selection | Result | Meaning |
|---|---:|---|
| New operations + plan tests | 44 passed, 6 subtests | Included in the medical total below; do not add twice |
| Consent order/rollout/operations/plan + staging + research guards | 135 passed, 78 subtests | Local synthetic data and offline GCS SDK boundary |
| All `engineering/phase2/test_lite_*.py` | 452 passed, 463 subtests | Includes local loopback HTTP and explicit build-context packaging |
| CI unittest entrypoints for new suites | 39 + 5 passed | Same 44 tests, verifying CI invocation compatibility |
| `git diff --check` | passed | Whitespace only |

The first sandboxed Lite run had four loopback listener failures (`Operation not
permitted`), with 448 tests passing. Rerunning with local listener permission gave
452 passing; the initial failure log is retained, not called a product regression.
Subtests are not added to test counts. Exact-head GitHub CI status is supplied
separately after push; prior-head CI success is not reused as this head's result.
No mobile code, build number or live allowlist was changed in this revision.

Reproduce using the repository's backend test environment:

```sh
python -m pytest -q engineering/phase2/test_medical_consent_order.py \
  engineering/phase2/test_medical_consent_rollout.py \
  engineering/phase2/test_medical_consent_operations.py \
  engineering/phase2/test_medical_operations_plan.py \
  engineering/phase2/test_p0_4_staging.py engineering/phase2/test_research_format_guards.py
python -m pytest -q engineering/phase2/test_lite_*.py
python -m unittest discover -s engineering/phase2 -p test_medical_consent_operations.py
python -m unittest discover -s engineering/phase2 -p test_medical_operations_plan.py
python tools/plan_medical_consent_operations.py --output /tmp/new-review-plan.json
```

`medical_fake_gcs.py` uses real production GcsStore methods with a fake SDK
client and fake locked retention metadata. It never constructs ADC or contacts
GCS. Test create-only contention, both restore/purge orders, simultaneous workers,
all-generation pinned delete, late replacement, recoverable soft versions after
policy-off, held objects, audit failure, interrupted run and unrelated fault.
These are caller/logic tests, **not actual cloud acceptance** or latency/load data.

## Requested independent review

- Can migration publish completion with incomplete/contradictory ownership or
  changed original data? Check marker-last, stale manifest, interrupted outputs,
  source/generation snapshots and readback. Do not infer all real legacy data
  are migratable: this tool explicitly blocks unsealed/unresolvable histories.
- Can overlap, response loss or reconsent delete unclaimed new media, release old
  purged images, or append duplicate consent sequence slots? Check the fake SDK
  interleavings and run-ID behavior. Real two-client races remain required.
- Can a current policy of zero hide older soft versions? Check the regression
  and fail-closed result when soft listing is denied/unsupported.
- Does the execution/retention scope match participant wording? Audit/annotations,
  backups/exports/models are not claimed erased. A missing old timestamp stays
  blocked. Production retention duration is deliberately unspecified.
- Does the worker identity work without bypassing existing scope? It currently
  requires a separately reviewed Job profile/appropriate production audit epoch;
  **do not spoof K_SERVICE or weaken MMH/Demo guards** to run a cleanup Job.

## What remains deliberately unexecuted / not closed

See [full operations acceptance contract](../engineering/contracts/medical-consent-operations-acceptance-v1.md).
No live index migration, cloud build/deploy, bucket/IAM creation or mutation,
Scheduler activation, real-media deletion, app distribution or public release.
The plan proposes isolated new synthetic GCS buckets, bounded one-time workload,
retained audit and separately authorized test-media deletion. A scope-guarded
live smoke adapter and Job-specific audit profile still need implementation/review
before execution. No raw patient data is needed for that stage.

Production migration requires paused/drained writers, zero unresolved mappings,
reviewed retention policy and cloud evidence. Do not reset damaged locked records
or roll back to pre-purge readers. Clinical mobile acceptance and prior open
M3/M4, R3/R4 and in-flight annotation revision ordering are not solved by this
operations change. This stage does not move Lite to external/public release.

Official references: [GCS preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions),
[GCS soft deletion](https://docs.cloud.google.com/storage/docs/soft-delete),
[Cloud Run Jobs scheduling and OAuth](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).
