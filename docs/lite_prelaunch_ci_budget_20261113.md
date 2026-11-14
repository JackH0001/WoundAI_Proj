# Prelaunch CI time budget — 2026-11-13

Exact-source run `43367236128` for `089fbb1` was cancelled at the existing
15-minute job limit. The GitHub check annotation explicitly says
`The job has exceeded the maximum execution time of 15m0s`. The offline job
started at 04:42:24 UTC and completed cancellation/artifact upload at 04:57:37.
It reached the final service-disposition step; disposition-progress and the
readiness summary did not run. This run is not a passing prelaunch result.

The four separate image-build jobs succeeded. Completed offline steps had no
failed conclusion. The longest recorded successful step was research-media
paging (147 seconds), followed by multi-batch/native purchase (64 seconds).
The accumulated suite and hosted-runner variation exhausted the job-wide limit;
the timeout is not evidence of a product failure or of all tests passing.

Only `offline-acceptance.timeout-minutes` changes from 15 to 25. YAML structure
comparison confirms every command, test selection, environment, artifact step,
permission and other job limit is identical. This retains a bounded timeout
without dropping coverage. The limit is a maximum, not an imposed extra runtime;
the actual new run duration and pass/fail must be read back after authorization.

The two tail suites were run locally: 80 tests pass. The service-disposition and
disposition-progress probes both pass with zero real provider calls. These
focused checks do not substitute for a complete CI rerun or real data deletion.
The pending-lanes receipt still does not claim complete account deletion.

Evidence is retained in the prior publication directory
`woundailite-request-admission-20261113/publish-ci-089fbb1-20261113` and the new
`woundailite-wound-wire-20261113/ci-timeout` directory. New CI requires pushing this
source; no rerun, deployment or Apple update was executed to conceal the timeout.
