# Lite usage reservation and subscription verification v1

Status at 2026-10-26: core connected to opt-in signed service-only HTTP via
`install_lite_attest(commerce=...)`, **not enabled by application startup or deployed**.
See `lite-service-operations-v1.md` for the new coordinator and current limits.
The existing `api_lite` JSONL check/append implementation and its default five
successful recognitions have not been replaced. The parallel race in that route
remains a rollout blocker. No real payment, ad delivery or Apple Sandbox purchase
has been tested by this change.

## Policy and identities

- Approved commercial direction: free two successful recognitions per UTC day;
  Plus US$1.99/month with a **total** of ten per day. Pricing is a planning input,
  not a server-side entitlement assertion or a configured live store product.
- Thirty recognition attempts per day remain an independent engineering bound.
  Empty results and known failures consume an attempt, not a success. Annotation
  revisions are a separate quota; they must not invoke the model or spend a new
  recognition credit merely to update a mask.
- A server-verified service account owns the allowance, not a client anonymous
  string, transport request UUID, research cohort, consent epoch or price flag.
  Withdrawal/reconsent must not reset paid/free credits. Two devices restoring
  the same purchase must resolve to the same service account. This registry and
  native `appAccountToken` acquisition/restore flow still need implementation.
- Research consent is independent. This core neither requires research uploads
  nor chooses the future 50% cohort. The current authenticated HTTP privacy gate
  still needs separate service-only routing before nonresearch use is ready.

## Required call order

1. Authenticate the device and service account, enforce request/body/capacity
   limits, then obtain a trusted current entitlement. Do not accept `DailyGrant`
   or `ServiceBinding` from JSON. Never downgrade a paid user because Apple or
   the proof store is temporarily unavailable.
2. The server issues a UTC day and stable logical operation ID. Persist these on
   the client before sending; all transport retries retain them. Compute the
   canonical input digest from image bytes and all model-affecting parameters
   and model version. A different digest under the same ID is a conflict.
3. `reserve` atomically checks successes plus pending reservations against 2/10
   and attempts against 30. Only after its outcome is known may work proceed.
4. `claim` is the single execution gate. Only **True** authorizes a model call.
   False means return/poll the existing receipt; it never authorizes execution,
   including when the same worker repeats after losing the response.
5. Durably save the result and its hash before settling. Call `settle` with a
   known outcome and immutable receipt digest. A worker must not turn a timeout
   or uncertain result into `failed`: leave it running for reconciliation.
6. Return a receipt with operation/day/digest/result hash/grant proof. Remaining
   allowance is a separate snapshot, not part of an immutable operation receipt.

Transport retries may require fresh App Attest challenges and counters. Those
fresh security tokens must not create a new logical operation or resend raw RGB-D
when already retained by the service. The coordinator/HTTP path is implemented
and tested locally; native clients and the existing segment endpoint have not
switched to this protocol.

## State and failure behavior

`UsageLedger` stores one bounded row per namespace/account/day using the existing
SQLite/GCS compare-and-swap adapter. SQLite is for tests/local disk, never Cloud
Run ephemeral disk. GCS uses generation preconditions; unknown commit responses
are unavailable, not permission to run. The row has at most 30 operation entries,
each with reserved/running/terminal history and proof/result hashes, within the
adapter's 16 KiB bound. Schema corruption fails closed; there is no inferred reset.

Pending work deliberately occupies capacity. There is **no automatic lease expiry,
worker takeover or model retry**. An unstarted reservation may be explicitly
cancelled; its attempt history remains. Running jobs require a future bounded
reconciliation worker with durable result evidence. Availability is sacrificed
where an ambiguous outcome could otherwise spend compute twice. The intended UI
must distinguish quota exhaustion, pending result verification and service wait.

Settling after midnight charges the original day. The new day has its own row.
A late original retry must retain its old day; changing the day is new work.
Upgrade grants a total of ten, preserving used credits. Refund/expiry prevents
new excess work; already admitted work is honored. A 30-second grant snapshot is
not instantaneous revocation, and a reservation may finish after it expires.

Rows are append-only histories by **application behavior**, not WORM storage.
Protected namespaces, minimal IAM, independent audit retention, generation
retention and recovery against administrative replacement remain deployment
requirements. No delete/list interface or cloud provisioning is added here.

## Apple verification

Use the installed and pinned `app-store-server-library==3.1.2`, not copied JWS
verification code. Read current `get_all_subscription_statuses` without an ACTIVE
filter, check application/environment and the uniquely bound original purchase,
then verify the transaction signature and certificate chain. Check product,
account token, purchased ownership, type, transaction ID and timestamp chronology.
After signature verification, check original JSON types and duplicate keys rather
than relying on model conversion that may coerce booleans/numbers.

The deployment verifier factory requires explicit roots and enables online checks;
it rejects Xcode/local-testing environments. Roots must be obtained from Apple PKI
and reviewed/pinned. Tests use generated EC chains with Apple's extension OIDs and
real ES256 signatures through the official verifier, **synthetic roots and disabled
OCSP only for fixtures**. Server API responses are fakes. No Apple service is called.

Persist a create-only owner binding and signed-status evidence before returning a
short-lived grant. A fresh API revoked status overrides an old unexpired JWS.
There is no active-entitlement cache or notification writer in this change; this
does not demonstrate notification ordering/delivery. Billing grace currently
blocks with an explicit unavailable result until signed renewal and storefront
grace policy are implemented. Family sharing and account transfer are unsupported.

## Migration, recovery and rollout gates

1. Define stable service accounts independent of research, restore ownership,
   account deletion/recovery and anti-abuse. Verify genuine Sandbox purchases,
   cancellations, refunds, grace and subscription notifications.
2. Implement persistent client operation/day/result references and the bounded
   HTTP coordinator; test lost response/relaunch/revision without photo reupload.
3. Prepare dedicated mutable quota storage and protected proof/audit storage;
   record exact IAM, retention, SDK telemetry configuration, versioning, limits and
   rollback. GCS metadata probes and same-object mutation throttling need actual
   latency/cost acceptance; offline CAS correctness is not throughput evidence.
4. Pause legacy writers before cutover; snapshot exact current-day success and
   attempt counts with a hashed manifest. `initialize_day` requires an explicit
   baseline, even if zero; it never modifies an existing baseline or creates an
   old day. Ambiguous baseline blocks. A new-day zero is allowed only after the
   trusted controller proves there are no legacy writers for that day.
5. Enable one admission authority at a time. If rollback is necessary, close new
   work, retain and reconcile all pending/result/proof records, and do not resume
   the old check/append ledger with a stale balance. Never clear receipts to reset
   counters. Unknown jobs need reviewed resolution, not automatic reruns.
6. Perform bounded synthetic cloud acceptance under a separate fixed-source
   approval, then TestFlight verification. No production deployment, cleanup,
   paywall activation, ad-provider connection or public listing is authorized by
   passing these local tests.

## Reproduction and references

Install the existing offline test dependencies plus
`engineering/phase2/requirements-commerce-test.txt`. Set
`PYTHONPATH=tools:engineering/phase2:Backend/Flask:.` and run:

```
python -m unittest -v test_lite_usage_ledger test_lite_subscription_authority test_lite_usage_probe
python tools/lite_usage_probe.py --output atomic-usage-probe.json
```

The output file must not already exist. The manual Linux workflow
`.github/workflows/lite-prelaunch.yml` includes these commands and the existing
regressions. No macOS CI minutes, live service, credential or payment is required.

- [Apple official Python library](https://github.com/apple/app-store-server-library-python)
  provides the Server API client and signed-data verifier. The installed wheel,
  not upstream `main`, is the API compatibility baseline.
- [SignedDataVerifier API](https://apple.github.io/app-store-server-library-python/appstoreserverlibrary.signed_data_verifier.html)
  documents signature verification, application/environment parameters and roots.
- [GCS request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)
  explain generation-based conditional updates and safe conditional retries.
