# MMH local export retry — Android 30 / iOS follow-up

Android 30 (validation r11) adds an explicit action on each retained encrypted export copy: export that copy to the configured folder without uploading. The action writes only image and overlay, retains the encrypted original on failure, prevents concurrent taps, and does not authenticate, enqueue, or change research receipts. Selecting a new folder explains that existing copies can be delivered directly.

The iOS settings section now observes its environment's outbox so changes to available submissions refresh the retry button while the view is open. When no submission matches the current account/environment, it explains the restriction and points to existing local copies. Account/environment isolation is unchanged. This is a follow-up source change, not part of the already distributed TestFlight build 38.

## Validation, 2026-10-16

- Android MMH internal: 132 unit tests passed; MMH validation: 132 passed.
- Emulator: 45 export/outbox integration tests passed.
- System folder picker and separate-process restart: 2 tests passed. The restart test uses the UI export-only action, verifies exactly image/overlay, zero network-client requests, unchanged outbox and unchanged encrypted source. Revoked folder permission preserves the copy and yields a retry message.
- Android AAB: package com.woundai.app.mmhps20261007, versionCode 30, no debuggable=true or ARCore declaration; ZIP integrity verified. Signing and Play publication pending.
- iOS follow-up: 47 tests passed, 1 skipped because the simulator had no prior system folder grant. The skipped test is not counted as a pass. Tested export, runtime/account isolation, tissue count and outbox behavior; a live settings-button transition remains to be verified.
- User acceptance on TestFlight 38: original tissue_edit_ratio item uploaded after explicit confirmation. Demo image/overlay exported after another submission; a subsequent settings-only retry also exported successfully. The first retry of the original record without re-submission was not demonstrated, and must not be claimed as passed.

No new backend deployment, IAM change, research-data deletion, or PR merge is included. Android 30 must be signed with the existing upload key and pass CI before Play internal publication. This emulator validation does not replace Play-signed physical-device acceptance.
