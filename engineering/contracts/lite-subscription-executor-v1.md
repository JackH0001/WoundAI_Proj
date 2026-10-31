# Subscription migration executor: explicit control, retained evidence

2026-10-27; parent `8d4df18`. This stage implements batch execution and rehearses it
locally. It does not enable a route, deploy a provider, create signing keys in a
cloud service, change IAM or perform a real Apple purchase.

## Boundary and state

`make_manifest` is read-only. It selects 1–100 explicit, existing service accounts,
deduplicated across installations, and retains each exact core plan. Missing
accounts, unknown policies or duplicate account entries stop preparation. A trusted
caller pins source commit, image digest, inventory and fence evidence hashes.
No list/discovery fallback or daily quota creation occurs.

`SubscriptionMigrationExecutor` binds the core authorization/fence callbacks and
the registry's new-admission guard. Construction is explicit; application startup
does not construct it. Existing public behavior remains unchanged. The operator
must route every candidate service instance through the same guard before cutover.
Merely constructing the executor does not establish a deployment-wide fence.

```text
absent -- signed start + independently observed drain --> running(index)
running -- exact account receipts --> sealed -- final checks --> active
running/sealed -- failure --> paused
active -- signed pause --> paused
paused -- new signed resume + observed old executor drained --> running(index)
active -- inspect / advance --> read-only observation (no automatic rollback)
```

Control state uses generation CAS. Every transition has immutable intent and
confirmation; each confirmed account retains its core completion. Progress is
persisted, not inferred from a timeout. The state has at most 32 phase transitions;
there is a reserved budget for containment on resume. Per-account progress does
not consume that phase budget. Total audit retention, including rejected or racing
commands, remains a separate policy gate; no cleanup is implemented here.

## Authentication and independently observed facts

- Operator approvals and observer records use separate pinned Ed25519 public keys
  (1–4 keys per role). A plan cannot supply a trusted key. Private keys appear only
  in ephemeral synthetic fixtures; no real signing credentials are generated.
- Approval binds exact manifest, action, current-state digest, nonce and validity
  window, no longer than 900 seconds. Every resume needs a new approval of the
  retained paused state. Stale starts/resumes are rejected.
- Observer evidence has a maximum 30-second lifetime. It binds namespace, manifest,
  source, image, inventory and fence, and states that all legacy writers are
  disabled/drained and the stable reader is installed. Start/resume additionally
  requires the previous migration executor to have no work in flight.
- The observer uses a GET-only adapter; it cannot itself change a fence. Signed
  facts are checked repeatedly during progress and admission. Bad signature,
  missing/expired evidence and read outage are `unavailable`; signed but
  contradictory facts are `contradictory`. Neither is an authorization to proceed.
- Production observer implementation, its identity/IAM isolation, inventory
  completeness, runtime identity of the pinned image, and durable shutdown of old
  writers still require independent cloud acceptance. A signature alone cannot
  establish that its asserted facts are true.

The core intent uses a stable manifest+plan authority reference. This reference is
not a credential: execution still needs the currently signed command, its durable
running state and fresh observer evidence. It survives response loss so resuming
does not conflict with the original immutable intent's authorization field.

## Failure and rollback

1. Preserve every completed pointer and receipt. Never return the legacy writer,
   erase a guard, refund quota or change an operation ID merely to retry.
2. A failure during progress attempts a durable pause and records confirmation.
   If that write/readback or audit is unconfirmed, report containment uncertainty;
   an independent operator must stop new admission externally and reconcile state.
   Do not claim service closure from a failed write.
3. Runtime admission requires active control, matching active confirmation in the
   audit store, an included subject and fresh observer evidence. Missing/unavailable
   evidence refuses a **new grant**. Historical results remain readable and already
   reserved valid work may finish once. This is not instantaneous cancellation of
   requests already admitted; independent draining remains necessary.
4. Active observation failure never mutates control into rollback. New grants fail
   while evidence is unavailable, and an operator may explicitly pause even when
   the observer is unavailable. Inspect and recover reads before making a decision.
5. A process crash leaving `running` is not blindly resumed. Inspect; externally
   fence/drain that process; explicitly pause; obtain fresh evidence and sign resume.
6. Core completion response lost: replay the same plan/reference and reuse the
   receipt. Do not call Apple or execute a model again to repair this response.
7. Uncommitted Apple intent expired: keep it immutable and remain paused. This
   exact batch cannot silently replace that plan. A separately reviewed replacement
   manifest/namespace and runtime switch are required; inspect current pointers and
   retain the old batch. Automatic replacement is deliberately not implemented.
8. Manifest, policy, registration or generation changes require a fresh review.
   A stale command must not pause or overwrite a different winning command.

## Storage capability plan (not deployed)

Use existing `GCSStateStore` adapters only for exact approved prefixes. The control
and stable-index identity needs conditional GET/create/update; immutable audit
needs create+GET; observer signing/writing uses a separate identity, with the
executor/runtime granted GET only. Neither receives media/research access, delete,
list, bucket policy changes or subscription purchase capability from this module.
The current repository does not provision these IAM rules. Before authorization,
name exact buckets/prefixes, source/image, accounts, roles, expiry and containment
operator, then prove cross-prefix/MMH/Demo denial and generation preconditions in
dedicated synthetic GCS. Do not reuse a test signature as live evidence.

This executor's 100-account subscription batch is distinct from UsageRollout's
up-to-1,000-account quota switch. It does not initialize or migrate v1 quota rows.
If both are switched, the quota program still requires same-UTC-day completion;
accounts outside its list get 503, and old v1 rows do not gain G2/G3 recovery.
Neither stage changes the selected Free2 / Plus10 policy or research consent.

## Reproducible verification

```text
python -m unittest -v test_lite_subscription_executor test_lite_subscription_executor_probe
python tools/lite_subscription_executor_probe.py --output fresh-executor-probe.json
```

Use project root, `tools`, `engineering/phase2`, `Backend/Flask` on PYTHONPATH;
`;` separators on Windows and `:` on Unix. Dependencies are pinned in the
`lite-prelaunch.yml` workflow. The probe uses synthetic signed Apple/App Attest
HTTP and real SQLite CAS. GCS adapter tests use a fake transport, not live GCS/IAM.
No production credentials are needed. Outputs use exclusive creation.

The paired HTTP rehearsal starts with one settled and one reserved operation,
loses a committed migration response, pauses, reads the old result, rejects new
admission, resumes, completes reserved/new work and repeats both requests. It then
pauses/resumes again. Expected total: three distinct model calls, three charges,
zero pending. Original registration, links, audit and research state are retained.

Real Apple Sandbox, native purchase/account bridge, cloud identity/fencing,
deployment rollback and mobile acceptance are still separate release gates.
