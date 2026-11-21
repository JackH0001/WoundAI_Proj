# Recover a saved research capture after a lost response

This local change follows `5a0bce7`. TestFlight build 38 was exported from `fa827a5`.

## Capture, save and recovery

In the native candidate, the camera reads the current research origin and
consent epoch at capture. If an original research installation and signed-in
service account are already available, it can save that identity with the
encrypted draft. The independent research receiver verifies consent, App Attest
and the signed service result. The app keeps the camera callback UUID from the
depth sidecar and the local measurement UUID separately. An old measurement
with no capture-time participation information stays local.

Saving the local record freezes the first eligible annotation and its wound
location. A later edit stays on the current record. The foreground retry loop
reads the exact JPEG and depth, obtains a source ticket, encrypts it and saves it
before attempting any media upload. The server-signed JSON is carried as an
opaque string so its original numeric spellings survive transport.

The authenticated research service accepts a JSON POST to
`/api/v1/lite/research/capture/status`. It resolves the original installation
and consent epoch and checks the ticket, image size and bundle digest against the
published intent, index and final metadata. A completed capture returns its
original storage receipt. This checks durable publication evidence without
reading the full JPEG or depth object again. Current withdrawal status is
checked before responding. The lookup does not create a research writer.

Only a matching `upload_required` response authorizes the client to send the
media. Unknown replies, uncertain reads, different original bytes or conflicting
receipts stop that attempt. An expired ticket may retrieve a completed receipt.
For a missing or incomplete capture the server requests a fresh ticket before
the app sends any media. An unavailable service result leaves the original
record on the phone.

The app matches the receipt to the capture UUID, deterministic image ID, original
photo, raw depth bytes and bundle digest. It commits the cloud binding and the
encrypted original receipt and frozen first annotation in one index write, preserving intervening edits. The
existing revision queue then sends revision one before later edits. The recorded
storage status describes the bytes. Geometric alignment and training admission
remain separate checks.

## Signed request dispatch

Code inspection found that a request could hold the App Attest signing lane and
ask that same lane for the installation again. This affected the previous native
service guard and initially also affected the new research lookup. A mock
transport that only counts requests cannot reproduce this wait.

Service renewal and normal authorization run before signing. At dispatch the
native client checks the captured, unexpired account session, Apple credential
state, local deletion intent and account generation using the known installation.
Inside the research signing callback it checks the original account, local job
and consent. The signing client itself verifies the research installation.
Both guards are checked again immediately before business dispatch. No callback
waits on its own signing lane. Synthetic tests run the actual signing client
against generated registration, assertion and HTTP responses. They cover
completed recognition, logout, receipt recovery and the lost-response path.

## Configuration and acceptance

- On iOS, `WOUNDLITE_RESEARCH_CAPTURE` defaults to `NO`. The native candidate
  enables the receiver client only when built with `WOUNDLITE_RESEARCH_CAPTURE=YES`
  and the dedicated research origin. The ordinary build does not construct a
  native session. A native candidate without its service session can still keep
  local measurements and does not fall back to the legacy research inference route.
- Both server roles still require the existing purpose-specific signer settings,
  the dedicated bucket, and `LITE_RESEARCH_CAPTURE_ENABLED=1`. The native and
  research runtimes continue to authorize requests independently.
- Saved-record QA is a separate internal build option. The candidate and
  ordinary profiles hide that history link by default, including while research
  capture is enabled. Only an explicitly internal native build can opt in
  using `WOUNDLITE_SAVED_RECORD_TEST=YES`. The existing local PDF action is
  independent of that option.
- Run the four suites in `docs/lite_research_capture_20261102.md` and
  `tools/lite_research_capture_probe.py` on synthetic fixtures. The probe now
  uses the JSON lookup for its retry. Check original bytes, revision numbers,
  model calls, personal usage and the actual lookup body.
- Run the WoundLite Release tests for both the ordinary and native projects.
  `LiteNativeSessionTests` covers interrupted receipt delivery, a later edit,
  old and changed consent, an incorrect depth receipt, source expiry and the two
  signing lanes. The camera test uses an isolated synthetic consent journal so
  a previous test installation's deletion or withdrawal remains effective.
- Pending acceptance includes the real iPhone file-protection check, provider
  readback across both deployed candidates, long-offline source expiry, and
  an actual Apple Sandbox login and purchase. Verify that the account-deletion
  worker can resolve the original research registration and apply the selected
  withdrawal policy before allowing real participant data. These local results
  do not replace those checks.
