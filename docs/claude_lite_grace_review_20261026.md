# Claude review — G4 signed Apple grace, 2026-10-26

Baseline `07f4faba8748e6488e5a2a2209b02f0603832137`; current commit identified in
the accompanying packet. Local-only continuation. Draft PR #36 is not updated
until a new exact-source push is authorized. No merge/deploy/store changes.

## What changed

`Backend/Flask/lite_subscription_authority.py` now verifies signed renewal proof
for current billing-grace status 4, cross-checks transaction/account/product and
grace expiry, and admits the existing total daily limit of ten until the earlier
of the signed deadline or the normal 30-second grant expiry. Previously all
status-4 requests failed unavailable. Both signatures are retained in v2 proof.
The official verifier is unchanged; no custom signature verification is added.

Missing/expired/contradictory renewal evidence does not invent free entitlement.
Refund, expired and retry-outside-grace keep the existing free floor and usage.
Completed retries still use the original receipt without another Apple query.
The existing account link hash, owner fence, ledger and research state remain
unchanged. **G4 policy/product migration is NOT implemented by this patch.**

## Before/after and tests

One frozen `07f4fab` authority module and the new authority receive the same
synthetically signed inputs, using the same unchanged pinned verifier and test
dependencies. Eleven cases are recorded in `before-after.json`:

| Case | Before | After |
|---|---|---|
| Valid grace | unavailable | limit 10, at most 30 seconds |
| Four seconds of grace left | unavailable | limit 10, capped to four seconds |
| Future renewal disabled, current grace valid | unavailable | current grace retained |
| Missing/expired grace evidence or Apple timeout | unavailable | unavailable, no free invention |
| Wrong renewal owner | unavailable without renewal check | rejected by binding validation |
| Active / expired / retry / refund | existing limit | identical decision |

- Full Lite suite: **933 passed**.
- All local test/probe commands from `lite-prelaunch.yml`: **901 test executions
  passed**, including **34 new grace tests**. These overlap; do not add totals.
- Focused new + existing authority/link tests: **85 passed** (overlapping).
- Authenticated synthetic HTTP probe: repeated first operation uses **one model
  call and one success charge**; missing current evidence does not reserve a
  new attempt. A second distinct operation after grace uses the free floor;
  the third is 429. Final two model calls/two charges, zero pending.
- Existing receipt remains byte-equivalent with Apple unavailable, zero status
  queries for reading it. New request is 503. Binding/registration/audit and
  research state remain unchanged.
- New GitHub CI has **not** run; prior 9-run/10-job success belongs to `07f4fab`.
  Native source/protocol/build numbers unchanged; no new native build claimed.

## Review map

- Implementation and invariants: `engineering/contracts/lite-subscription-grace-v1.md`.
- Signed-chain, boundary and HTTP regressions: `engineering/phase2/test_lite_subscription_grace.py`.
- Repeatable HTTP scenario: `tools/lite_subscription_grace_probe.py` and
  `engineering/phase2/test_lite_subscription_grace_probe.py`.
- CI addition: new signed-grace step in `.github/workflows/lite-prelaunch.yml`.
- Broader gates: `engineering/contracts/lite-commerce-rollout-gates-20261026.md`.

Run with the pinned requirements and PYTHONPATH from the workflow:

```sh
python -m unittest -v test_lite_subscription_grace test_lite_subscription_grace_probe
python tools/lite_subscription_grace_probe.py --output /fresh/path/grace-probe.json
```

Use a fresh output path; this probe does not overwrite its output. All account,
signature and HTTP fixtures are synthetic. SQLite/fake adapters do not prove
real cloud IAM, App Store grace configuration or real Sandbox behavior.

## Open items / what this does not claim

Policy/product-link migration (including unknown legacy policy and compatible
rollback), live provider/cache, verified human account and native purchase/
restore, real Apple Sandbox/online OCSP, TestFlight/cloud readback, state retention
and medical ba970b1-N1/N2/N4/L1/L2 remain open. G4 is partially completed, not
closed. No patient data, real original media, credentials or purchase JWS is in
the review packet. No public launch recommendation follows from these tests.

## Official basis

- [Apple grace configuration and Sandbox-first validation](https://developer.apple.com/help/app-store-connect/manage-subscriptions/enable-billing-grace-period-for-auto-renewable-subscriptions/).
- [Official verifier v3.1.2](https://github.com/apple/app-store-server-library-python/blob/v3.1.2/appstoreserverlibrary/signed_data_verifier.py).
- [Signed renewal payload](https://developer.apple.com/documentation/appstoreserverapi/jwsrenewalinfodecodedpayload).

Apple documentation supports entitlement during an existing grace period. It
does not prove this implementation has passed live Apple purchase testing.
