# Private disposition provider budget

This is a local implementation and synthetic validation milestone. No deployment,
cloud resource, real deletion, key destruction, store update or new CI run is
authorized by this document. The public/native service graph is unchanged.

## Before and after

The existing signed executor reserves a finite number of component dispatches.
A dispatch may perform many storage operations, so that limit is **not** an HTTP
request or byte limit. The new private factory requires an additional hash-bound,
durable provider allocation policy and connects operational GCS, KMS and Apple
revocation transports to the same per-process allocation.

Before any operational provider call, the whole allocation is durably reserved
using the original generation/CAS store, with create-only policy/checkpoint audit.
Only an acknowledged successful CAS grants the live caller authority. A lost
write/readback response consumes that allocation without granting permission.
Restarting requires another allocation; no refund, refill, old-lease recovery or
automatic renewal exists. Changing the policy in the same bound namespace fails.

An allocation atomically reserves a request, outgoing payload size and maximum
response payload before sending. Errors, 401, 429, partial reads and small replies
do not refund capacity. The SDK's explicit retry invokes the wrapper again and
must reserve again. Google authentication replay is disabled. Redirects,
compression, file/generator request bodies and unsupported provider hosts fail
closed. Responses are bounded before exposing the original bytes to the SDK;
GCS generation conditions and CRC validation remain in place.

## Configuration

`provider_budget` is mandatory in the independently hashed startup configuration:

- `v=1`, explicit `namespace`, manifest SHA-256, source commit and image SHA-256;
- `not_before` and `expires_at`, at most one hour, covering the signed manifest;
- 1–20 `max_allocations`, each with 1–10,000 `requests` and 1–300 `seconds`;
- positive `send_bytes` (at most 32 MiB each) and `receive_bytes` (512 MiB each);
- aggregate ceilings of 100,000 requests, 256 MiB sent and 4 GiB received.

These are validation ceilings, **not approved production budgets or defaults**.
The reviewed execution plan must choose smaller amounts appropriate to the exact
operation. A single body/response cannot exceed 1 MiB. The private GCS transport
reserves 64 KiB per reply, KMS 32 KiB, Apple 16 KiB. Unused response reservation is
not returned; a large listing may conservatively stop rather than exceed its cap.
The factory now requires 32 separately named stores, including independent budget
state and create-only budget audit stores. Existing evidence is never reset.

`DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true` is required by the real
provider factory and set in the dedicated image. The pinned storage SDK 3.13.1
otherwise queues background bucket metadata reads from media tracing helpers,
even when no application read was requested. Actual SDK tests initially exposed
this race; disabling that documented SDK switch removes it from this private
process. Tests still count every outgoing HTTP request. No SDK monkey patch is
used in production.

## Failure and recovery

Budget exhaustion stops further operational requests. If even the error receipt
cannot be saved, the original running claim stays in place. A fresh reviewed
allocation may inspect it; blindly replaying the same incomplete command is
refused. Advancing requires the existing separately signed drain/recovery proof.
This does not assert that an interrupted provider operation was cancelled.

Wall and monotonic clocks are checked before requests, at response-read boundaries
and before successful completion. Clock regression closes the allocation.
Connect/read timeouts are capped to remaining time, but an in-flight OS/network
call is **not a proven hard deadline**. Operator/supervisor drain and cancellation
remain deployment gates. The allocation is process-wide: warming a worker consumes
one reservation even if later startup checks fail. Idle expiry requires a new
reviewed process allocation, not implicit refill inside a request.

## Evidence and its limits

`test_lite_provider_budget` exercises CAS concurrency, lost acknowledgement,
restart, immutable audit, payload/thread bounds and clocks. Actual `requests`,
`AuthorizedSession` and `google-cloud-storage` run over a synthetic HTTP adapter
in `test_lite_provider_transport`; no cloud identity or network is used. The probe
reserves two allocations, spends four HTTP attempts including a lost response,
then refuses additional requests and a third allocation. It verifies the real
GCS generation-conditioned download path.

The original factory/worker fixture separately verifies preserved captures,
identity/quota audit, one model execution and completed-command replay without
additional work. That fixture uses synthetic storage and does **not** prove a
complete six-lane run fits a real provider budget. Its injected exhaustion test
checks the original claim survives when error reporting also cannot write.

The scope is payload/request accounting, not a cloud billing quote. Bootstrap
metadata, allocation journal reads/CAS, credential acquisition, TLS headers,
provider internal work, retries outside these adapters, and persistent storage
are not counted by this data-plane meter. A [separate per-start meter](lite_disposition_bootstrap_20261031.md)
now covers the metadata endpoint and startup bucket/journal requests. Bounds
across process restarts, IAM enforcement, hard supervision, cost measurement and live failure/recovery still require a
separate fixed-source execution review. Do not deploy on the strength of these
local tests or describe the whole account as erased.

## Reproduction

Use the pinned dependencies and paths in `.github/workflows/lite-prelaunch.yml`:

```text
python -m unittest -v test_lite_provider_budget test_lite_provider_transport
python -m unittest -v test_lite_disposition_startup test_lite_disposition_package
python tools/lite_provider_budget_probe.py --output provider-budget-probe.json
python tools/lite_disposition_startup_probe.py --output startup-probe.json
```

Review especially lost-CAS authority, persistence of spent allocations across
processes, response buffer limits, authentication/SDK hidden retries, explicit
control-plane exclusions and budget exhaustion while preserving execution claims.
