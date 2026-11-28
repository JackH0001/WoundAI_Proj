# Native subscription lifecycle wire acceptance — 2026-11-13

This extension uses the production Swift identity, purchase, service and balance
clients against the composed Python HTTP application. It changes only the
opt-in test harness and documentation. Apple transport, roots, transactions,
inference and KMS remain synthetic; it is not real Apple Sandbox acceptance.

## Scenario and acceptance conditions

The same account performs two free recognitions, purchases Plus once, and
performs a third recognition. Synthetic signed subscription responses then
advance through renewal, verified billing grace, provider timeout, missing
grace evidence, confirmed expiry and recovered subscription. Time advances
31 seconds between phases so the existing short entitlement cache cannot hide
the response changes. No production cache or authentication check is bypassed.

| Phase | Required behavior |
|---|---|
| Renewal, same original transaction, new transaction | Total daily limit 10; three uses retained, seven remaining |
| Grace with valid signed renewal information | Limit 10; three uses retained; fourth recognition permitted |
| Fourth recognition response lost, then Apple unavailable | Resume the same operation by status; no extra Apple lookup, inference or debit |
| Balance lookup during Apple timeout | Service unavailable, not a fabricated free balance |
| Grace status without signed renewal information | Service unavailable, not an inferred paid or free entitlement |
| Confirmed expiry after four uses | Free limit 2, used 4, remaining 0; new recognition denied before inference |
| Recovery, backend restart, restore twice | Limit 10, used 4, remaining 6; no new purchase or interactive sign-in |
| One additional recognition | Exactly five model calls, successful debits and attempts overall; five remaining |
| Saved third result | Local operation journal unchanged; research state and original synthetic image unchanged |

The rejected free-tier operation is retained but is not silently resubmitted
after recovery; the scenario creates a distinct explicit new recognition.
The original transaction, account token, product and environment are preserved
by every fixture transition. Only allowlisted phase names are accepted.
Normal backend JWS verification runs against the test roots, including signed
renewal information for billing grace. Evidence stores only transaction hashes,
phase, time and status, never JWS or account tokens.

## Reproduce and evidence

Eight Release native integration tests passed, with zero failures and zero
skips. Fourteen fixture tests passed. The new scenario produced five model
calls, five successful debits and five attempts. Five prepares and runs
returned 200; the expired-account prepare returned 429. Balance requests
returned five 200 responses and two 503 responses (provider timeout and
missing renewal proof). There was one successful status recovery and one
purchase confirmation. Restoration ran twice without a second confirmation.
One original-transaction hash and three distinct transaction hashes were
observed. The seven prior native scenarios also passed.

The first fixture invocation was blocked before execution by the local
sandbox's localhost binding restriction. Its log is retained separately; it
is not a product regression. Running with localhost access passed all fourteen
tests. The native run passed on its first execution. There were no product
changes or weakened assertions needed to obtain the result.

Use the opt-in runner and dependencies in
`lite_native_wire_acceptance_20261113.md`. The current runner requires exactly
eight passing native tests with zero skips and eight backend snapshots. The
fixture boundary suite has fourteen tests. These counts include earlier wire
scenarios and are not additive to previous reports or the full iOS suite.

Local result directory: `woundailite-subscription-lifecycle-wire-20261113` in the
existing review workspace. It preserves the original xcresult, summary/test
tree, body-hash HTTP observations, source hashes and fixture logs. The Claude
review package contains an offline evidence verifier and a patch against the
last published source. An evidence verifier checks saved results; it is not a
new execution of Xcode or proof of a live provider.

## Remaining external acceptance

The scenario exercises synthetic counterparts of Sandbox S5–S7. Actual Apple
renewal timing, billing retry, notifications, App Attest, TLS, Cloud Run/IAM,
real subscription cancellation/refunds and cloud persistence remain unverified
by this test. No payment, real sign-in, device record, research image, cloud
key, deployment, TestFlight delivery or research consent is changed. Owner
operational material preparation and separately authorized candidate deployment
are still prerequisites to the real Apple Sandbox run.
