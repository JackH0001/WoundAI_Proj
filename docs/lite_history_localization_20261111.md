# Lite history, photo comparison and tracking language verification — 2026-11-11

## Result and scope

English devices previously continued to show Chinese history/detail, tracking, contour revision and depth-reference messages. The presentation now uses 171 paired English / Traditional Chinese entries. Persisted wound names, identifiers, location codes, capture dates, numerical measurements, revision payloads and receipts remain unchanged. The row source label also distinguishes `local` on-device inference from manual outlining; this corrects a display label, not any stored source value.

The shared image preview accepts two optional text arguments with its original Chinese defaults. Lite supplies localized zoom/reset labels; medical callers retain the defaults and all drawing/interaction behavior. Unknown errors remain verbatim. This is not a full-app English release: capture, guide, accuracy details and internal diagnostics still have untranslated content.

## Verification on the local candidate

- Ordinary Release / en-US: 613 passed, 0 failed, 1 documented simulator file-protection skip.
- Owner candidate Release / zh-Hant-TW: 613 passed, 0 failed, the same documented skip. These suites overlap; do not add their counts.
- Ten new XCTest cases cover table placeholders, original names/IDs, downgraded tracking without extra entitlements, whole-message revision translation, area precision/provenance, acknowledged no-op, pending deadline, consent/retired states, unavailable depth and shared medical preview defaults.
- An acknowledged revision 5 remains revision 5 with zero writes/sends while alternating language. A pending revision 6 preserves its payload and 120-second deadline; three retries after the deadline send exactly once, retaining original image/depth references.
- Isolated iPhone 16 Pro Max / iOS 26.2 simulator UI: two language flows passed, plus one final persisted-data readback. Six screenshots inspected: comparison, detail and tracking in each language. Original index and three generated images retain their hashes; research upload remains false and no cloud binding appears. No real patient data or physical phone was used.

## Initial QA failure and correction

The first post-UI readback found the fixture's research preference absent rather than false; index and image hashes already matched. A new isolated bundle reproduced that missing preference in a baseline readback before UI navigation. Thus the evidence did not establish a localization-induced consent change. The test-only setup was changed to set the preference after the host had settled, synchronize, and verify it across another launch before running both UI flows. That baseline, both UI flows and final readback passed. Initial failed results are retained alongside successful evidence; production consent code was not changed.

The generated circles and a pre-existing rectangular polygon intentionally are not a segmentation truth pair. Screenshots verify localization/state preservation, not RGB-depth alignment, segmentation or area accuracy. The QA bundle uses a separate app identifier, and fixture seeding exists only in the local QA test copy.

## Reproduction and limits

Use `project.institution.yml` for ordinary Release and `project.lite-sandbox.yml` for the owner candidate. Run WoundLite XCTest in en-US / zh-Hant-TW and the repository `tools/verify_xctest_results.py` with only the documented simulator protection skip. UI tests are under `LITE_QA_PHOTO_FIXTURE`; seed only an isolated QA bundle, verify settled consent before navigation, then verify unchanged index/media hashes after both languages. The review package includes actual summary/test-tree exports, logs, fixture helpers and screenshots for independent verification.

Evidence directory: `woundailite-history-localization-20261110` under the local Codex workspace. The directory start date differs from this final validation date. This local follow-up is separate from the authorized `0c75c4f` CI source. No cloud deployment, Apple delivery, real subscription, model inference or live upload was performed for this change. Physical file-protection validation remains pending.
