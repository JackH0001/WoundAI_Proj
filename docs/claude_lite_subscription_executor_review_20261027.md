# Claude review: subscription migration executor and cutover rehearsal

Parent: `8d4df18a0246cda0959e3fd0f3adfec90821e7a6`. New commit is recorded in the
external packet manifest (avoids self-referential commit hashes). Draft PR #36
remains unchanged until separately authorized. No merge/deployment/store change.

## Before and after

| Parent behavior | This stage |
|---|---|
| Explicit per-account migration core, abstract control callbacks | Exact batch manifest, separate signed approval/observation, persisted progress and admission guard |
| Caller must coordinate completion and rollback | Sealed/active gate after all exact completions; failed progress attempts a durable pause |
| Single-plan lost-response tests | Batch response loss + pause/resume through signed synthetic HTTP; retained core authority reference |
| Runtime account grant has no batch admission guard | Guard installed by executor construction; new grants denied unless active and independently observed; existing result reads continue |
| Actual Apple Sandbox not performed | Concrete readiness checklist and user/engineer runbook, still NOT_RUN |

No source claims the core previously double-charged. The change fills a missing
batch-control layer; existing single-account correctness remains covered.

## Local evidence (2026-10-27)

- New executor tests: 35; signed HTTP probe assertions: 4; total new **39**.
- Full `test_lite*.py`: **1,008 passed**.
- Exact prelaunch workflow command replay: **976 executions**, 15 unittest
  commands; all returned 0. Suites overlap, do not add the totals.
- Dependency closure/isolated legacy startup: **10/10**, included in prelaunch.
- No iOS or Android source changed; no mobile build rerun claimed this round.
- New remote CI has not run before push. Parent CI evidence is not this commit's CI.

HTTP rehearsal: 1 settled + 1 reserved operation; committed migration response
lost; new work returns 503 during pause while old result returns identical 200.
Explicit resume repairs the same receipt with **0 Apple queries**. Reserved and
new operations each finish, repeats reuse their results. Final **3 model calls,
3 used, 0 pending**. Another pause/resume preserves ledger, results, registration,
old links/audit and research state. Research remains off in this service-only probe.

The first executor run recorded one test-fixture error (a trailing slash rejected
by the real GCS adapter), then corrected the fixture. An imported TestCase also
made that first run include existing tests; final new count is 39, not 64. Logs are
preserved in the packet. No test disabled and no production prefix validator relaxed.

## Priority review targets

1. Signed roles cannot be interchanged; no keys from the plan; fixed source/image
   and state digest; 900-second approval and 30-second observer maximums.
2. Stable per-plan authority reference is not a credential. Fresh command+state
   and observer authorization are required for writes; replay cannot create a
   second intent or query Apple solely because a completion response was lost.
3. Partial progress, process crash, lost control response, explicit pause racing
   completion, stale-start CAS loser and fake-GCS lost response preserve evidence.
4. Active inspection does not rollback on observation failure. Admission separately
   fails closed while evidence/active confirmation is unavailable. Failure to prove
   containment is reported as uncertain and requires external shutdown/reconciliation.
5. Exact original ledger, registration and research state remain unchanged; old
   results readable; no data deletion or list capability introduced.
6. The default unconstructed executor does not enable commercial routes. Packaging
   and legacy startup still pass without commercial modules.

## Explicit limits / remaining work

- Signed observation attests external facts; actual cloud observer, durable writer
  fence, complete inventory and IAM proof are not implemented/verified here.
  GCS adapter transport is mocked; SQLite and HTTP/signatures are real local tests.
- This is a callable production-oriented execution layer, not an installed CLI,
  cloud job or automatically active provider. Startup wiring remains disabled.
- Admission checks have bounded observation staleness and are not revocation of
  already admitted tickets. Stop/drain old processes independently before cutover.
- Expired **uncommitted** Apple intent remains paused; obtain a separately reviewed
  replacement plan/namespace and runtime switch, never rotate silently. That
  replacement orchestration is not automated by this exact-manifest executor.
- Keep stable reader and completed rows on rollback; never restore legacy writers.
- Total audit retention, real Apple Sandbox, native purchase/account bridge, live
  GCS and device cutover acceptance remain release gates. Medical ba970b1-N1/N2/N4
  and L1/L2 are outside this subscription-only patch and are not declared resolved.

## Reproduction and references

Read `engineering/contracts/lite-subscription-executor-v1.md` and
`docs/lite_apple_sandbox_acceptance_20261027.md`. Follow the workflow's pinned
dependencies and PYTHONPATH. A local loopback-capable test environment is needed
for the complete suites; no production credentials or Apple login are needed.

```text
python -m unittest -v test_lite_subscription_executor test_lite_subscription_executor_probe
python tools/lite_subscription_executor_probe.py --output fresh-executor-probe.json
python -m unittest discover -s engineering/phase2 -p test_lite*.py -v
```

The external packet includes the commit patch/bundle, changed source/context,
original failure log, complete local test logs, JSON probe and hash verifier.
Hash verification is evidence integrity, not an independent test rerun. Review and
run code only in an isolated checkout; do not overwrite existing Windows work.
