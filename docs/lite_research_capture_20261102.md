# Research capture after personal recognition

Local candidate after `1135fa9`. Neither TestFlight 38 nor the published
`1135fa9` includes these routes. Both services default to disabled for this
feature. No patient data, real cloud storage or Apple Sandbox was used.

## What this changes

Previously, replaying the research segment request for the same image could
run segmentation again and produce a second index entry. Once personal recognition
has already completed, saving the consenting user's image and its original result
should not run another model. A manual contour may also be recorded without
inventing a model result. The phone must still register with the research service
and the user must consent; a service result is not research consent.

1. The native service issues a signed, short-lived source ticket only to an
   attested installation with an active service account and session. For a
   completed recognition it reads the existing result and settled receipt. The
   stable signed claim binds the capture UUID, image hash, original model and
   result to the research installation, destination and consent epoch.
2. The independent research service accepts that claim and the original JPEG,
   optionally with the exact `lite.rgbd/2` metadata and depth bytes. It checks
   the signed request, consent version and epoch, original image dimensions and
   hash, capture identity, and the existing face-screening rule. It does not
   have to access the personal account's usage ledger. A manual source cannot
   supply artificial model output.
3. A deterministic capture ID and immutable intent bind all input bytes and
   provenance. The index entry becomes visible only after reading back the
   JPEG, depth bundle (if present), and final metadata. The receipt confirms
   storage, not the final annotation, depth alignment or fitness for training.
   The normal revision endpoint stores subsequent edits separately.
4. Withdrawal applies to in-flight writes as well as future admission. Fresh
   consent opens a different epoch. Old requests cannot change the new epoch.
   All capture objects use the existing ownership manifest and withdrawal fence.

## Routes, quotas and retries

| Service | Authenticated POST | Result |
| --- | --- | --- |
| Native | `/api/v1/lite/service/research-source` | Source ticket; no new inference, usage settlement or media upload |
| Research | `/api/v1/lite/research/capture` | Immutable storage receipt, including the raw-depth receipt when present |
| Research | `/api/v1/lite/annotation/revision` (existing) | Versioned annotation receipt; no new JPEG or depth |

The capture admission limit (30 new captures per installation per UTC day)
is separate from personal recognition credits and annotation submissions.
Identical attempts reuse a persisted intent. Admission may consume a slot if
intent persistence fails; it does not charge the personal account. The claim is
stable and the ticket lasts 10 minutes. Expired tickets can confirm an already
completed, identical capture. An incomplete capture needs a fresh ticket,
not another inference. Conflicts and uncertain storage retain local data.

## Verification and limits

The reproducible probe uses generated App Attest signatures, independent Flask
applications, synthetic recognition/Apple responses, SQLite, and a
generation-aware object-store test double. It does not contact a provider. The
legacy comparison is two requests to the unchanged research segment route;
it is not a comparison of clinical model quality or personal charges.

| Check | Result |
| --- | --- |
| Legacy research segment called twice | Two model calls, two index entries |
| Personal recognition followed by research save and replay | One model call total, one personal recognition used, one research capture |
| Original JPEG and raw depth | Both byte-for-byte equal after storage |
| Revision 1 retried, then revision 2 retried | Two annotation entries, equal receipts on each retry; original media generations unchanged |
| Withdrawal followed by a late upload | HTTP 410; current synthetic media sealed, original personal receipt and usage unchanged |
| Withdrawal racing a write; fresh consent | Old writes or late withdrawals cannot modify the new epoch in the synthetic fixture |

For an independent replay from the checkout, install the existing prelaunch
workflow dependencies, set `WOUNDAI_STORE=local` and put `tools`,
`engineering/phase2`, `Backend/Flask` and the checkout on `PYTHONPATH`.
Then run:

```sh
python -m unittest -v test_lite_research_source test_lite_research_source_http test_lite_research_capture test_lite_research_ingest_flow
python tools/lite_research_capture_probe.py --output probe-new.json
```

The four focused suites have 45 tests (16 + 6 + 21 + 2). Local full discovery
ran 2,220 tests: 2,207 passed; 13 could not bind a loopback socket in the
restricted environment. All 13 were then exercised in the four affected modules
with local loopback enabled: 40 passed, including those 13. These overlapping
counts are not additive; the two original run logs are retained.

- This change supplies the server contracts. The iOS consent-bound outbox,
  retry UI, physical device acceptance, and candidate cloud readback remain to
  be connected and tested. It does not change TestFlight, the deployed API or
  the public build.
- A repeated multipart request can still transmit image bytes. Idempotent
  storage means no duplicate object version, inference or recognition charge;
  it does not guarantee zero bandwidth. A receipt-only recovery path is needed
  before promising a lightweight retry to the user.
- The native result has a bounded lifetime. A receipt that can no longer be
  exported must leave the local record intact and must not silently trigger
  recognition again. Long-offline completion still needs client acceptance.
- The research data contains signed pseudonymous provenance. Treat this as
  research data subject to the same consent, access, retention and withdrawal
  controls, not as anonymous analytics. Sealing currently visible test objects
  does not establish erasure of all historical generations or backups.

### Candidate route acceptance

Run `test_lite_research_ingest_flow`, `test_lite_research_issuer` and
`test_lite_service_profile` together. The research fixtures install the same
first request hook as the Lite service. On the research origin, a canonical
POST to the configured capture, receipt lookup or deletion-proof endpoint must
reach device authentication. Check for 401 on unsigned input and a matching
receipt on a valid signed request. Disabled endpoints, wrong methods, query
strings and native-only paths must return 404. Checking only `/api/health` or
testing a blueprint without the first request hook can miss a broken upload.
