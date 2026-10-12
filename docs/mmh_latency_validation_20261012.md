# MMH latency investigation and bounded audit reads — 2026-10-12

## Observed service evidence

The user reported iOS 1.0(33) synthetic measurement taking nearly one minute, with further waiting for subsequent operations. Read-only Cloud Run request logs in the nearby window show the following on revision `woundai-backend-mmhps20261007-cb7f9dc2b28bc` (before the CAS cutover):

| UTC request start | Endpoint | HTTP | Duration |
|---|---|---|---|
| 07:15:58 | login | 200 | 9.986 s |
| 07:16:08 | care attest | 200 | 17.395 s |
| 07:16:26 | classify | 200 | 18.287 s |
| 07:17:17 | annotation | 200 | 71.310 s |
| 07:18:38 | depth | 200 | 19.432 s |

The first three durations total 45.668 s. These are service request durations, not instrumented phone end-to-end timings or exact per-image correlation. No image IDs, bodies or credentials were retrieved for this analysis. HTTP 200 alone does not verify the RGB-D contents or final research receipt. New CAS revision login later took 13.331 s.

## Source findings

- `MeasureFlowView.ensureLogin()` in the mobile checkout creates a new client/login before analysis and research submission. It must be optimized with credential/Google identity binding, expiry and live role/account-version validation, not by trusting a cached `isLoggedIn` flag. This report does not claim a mobile fix has shipped.
- Synthetic `seg=color` bypasses AI segmentation; it still goes through authenticated consent and persistence. This latency cannot simply be called AI inference time.
- Unlocked MMH audit rereads every historical slot at each append. Login emits an audit event; care attest emits intent and outcome; image/annotation/depth persistence emits further events. Reads were sequential. This is a code-confirmed scaling problem, but request logs do not yet quantify its exact share of each request.
- Service serialization and a preceding upload may add waiting. Increasing instance counts would need separate load/concurrency validation and could increase cost; no infrastructure was changed.

## Local implementation

Only MMH unlocked verification overlaps up to four generation-pinned object reads per batch. The verifier still validates every historical byte in sequence, checks hashes, links, names and generations, takes a fresh final manifest, and conditionally creates the next slot under the existing append lock. Read failure, altered history or manifest race still latches writes closed. Locked/WORM mode keeps sequential suffix reads. The change does not reduce object read count or the asymptotic full-history scan. It is an immediate bounded latency improvement, not a permanent solution to unbounded chain growth.

Added a count/result/duration-only `mmh_audit_verify` log for post-deployment attribution. No subject, image ID, content, password or token is logged by this instrumentation.

## Verification

- MMH unlocked audit: 24/24, including five added parallel-read tests (four-reader bound, ordered complete verification, network failure, broken link, later-batch corruption and manifest race across combined cases).
- Broader unittest audit discovery: 42/42 before the timing-only log addition; targeted 24/24 rerun after it.
- Standalone chain checks: 20/20; concurrency/admission: 102/102; versioning: 27/27; console verdict: 17/17.
- Offline benchmark: 120 synthetic slots, injected 5 ms read latency, three runs each. Median sequential 0.739 s versus four-reader 0.191 s (3.86x for this simulated read workload). **Not a phone or GCP speedup claim.**
- Manager fixes separately pass Python 121/121 and JS 9/9: scoped 60 s login/90 s account-write budgets, no automatic write retry, explicit service failure instead of invalid-password advice, clear waiting text and one-time-password section visibility.

## Deployment and next gates

Local changes only: not pushed, built or deployed. Current iOS and cloud latency are unchanged. Next: review source, CI, fixed-image build, private deployment preserving current IAP/CAS and traffic boundaries, then compare repeated synthetic measurements by stage and validate unchanged image/mask/depth/receipt binding. Target metrics must include warm/cold latency, median/tail, failures and request cost; do not promise a fixed number of seconds from the offline benchmark.

A safe client session-reuse change and durable upload queue are follow-up work, not implemented here. UI must distinguish local save, upload in progress, server receipt verified, and retry needed. Do not show a fabricated percentage when the server provides no progress.

Evidence: `mmh-latency-20261012.json`, `mmh-audit-benchmark-20261012.json`, and benchmark script in the private latency evidence directory. This report contains no secrets or patient data.
