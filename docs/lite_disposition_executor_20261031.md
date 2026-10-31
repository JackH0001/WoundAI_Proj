# Finite signed disposition execution — local acceptance

Parent `72ee5c5a98d6be26c51d0d32370dd2257bab7892`, 2026-10-31.

## What changed

The six-lane account worker previously required a trusted in-process caller to
select an account and execution bounds. `DispositionExecutor` adds a separate
private HTTP factory with explicit enablement, independently pinned source,
image and manifest digests, operator Ed25519 signatures, and durable total
dispatch reservations. It is not attached to any existing app route table.

One manifest names at most ten exact closed-account plan hashes, lasts at most
one hour, and allows at most 100 commands / 1,000 reserved component dispatches.
Each command has fixed limits of 1–100 dispatches, 1–20 items per component and
1–60 seconds. The account plan includes frozen closure, result inventory and
research/history policy. No caller supplies a bucket, object, key version,
installation, grant cursor or deletion selector.

These are **component-dispatch bounds, not GCS request or byte budgets**. A
component performs several bounded calls. Production provider request/byte/cost
limits and end-to-end deadlines require separate implementation and acceptance.

## Signed request and exact replay

Only POST `/internal/lite/disposition/command` is supported, with strict JSON
`{command: <signed envelope>, drain: null | <signed drain envelope>}`. Request
size is limited to 32 KiB and each signed envelope to 16 KiB. Unknown fields,
duplicate JSON keys, wrong paths/methods and unsigned requests are rejected.
All responses are no-store and never include grant tokens or KMS resources.

Operator command payload fields:

| Field | Meaning |
|---|---|
| `kind` | `lite-disposition-command/1`, independent from migration approvals |
| `manifest` | Reviewed manifest digest |
| `command` | Unique 32-character hex command ID, persisted before work |
| `action` | `inspect`, `run`, or `resume` |
| `person` | Exact internal hash already listed in the manifest |
| `expected_state` | Digest of the executor state read before signing |
| `replaces` | Interrupted command ID for resume only; otherwise null |
| `issued_at`, `expires_at` | Validity interval, at most five minutes |

Construction is read-only. On the first mutation the executor records the plan,
namespace binding and command proof before claiming the durable state. A claim
reserves the full dispatch cap even when work pauses, performs fewer steps or
fails; no refund or implicit reset can exceed the reviewed total. The namespace
cannot be rebound to extend its budget. A new reviewed plan needs an explicit
new namespace and authorization; it still resumes the original account worker
and closure evidence rather than creating a different account identity.

At most one command is running/uncertain in an executor namespace. Only a
definitively won CAS dispatches work. An identical concurrent claim loser or a
lost claim acknowledgment does not guess ownership. A completed response can be
read back with the same valid signed command, without a new worker call or
budget reservation. After expiry, a fresh signed inspect still reads saved
status; it does not reopen execution. This is an operator interface, not the
end-user session or account-deletion status API.

## Interrupted execution and containment

An incomplete or uncertain command holds its position and returns HTTP 503 with
`retry_automatically=false`. A new ordinary run is refused. Recovery requires:

1. Stop/drain the old execution using the separately configured supervisor.
   A timeout alone is not proof that its process or provider RPC stopped.
2. An independent signer records `lite-disposition-drain/1`, binding the same
   manifest, interrupted command and observed executor-state digest, with
   `drained=true` and validity at most 60 seconds.
3. The operator signs a **new** resume command naming the same account, state
   and interrupted command. The executor saves both proofs, marks the old
   command superseded and reserves a new bounded execution budget slot.
4. Resume continues the original account checkpoints. It does not repeat
   inference, reset quota, reopen research admission, undo an accepted closure,
   restore deleted generations, or create replacement encryption keys.

The two verification-key sets must be independent. A signed drain is an
attestation from an external trusted supervisor/operator, not a process detector
inside this module. Real drain collection, IAM, key custody and supervisor
integration remain deployment gates. No observer heartbeat is used for ordinary
work or public login; drain evidence is checked only for explicit recovery.

Worker pause/cancellation remain supported by the underlying worker. This
private command route deliberately does not expose arbitrary control changes or
rescan. **Deadlines remain cooperative**: authorization/ownership/expiry are
checked before dispatch and at each worker boundary. An in-flight RPC is not
killed. Recovery must establish actual drain; restart alone is not permission.

Missing original command, drain, checkpoint, binding or receipt evidence stops
processing. A binding without a state is ambiguous (interrupted first claim or
lost budget), so it does not reconstruct a new budget. That condition needs a
separate reviewed repair using original evidence. Stored historical signatures
are verified without requiring their original validity window to remain open.

## Isolation, package and current release status

The executor jobs/audit stores cannot alias any of the underlying account,
worker, service, research or history stores/prefixes. HTTP status exposes only
component states and aggregate progress; private raw observations/KMS resource
references are not forwarded. `account_deleted` and backup-erasure claims remain
false, including after six component observations finish.

This module is intentionally **not in the native, key-preparation or legacy
image allowlists**. There is no standalone production configuration factory,
cloud credential discovery, scheduler, IAM grant, image build or deployed
service in this change. A future dedicated composition/package must supply the
real six-lane graph, least-privilege transports and pinned configuration, then
pass Linux image, negative-IAM and provider acceptance before activation.
Do not deploy this factory behind a public WSGI endpoint. Signature checks are
additional to private service-level invoker IAM, not a substitute for it.

Remaining deletion gates include orphan/unlinked data, soft deletion/backups,
identity/subscription/audit retention, public/native detailed status and actual
GCS/KMS/Apple/device verification. No real data or keys were deleted here.

## Reproduction and review

Use repo workflow dependencies and PYTHONPATH containing Backend/Flask,
engineering/phase2 and tools:

```text
python -m unittest -v test_lite_disposition_executor test_lite_disposition_worker
python tools/lite_disposition_executor_probe.py --output executor-probe.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

Tests use generated captures, real local CAS and signed HTTP requests with fake
Apple/KMS/versioned-object transports. They check valid/rejected requests,
concurrency, lost claim/completion/receipt responses, expired authorization,
same-receipt replay, finite restart-stable budgets, signed drained recovery,
missing original evidence, redaction and preservation of quota/source capture.
The probe drives the actual six components; it is not true Apple Sandbox,
cloud-IAM, real provider latency or physical-device acceptance.

Claude review focus: lost-claim ownership versus completed-response replay;
original evidence validation; resume account/state/drain binding; durable budget
exhaustion; no hidden reset/refund; clear boundary between cooperative deadlines
and real process/RPC drain; unchanged public package/route isolation.
