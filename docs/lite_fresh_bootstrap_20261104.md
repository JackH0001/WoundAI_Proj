# WoundAILite: bounded first-install bootstrap

Local implementation after `5315430`; not a deployment or data migration approval.
No app code, container allowlist, endpoint, cloud IAM, secrets or current data changes.

## Before and after

Previously the subscription activation, quota rollout and durable service release
were separate operational APIs. Synthetic startup could exercise them, but there
was no durable overall record identifying which step had completed after a lost
response. `tools/lite_fresh_bootstrap.py` now coordinates those same APIs, one
signed step at a time. It does not copy fixture records into a real environment.

| Step | Existing core | Observable completion |
|---|---|---|
| subscription | `SubscriptionRuntime.activate_fresh` | Exact pinned origin and activation audit |
| quota | `UsageRollout.advance` | Confirmed active control and original sealed cutover |
| release | `ServiceReleaseController.apply` | Exact release generation and confirmation audit |

The public runtime still cannot admit new work between steps. A bootstrap result
is historical evidence, not a replacement for the runtime's live active/paused
check. Replaying the completed bootstrap after an operator pause does not resume
service.

## Inputs and separation

The constructor requires separately pinned plan and native configuration hashes,
the expected inventory sources and writer list, existing core objects, dedicated
CAS control and append-only audit stores, an independent observer store and
distinct operator/observer public verification keys. It neither discovers cloud
resources nor signs its own proof. The full plan is at most 8 KiB; each audit row
is checked against the production state adapter's 16 KiB limit before writing.

The first-install inventory must cover accounts, stable subscription links and
quota, with exact location hashes and frozen snapshot hashes. Every count must
be the integer zero. Unknown counts, missing coverage, duplicates and nonempty
sources are refused. This is not a general migration for existing accounts.
Missing buckets are not proof that an old system had no data.

SQLite paths and GCS bucket/prefix overlap are rejected for the operational
lanes. Cloud acceptance must additionally verify effective inherited IAM and
the complete physical lane mapping; Python object checks are not IAM evidence.

Each outer approval pins the previous control state, step, full plan and inner
command hash. The observer proves closed customer ingress, disabled legacy
writers and zero legacy/bootstrap in-flight operations. Observer records remain
valid for at most 30 seconds, only during these operational transitions. The
normal runtime keeps using the existing durable service-release control.

## Uncertain execution and recovery

1. Write/read back an immutable intent, then claim the step by generation CAS.
2. Only the winner calls the existing core. Read back the core's own receipts.
3. Preserve a result audit before completing the outer control record.
4. After a lost response, inspect only. A completed exact command returns its
   historical receipt without calling the core, even after the command expires.
5. A running/uncertain step requires a new signed `bootstrap-recover` command
   and an independently signed drain observation issued after the original
   attempt began. Merely retrying the old command cannot acquire a new attempt.
6. If quota rollout paused during failure, the new approval must additionally
   pin that exact paused state. The existing `resume_paused` API preserves its
   original manifest, baseline and audit. There is no clearing of used quota.
7. After eight attempts, or after the planned UTC day ends, stop for explicit
   reconciliation. Do not change namespace/day to bypass the old state.

A missing release confirmation can be completed using a new signed confirm of
the same generation. A conflicting origin, changed release or paused service
cannot be overwritten by bootstrap. An expired observer prevents a new step;
it does not cause a previously active service to be rolled back.

The drain proof assumes the separately controlled executor has really stopped.
The cloud wrapper must enforce a single bounded executor and close/drain its
predecessor before signing recovery. This module alone cannot stop a remote
process or prove an externally asserted fence.

## Reproduce offline

Use the existing backend + commerce test dependencies and `PYTHONPATH` entries
`Backend/Flask`, `engineering/phase2`, `tools` (use `;` on Windows). Run:

```text
python -m unittest -v test_lite_fresh_bootstrap
python tools/lite_fresh_bootstrap_probe.py --output fresh-bootstrap-probe.json
```

The probe refuses an existing output path. Its generated Apple signatures,
KMS responses, images and GCS transports are synthetic, with socket connections
blocked. It starts empty commercial stores, loses an activation reply, resumes
without activating again, boots the real shared-key native factory, and sends
the same signed recognition operation twice: one model call, one quota debit.
Original fixture records and audits are checked unchanged.

Coverage includes concurrent claims, GCS generation CAS and lost acknowledgement,
release-confirmation loss, paused quota recovery, unauthorized inner actions,
control/audit aliasing, stale observations, midnight, bounded attempts, corruption
and retained data. The GCS transport is simulated; passing is not a live GCS or
Apple Sandbox acceptance result. The prelaunch CI workflow now includes both
the tests and probe; no new CI success is claimed until this source is pushed.

## Remaining deployment work

- A reviewed, least-privilege provider wrapper and GET-only inventory collector
  must bind these APIs to the exact live source/image/configuration and location
  list. No real-data seeding command is supplied by this change.
- Before authorization: pin resource inventory, costs, writer/drain mechanism,
  independent signers, execution time/write bounds and failure containment.
- If a deployment fails after release publication, keep ingress closed and use
  the existing signed release-pause procedure; preserve all records. Never remove
  release bindings or turn legacy writers back on as an automatic rollback.
- Owner-only Apple sign-in, subscription purchase/restore, shared-key retirement
  and complete account deletion still need their separate live acceptance.
- Existing TestFlight records, imported disabled Apple secret versions and raw
  research archives remain unchanged. There is no new store upload in this work.
