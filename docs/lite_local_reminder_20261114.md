# Optional local record reminder — 2026-11-14

The launch feature review requested simple reminders alongside local records and
PDF sharing. This change adds **Settings → Local record reminder**, available
without sign-in, subscription or research participation. It does not prescribe
dressing changes, treatment or medical visits. The reminder is off by default;
09:00 is only the initial picker value, not an enabled schedule.

## Behavior and data boundaries

- The user explicitly chooses one daily local time and presses Enable. Only this
  action can request iOS alert/sound permission; launching, returning to the App
  and opening Settings never request permission or recreate missing reminders.
- A separate, validated JSON journal saves the intent before scheduling. It uses
  the existing file protection / backup exclusion helper, without an account,
  wound, photo, area, depth, cloud identifier or quota field.
- The fixed identifier replaces this reminder when time changes. Confirmation
  requires exactly one matching request read back from the real notification
  center; an uncertain add result does not cause automatic retries.
- Denial explains how to enable iOS notifications. A missing schedule after a
  permission change or interrupted save requires explicit Save, not silent
  re-enablement. Corrupt storage is preserved, not reset to enabled defaults.
- Turn off attempts to cancel this reminder even if journal persistence fails,
  does not remove other identifiers, and does not falsely report success when
  cancellation cannot be confirmed. Existing record/media files are untouched.
- The local notification contains only the App name and a generic reminder.
  It has no photo, wound location, area, attachment, badge, userInfo or remote
  notification registration. Focus/system notification settings may delay or
  hide delivery; it must not be relied on for essential care.
- Settings, status/error messages and the offline operation guide are available
  in Traditional Chinese and English. Changing App language does not silently
  rewrite an existing notification; an explicit Save updates its language.
- Calendar trigger has only hour/minute, repeats daily and has no fixed timezone
  or date. It follows the device's local time. Travel/DST and real-device delivery
  remain part of owner acceptance, not a guaranteed exact-time medical alarm.

## Validation

Original baseline: `58b5dc0be5f66cb97999ce6e8c3f06201177182d`.
Test evidence is under the local `woundailite-local-reminder-20261113` directory;
the work began November 13 and final verification occurred November 14.

| Evidence | Result | Scope |
| --- | --- | --- |
| New reminder XCTest methods | 18 passed | Default off, explicit/denied permission, same-ID replacement, lost acknowledgement, add/write/removal failure, restart, revocation during add, concurrent actions, corrupt storage, notification contents, real protected journal and localization |
| Full ordinary Release | 672 passed / 0 failed / 1 existing skip | Existing plus 18 new tests; simulator file-protection-class limitation remains explicit |
| Full native Sandbox Release | 672 passed / 0 failed / 1 existing skip | Same suites with the native service build condition and its privacy manifest |
| Isolated full-App UI | 3 passed / 0 failed / 0 skipped | Chinese and English system permission → actual OS schedule readback → restart → explicit disable; denial → restart → no repeated permission dialog |

The two full suites overlap and are not 1,344 independent tests. Each UI scenario
used a new QA bundle identifier, the production screen/model and actual
UNUserNotificationCenter; no hook replaced the OS prompt or readback. TestFlight,
real research consent and owner records were not touched. The UI tests do not
prove physical-device notification delivery, Focus handling or DST behavior.

Visual review of the first UI run found that scene-phase refresh could replace
"Notifications are not allowed" with "Reminders are off" after denial. A new
regression test reproduced that failure on the previous implementation (two
failed assertions, preserved in `denial-before.xcresult`). The corrected refresh
preserves the denied state when no explicit choice has been saved; an explicit
Turn off remains off. UI checks now wait for controls to become enabled before
asserting status and capturing screenshots, avoiding transient false positives.

Final full-suite results include 18 new reminder tests. Earlier 671-pass runs and
transient UI screenshots are superseded, not counted as final evidence. Initial
xcresult export needed permission for Xcode's report cache outside the sandbox;
that was an evidence-export restriction, not a product test failure. The expected
before-fix regression failure remains available; no tests were suppressed.

## Owner acceptance after a separately authorized TestFlight delivery

1. Preserve the installed App and records. In Settings open Local record reminder.
   Confirm it starts off; opening the screen must not prompt for notification
   permission. Existing photos, PDF and research settings should remain intact.
2. Pick a time a few minutes ahead, press Enable and choose Allow in iOS. Confirm
   the saved time and scheduled message, then leave the App in the background.
   Check that any delivered message contains only the generic reminder.
3. Change the time and save. Confirm only the new daily time remains, including
   after relaunch. Notification visibility still depends on Focus and summaries.
4. Turn off; reopen and confirm off. If notifications are denied in system
   settings, verify the explanation and explicit Save after restoring permission.
   There is no need to take a photo, upload, subscribe or rejoin research.

This change is local source only until separately authorized for push/CI and
Apple delivery. It does not complete personal-service deployment, real Apple
Sandbox, research data disposition or external Beta App Review.

## Primary references

- [Apple: asking permission to use notifications](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications)
- [Apple: scheduling a notification locally](https://developer.apple.com/documentation/usernotifications/scheduling-a-notification-locally-from-your-app)

Apple documents context-specific permission requests, checking authorization
and local calendar triggers. The content minimization, journal and explicit
recovery rules above are WoundAILite implementation choices, not Apple claims
of medical reliability.
