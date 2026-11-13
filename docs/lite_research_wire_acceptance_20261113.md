# Native research RGB-D and revision wire acceptance — 2026-11-13

This is test-only integration evidence. Product sources are unchanged from
`089fbb138cb734f6103331994d793284091e55bb`. Previously native research delivery
used scripted transport responses, while real Python routes were exercised by
a Python client. This extension connects the actual Swift service recognition,
research delivery, encrypted local record store and revision sender to both
composed HTTP applications on loopback. Neither JSON nor multipart bodies are
rewritten by the test forwarding layer.

## Observed result

Five Release integration tests passed, with zero failures and zero skips. Eight
fixture boundary/readback tests passed. The previous four native scenarios also
passed; these counts overlap prior wire coverage and are not a new full Release
suite or CI result.

| Case | Readback |
|---|---|
| Service recognition followed by independent research upload | One model call, one successful debit, one attempt; distinct native/research installation identities |
| RGB-D saved, then response lost | One 4,188-byte capture request; no binding prematurely marked confirmed |
| Edit boundary while receipt is unconfirmed, then reload local index | One subsequent 1,579-byte receipt lookup, no repeated capture; original revision remains frozen |
| First annotation saved, then response lost | Native client retains revision 1; retry confirms the same revision before sending revision 2 |
| Annotation readback | Three requests, two stored label rows; surface values 4 then 5 and distinct expected polygons; same wound and image |
| Complete without another edit | No additional HTTP request and no revision 3 |
| Original media and AI contours | Same RGB-D object generation and bundle hash; original AI polygons unchanged; JPEG and depth float bit patterns preserved locally |

There is one source-ticket request and two receipt lookups in total (initial
lookup plus recovery). The packet contains a synthetic 64×64 JPEG and 8×8
Float32 little-endian depths with varying values. Stored metadata preserves
capture identity/time source, camera intrinsics, dimensions, orientation,
filter flag and RGB/depth hashes. Both artificial response losses occur after
the real server returns HTTP 200. No recognition retry is used to repair a
research receipt. The annotation payload and first revision survive encrypted
index reloads; unchanged completion emits no request.

The request byte counts describe this tiny fixture, not typical photos or a
cloud billing estimate. Synthetic 4→5 cm² values exercise serialization and
revision ordering; they are not measured area accuracy.

## Isolation and limits

The fixture forwards only loopback requests carrying its ephemeral token. It
uses two independently registered synthetic App Attest identities and normal
verification/routes. A native installation cannot select the research receiver;
unknown services and mismatched identities are rejected before dispatch. The
native service does not expose the research capture route. Readback comes from
persisted generation-aware objects and ledger rows, not an echoed request.

Synthetic consent callbacks explicitly permit this test only. No real user
consent, App Attest key, Apple purchase/login, cloud resources or TestFlight
records are read or changed. Apple, KMS and inference remain test providers;
GCS is a generation-aware fake, not the real service. The test is one simulator
QA host with real HTTP between Swift and Python; it does not prove physical
LiDAR registration, depth accuracy, real TLS/IAM, research withdrawal, account
deletion, cohort sampling, or full Apple Sandbox readiness. The original
`registration: not_verified` metadata is retained.

## Reproduce and review

Use the opt-in commands in `lite_native_wire_acceptance_20261113.md`. The runner
now requires exactly five passing native tests and five backend snapshots. The
eight fixture tests include service/identity separation and generation-preserving
readback. Tests are not added to a production App or enabled in ordinary builds.

Local evidence: `woundailite-research-wire-20261113/attempt1` contains the original
xcresult, parsed summary/tree, body-hash HTTP observations and source hashes.
`fixture-tests-final.log` holds the eight Python results. The compact Claude
package includes exact source, patch, evidence, SHA-256 manifest and an offline
verifier. It validates saved evidence; it is not a new test run or an external
provider attestation. No deployment or store upload was performed.
