# Lite 46 research rejoining: service mismatch and explicit feedback

The owner installed TestFlight 46 and reported that the research upload switch
stayed off after reading and accepting the rejoin disclosure. The supplied light
and dark screenshots still showed the earlier completed withdrawal. They do not
prove that the disclosure failed to open or that the user did not confirm it.

## Evidence and cause

Read-only Cloud Run metadata and bounded HTTP logs on 2026-11-12 showed two rejoin
requests at 17:15 and 17:16 Asia/Taipei returning HTTP 404. Their challenge requests
returned 200. The research service was still revision `00009-kid`; its allowed
build configuration listed `35,36,37`. This is evidence of an unavailable rejoin
route in the deployed path and a separate build-policy mismatch. No 401 for
build 46 was observed in that readback, and we do not infer one.

The current repository has the rejoin route, but repository/local test success
does not deploy it. Merely adding 46 to the old service's allowed-build setting
would not fix the missing endpoint. Re-registering or replacing the installation
identity would not fix it either and must not bypass the earlier withdrawal.

## Local change

- Distinguish an unavailable rejoin endpoint (404/405), rejected verification
  (401/403), network interruption, capacity wait, and missing original identity.
  Unknown errors remain unconfirmed. A 404 from a challenge is not mislabeled as
  proof that the rejoin endpoint is absent.
- Dismiss the explicit consent sheet before starting the request and presenting
  its result alert. Show progress and a visible completion/failure message.
  Declining or dismissing without confirmation does not start a request.
- Keep uploads off until the receipt matches the original owner, prior/new epoch,
  disclosure and consent versions, scope, and non-restoration constraint.
  Preserve the immutable intent across an explicit retry; do not auto retry,
  regenerate an identity, restore old media, or enqueue old records.
- Explain device registration separately. `registeredInstallation()` can return
  an already registered local identity without another HTTP request. The UI now
  says when it used this saved registration instead of suggesting a fresh cloud
  health check. Initial registration can still perform App Attest enrollment;
  this action sends verification material, not photos.

Device registration associates requests with this app installation and supports
request verification and anti-abuse controls. It is not a research-consent
decision, a subscription purchase, proof of a photo upload, or LiDAR calibration.

## Verification

- Ordinary Release (Traditional Chinese) and native candidate Release (English):
  **640 passed each, 0 failed, 1 existing simulator file-protection skip each**.
  The suites overlap; do not add them together. Seven added tests are included.
- Focused rejoin/withdrawal/registration/copy suite: **62 passed**, included above.
  Saved-registration testing confirms no new challenge or registration request.
- Two isolated UI tests, Chinese and English: **2 passed**. Each uses synthetic
  completed withdrawal, simulated HTTP 404, app restart, then an explicit second
  request with a matching simulated receipt. The actual settings/disclosure
  views and response parser are unchanged in the QA host; only app entry,
  synthetic identity/journal location, and network dependency are substituted.
  This is not a real Apple or Cloud Run rejoin acceptance test.
- Synthetic readback: two explicit requests per language use the same intent;
  original withdrawal bytes are retained in the intent and final audit; one
  success audit per language; no historical media restoration.
- Existing backend `test_lite_reconsent`, `test_lite_attest_http`, and
  `test_lite_withdrawal_fence`: **53 passed**, with a loopback-only HTTP fixture.

Initial attempts exposed environment/harness issues: restricted simulator access,
a missing Python dependency set, loopback binding restrictions, and an actor
annotation in the synthetic QA host. Those attempts and logs are preserved;
none is reported as a product test pass. The final runs above use the existing
test environment and corrected QA host. No product logic was relaxed to pass.

## Remaining deployment work

The source fix is local, not a change to the installed TestFlight 46. The cloud
readback made no configuration changes, research-payload reads or secret reads.
Original records, withdrawal journals, identities and audits remain intact.

A deployment proposal must pin a tested research image with the rejoin route and
its fenced-privacy/epoch dependencies, preserve the existing service origin and
installation bindings, and admit only the agreed latest stable app build. Test
an isolated synthetic withdrawal/rejoin flow, delayed old writes/deletes, lost
reply retry, and version rejection before enabling owner acceptance. Preserve
the old revision/configuration for containment; do not roll storage back or
restore withdrawn data. If new epochs have been written, the legacy service is
not an automatically safe rollback target; pause affected writes and reconcile.

Native personal service deployment, its initialization and Apple Sandbox are
separate pending work. This change does not authorize or execute cloud IAM,
storage creation, cleanup, deployment, GitHub push, or another Apple delivery.

Local evidence and Claude package: `woundailite-rejoin46-20261112` under the
existing Codex evidence root. See `cloud-metadata.json`, XCTest summary/tree
pairs, `ui-readback.json`, synthetic UI attachments, and the review verifier.
