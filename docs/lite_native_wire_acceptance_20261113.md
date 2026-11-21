# Native Swift / Python wire acceptance — 2026-11-13

This adds an opt-in integration harness, not a deployed service or a change to
product behavior. Previously the native composition tests used scripted Swift
transport replies while the composed Python HTTP tests used a Python client.
Here the actual `LiteNativeSession`, identity, purchase and service clients send
their own JSON and multipart bytes over loopback TCP into the composed Flask
router. Responses are decoded and persisted by the production Swift clients.

## Results

Two Release simulator tests pass, without skips, plus three fixture boundary
tests. These are additional focused results, not a rerun of all Lite tests.

| Scenario | Observed result |
|---|---|
| Two free recognitions, then a third attempt | Two completed; third prepare returns 429 without running the model |
| Purchase Plus | Daily total becomes 10, with the original two uses retained |
| Restore twice | One purchase call in total; one server purchase confirmation; no quota reset |
| Restore an event with another account token | No additional purchase confirmation; usage remains three of ten |
| Logout | Further service access is denied; completed local journal remains readable |
| Model result returned but client loses the reply | Client saves uncertainty; retry reads the existing result |
| Recreate Swift session and Python graph from their durable stores; advance 1,201 seconds | Session renews once without a second interactive Apple login; one model call and one debit overall |
| Research and original synthetic JPEG | Research privacy state and original bytes remain unchanged in both scenarios |

The first draft used a 32-character operation identifier where the saved-record
API correctly requires a UUID. It failed before inference. The fixture was
corrected to pass real UUID-shaped synthetic record IDs; product validation was
not weakened. A fixture snapshot before login also initially assumed an account
existed; this evidence-only bug was corrected and its three boundary tests pass.
Failed attempts remain in the local report directory.

## Boundaries

* Apple identity/JWS, KMS, device proof and inference are synthetic. The normal
  backend signature verifiers run against synthetic roots; the fixture middleware
  adds test App Attest assertions. This does not test the real iOS App Attest API.
* Only the QA transport maps the validated HTTPS origin to loopback and maps the
  response URL back. TLS, Cloud Run routing, provider IAM, mounts and actual Apple
  Sandbox are not covered. No production authentication bypass is added.
* The host is a separate simulator App with an isolated file journal. No physical
  device, original TestFlight record, cloud key, purchase, or research media is used.
* The server binds only 127.0.0.1 and requires an ephemeral bearer for every route,
  limits request size, and exposes only synthetic fixtures. Its test-only bearer
  is removed from generated resources after the server stops.
* Existing full Release results (646 per configuration) belong to the preceding
  guide change; they are not added to these counts or presented as newly rerun.

## Newly measured follow-up

The first scenario emits 41 `account/status` calls while executing three model
calls; the lost-response/renewal scenario emits nine. These are not repeated
model executions or debits. The current nested admission checks call
`canUseService()` before and after requests, which performs a status request.
This is now a measured request-overhead baseline, not a cloud cost/p95 result.
Before broader testing, evaluate request-scoped validation that preserves
renewal, logout/deletion races, origin and account checks, and backend
authorization. Do not simply remove checks or cache revocation state indefinitely.

## Reproduce

Install the repository backend test dependencies, Xcode, XcodeGen and PyYAML.
Use a dedicated simulator; output must be a new directory. The runner copies
tracked App sources, records their hashes and generates an isolated QA project.

```sh
PYTHONPATH=tools:Backend/Flask:engineering/phase2 python -m unittest tools/test_lite_native_wire_fixture.py -v
python tools/run_lite_native_wire.py --output /absolute/new/output --simulator SIMULATOR_UUID
```

The runner saves the original `.xcresult`, summary/tests JSON, request body
hashes, route counts, model/ledger counters and source binding. It does not
deploy, sign with production credentials, change research consent or enroll a
real device. It is opt-in and not silently added to the normal App test target.

## Evidence and next dependency

Local evidence: `woundailite-native-wire-20261113/` under the existing review
workspace. See `verification-report.html`, `CLAUDE-REVIEW.md` and the hash-checked
review ZIP. Native cloud initialization still requires the owner-created encrypted
operational materials and separately authorized fixed deployment/IAM plan.
Actual Apple sign-in, purchase, restore and provider failure recovery remain
explicit external acceptance steps.

## Later extension on the same day

Request-scoped admission was subsequently fixed in `089fbb1`; its separate
regressions retain revocation and logout/relogin fences while reducing redundant
status calls. See `lite_request_admission_20261113.md` for the before/after counts.
The harness now also covers two separately registered installations, shared
five-wound limits and downgrade/restoration with local timeline preservation.
The four-native / five-fixture run is documented in
`lite_wound_wire_acceptance_20261113.md`. The latest research RGB-D extension has
five native tests and eight fixture tests; see
`lite_research_wire_acceptance_20261113.md`. The subsequent withdrawal/rejoin
extension requires six native tests and ten fixture tests; see
`lite_research_privacy_wire_acceptance_20261113.md`. Earlier counts above describe
their original runs, not the latest harness.

The next account-deletion extension runs seven native tests and twelve fixture
tests. It covers lost intake acknowledgement, reopening both client and backend,
both installations losing ordinary service access, and explicit bounded fake
Apple revocation. See `lite_deletion_wire_acceptance_20261113.md`. It does not
claim that all account data or linked research backups have been erased.

The subscription lifecycle extension requires eight native tests and fourteen
fixture tests. It connects renewal, signed billing grace, provider outage,
confirmed expiry and repeated restoration to native balance/result readback.
See `lite_subscription_lifecycle_wire_20261113.md`; real Apple Sandbox remains
a separate acceptance gate.

## Independent external tester accounts (2026-11-18)

The ninth native scenario signs two different synthetic Apple users into two
separately registered installations. It uses the shared wrapping-key startup
configuration and ordinary service routes. The existing two-device test still
signs the same user in on both installations and checks shared wound slots.

One account purchases Plus before the other first signs in. An intact StoreKit
transaction from the subscriber is presented during the free account’s restore.
The app must report an account mismatch before confirming or finishing that
transaction. After rebuilding the backend from its stores and reopening each
client journal, two restores by the original subscriber must succeed from the
same purchase. Each wound roster and every saved result must still match its
original account. The backend evidence for `two-independent-accounts` records
distinct subjects and purchase tokens as booleans, the subscription links as
`[true, false]`, and the actual debits as `[1, 2]` in subscriber/free order.
The app checks daily limits of 10 and 2 and wound slot limits of 5 and 1.

The commands under **Reproduce** cover the native scenarios and fixture tests described here. Apple identity, purchase, KMS, and inference providers are
simulated. Real Apple sign-in, App Attest and Sandbox restoration with a fresh
external tester are separate release checks.


## Restoring on a fresh installation (Sandbox case S4)

The tenth native scenario uses two independently registered installations, separate file journals and the same synthetic Apple user. The first installation uses one free recognition, buys Plus and registers a wound. The second installation signs in through the subscription page, then restores the original StoreKit event. It receives no copy of the first installation’s photo or saved result.

The fixture drops the first successful confirmation reply to the second installation. After restarting the backend and reopening the second journal, restore resends the same confirmation and finishes the transaction locally. A further restore can use the saved acknowledgment. The expected server trace has three successful confirmations (original purchase, lost restore reply, retry). The second and third requests match after JSON key ordering is normalized. All three observations must show one durable receipt with the same hash, including the stored revision. The second installation makes zero purchases.

A subsequent recognition on the second installation brings both balances to 2 used and 8 remaining. Both see the same active wound with a limit of 5, and the first installation still reads its original result. Run all ten scenarios and the fifteen fixture tests with the commands above. Apple identity, StoreKit transactions, the model and cloud providers are synthetic. For the real S4 acceptance, record both builds and the actual backend revision and repeat on a second installation using the original Sandbox purchase and service account.
