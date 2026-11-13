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
Its current four native / five fixture tests and explicit synthetic boundaries
are documented in `lite_wound_wire_acceptance_20261113.md`; the two-test figures
above describe the original run, not the latest harness.
