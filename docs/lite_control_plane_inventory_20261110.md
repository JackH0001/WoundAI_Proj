# Lite control-plane readback before bootstrap

The bootstrap operator can now prepare a signed step, but a signature is not
evidence that all writers have stopped. `tools/lite_bootstrap_control_plane.py`
collects bounded Cloud Run metadata from an independently pinned resource list.
It does not sign an observer envelope or automatically enable initialization.

## Why the distinction matters

Cloud Run can return a request timeout while its container continues processing.
Waiting for the configured HTTP timeout, removing a route, observing a zero
request metric, or finding a missing service is therefore insufficient to claim
that an old writer cannot commit. [Google request timeout documentation](https://docs.cloud.google.com/run/docs/configuring/request-timeout)

An execution's `runningCount` also excludes queued work. The tool requires a
completion timestamp, reconciled generation, and terminal task counts before
marking a **visible execution** terminal. Failed and cancelled executions can be
terminal without being successful. A terminal execution does not prove that a
new execution cannot start. [Google Execution reference](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions)

## Behavior

- Independently hash-pinned project, region, and named Lite services/jobs. No
  project-wide discovery or MMH/Demo queries. The caller must review coverage;
  this tool cannot discover omitted writers.
- Injected GET-only transport, limited requests, time, pages, items and response
  bytes. No credential discovery, IAM modification, POST, Secret, KMS, payload,
  object deletion or deployment path in this module.
- Service metadata is read twice. Each job's execution list is completely paged
  twice, including retained deleted executions, and compared with resource
  metadata before/after. Changes, duplicate rows or tokens, malformed counters,
  partial pages, budget exhaustion and errors remain unconfirmed.
- 404 is `absent`, not empty history; 401/403/429/5xx are `unavailable`. Provider
  error bodies are discarded. Environment values and credentials are neither
  requested nor included in the output.
- `inflight` and `route_closed` remain unknown. Even matching reads are not an
  atomic fence and cannot establish effective inherited IAM or complete history.
  `writer_fence_verified`, `effective_iam_verified`, `authorizes_bootstrap` remain
  false. The actual signature verifier rejects this unsigned metadata report.

The fields and paging follow the [Service reference](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services)
and [executions.list reference](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions/list).

## Tests and usage

`ControlPlaneInventory(get=...).inspect(coverage, expected_coverage=...)` accepts
an injected provider reader and returns a report. The caller bounds the HTTP
response before decoding it, pins allowed paths/field projections, and preserves
request status and hashes. It never passes this report as a bootstrap observation.

```sh
PYTHONPATH=Backend/Flask:tools:engineering/phase2:. python -m unittest -v test_lite_bootstrap_control_plane
```

The tests cover queued zero-running work, active tasks, failed/cancelled terminal
tasks, new executions on page two or between passes, service replacement, missing
or denied history, all bounds, scope mismatch, private field omission and refusal
by the real signed-evidence verifier. CI saves a separate control-plane test log.

## Remaining real execution prerequisites

1. Identify all writer identities and destinations from the approved native config
   and the full existing-source inventory; include inherited permissions and
   jobs, services or tools capable of writing those destinations.
2. In a separately authorized change, prevent new writes and prove existing work
   drained or unable to commit. Read back the effective protection; elapsed HTTP
   timeout and metadata observations alone do not meet this requirement.
3. Collect a frozen source inventory while that protection holds; keep unknown
   historical or soft-deleted scope visible for review.
4. Use separately held observer authority to attest that reviewed fence and the
   fresh state. Refresh the short-lived evidence without fabricating zero counts.
5. Run only the approved single bootstrap command, retain uncertain outcomes and
   use explicit recovery; verify the fence again before opening the candidate.

No real observer, writer-fence change, IAM update or bootstrap execution is
performed by this change. Existing owner runtime material and full provider
acceptance are still prerequisites, not satisfied by these local tests.
