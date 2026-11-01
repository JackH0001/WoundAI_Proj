# Bounded account-disposition progress — 2026-10-29

## Problem and resulting behavior

At parent `d6f5bdbdd6695a18d42956b0e67120a52cb61736`, result-slot sealing,
Apple grant revocation and per-account key destruction have separate executors.
Even after an operator drives those components in a synthetic rehearsal, the
public deletion receipt remains v1 `data_disposition=not_started`. A client
cannot distinguish a scheduled KMS destruction from verified destruction or
show the already-completed result work.

`DispositionProgress` explicitly composes those existing components. A bounded
internal worker advances the same frozen closure inventory and persists an
audit-bound progress checkpoint. The existing signed privacy status route only
reads progress. Native v2 receipts retain the original deletion operation and
show separate states. Old v1 receipts remain readable.

This does not finish account deletion. Every v2 receipt still has
`account_deleted=false`, `data_disposition=pending`,
`subscription_cancelled=false` and `local_records_preserved=true`.

## Contract and ordering

- Constructor requires the actual `AccountDeletion`, `ServiceResultDisposition`
  and `AccountGrantCipher`, one lifecycle, and separate progress/audit stores.
- The application must explicitly call `deletion.attach_disposition(progress)`.
  Default startup remains unchanged. No environment switch, scheduler, public
  worker endpoint or production provider is enabled by this change.
- The plan binds scope, original deletion operation, requester, frozen service
  and grant inventory and a versioned disposition catalog. The worker writes
  immutable intent before dispatch. Missing prior intent or checkpoint evidence
  stops it; it does not recreate missing audit evidence to justify progress.
- One invocation advances at most `limit` service slots and `limit` grants,
  with `1 <= limit <= 100`, plus at most the account-key action allowed by the
  existing key executor. A caller's arbitrary person or operation is not a new
  public authorization path.
- Current result slots may be sealed while Apple is unavailable. Apple
  revocations may progress while a result store is unavailable. The key lane
  cannot run before all inventoried Apple revocations are confirmed.
- A KMS `DESTROY_SCHEDULED` receipt is pending. Only the existing provider's
  validated `DESTROYED` observation can produce `grant_key_state=unrecoverable`.
  This is cryptographic unavailability of that account's grant credentials,
  not confirmation that cloud backups or research data have been erased.
- Original component receipts are validated before they enter the checkpoint.
  The immutable observation is written before CAS. Lost CAS acknowledgments
  are read back. Lower counts or a late scheduled-key observation cannot replace
  already-confirmed progress. Unchanged replay creates no extra observation.
- Two workers rely on the component leases and CAS checks; bounded contention
  may require resuming the same operation. Neither worker starts inference,
  credits quotas, changes research consent or opens a new account.

The additional catalog is
`490147fc0741120d0f36252d2d6c03f15da67e1b89bd3bc217c6d1b6848fc891`.
It always retains these unimplemented/unverified lanes:

1. `historical_versions_backups`
2. `research_association_disposition`
3. `identity_subscription_retention`
4. `live_provider_device_acceptance`

## iOS handling

`LiteDeletionReceipt` accepts the existing v1 form or the strict v2 form. v2
requires the recognized catalog, the complete pending-lane list, consistent
counts, known states and monotonic progress. It rejects false account completion,
subscription cancellation, loss of local-record preservation, changed inventory,
backward checkpoints and v2-to-v1 downgrades.

The store saves the receipt to the existing journal and queries only the original
operation. Relaunch does not ask Apple to log in or initiate another deletion.
The page separately shows Apple revocation, current result handling, credential
key handling and the remaining incomplete account deletion. Subscription
cancellation remains an explicit separate Apple flow.

## Synthetic verification and repeatable comparison

Use the same `tools/lite_disposition_progress_probe.py` against the parent and
candidate modules. It uses real SQLite CAS, AES envelope encryption, the actual
service coordinator and lifecycle, with generated images and local Apple/KMS
transport doubles. It does not call live Apple, Cloud KMS or GCS.

The parent run explicitly advances the existing component executors to prove the
reporting gap. The candidate run advances the composed worker. Both retain the
original model invocation count (one), quota/identity rows and original generated
capture. Candidate progress moves from not-started to current-results-sealed and
key-destruction-scheduled, then to key-unrecoverable after synthetic destruction;
all remaining lanes stay pending. Status queries invoke no provider mutation.

New backend tests include signed App Attest HTTP status, default-off behavior,
125-slot batching, two workers, provider outages, restart, lost acknowledgment,
missing original audit, invalid component claims, cross-account isolation and
non-regressing observations. New XCTest cases cover decoding, version migration,
misleading completion prevention, journal restart and late status responses.

Local validation on this source:

| Suite | Result |
| --- | --- |
| New composition/backend regressions | 34 passed |
| Complete Lite backend | 1,562 passed |
| Local replay of prelaunch workflow | 1,530 test executions passed |
| Lite iOS Release simulator | 415 passed, zero failures, one existing file-protection skip |
| New native disposition regressions | 14 passed, included in the Lite total |

Exact source hashes and the original XCTest summary/tree are in the accompanying
verification packet. Test runs overlap and must not be summed. The documented
simulator file-protection skip is not physical-device acceptance. Remote CI for
this change has not run; it requires the next authorized push.

## Deployment and failure containment still required

There is no deployment in this change. Before opting a live candidate in:

- complete the remaining data-disposition executors and their retention policy;
- validate the service/grant inventory migration and drain incompatible writers;
- select a native build that supports v2 and preserve existing journal compatibility;
- provision separate constrained progress/audit stores and per-account KMS keys
  under a separately reviewed authorization, with live provider and backup tests;
- prepare a durable worker queue, bounded retries, alerts and a reviewed operator
  runbook; the current internal API is not a production scheduler;
- if a worker fails, leave the account closed and resume the same inventory.
  Never reopen it or restore deleted payloads as rollback. Once v2 has been
  observed, do not downgrade its status route to v1; pause advancement while
  preserving v2 reads instead;
- do not remove the unresolved lanes, claim complete erasure, delete audits,
  cancel a subscription or restart research upload from these component results.

Claude review priorities: recompute the before/after probe; attempt receipt
injection and late CAS; verify native monotonicity and default-off imports;
confirm that no existing raw capture, research index or quota is modified.
