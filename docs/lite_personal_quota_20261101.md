# WoundAILite research and personal quota displays

The research endpoint returns an installation balance for a research capture. Its verified receipt can appear only in the research UI for the same origin, registration, consent epoch and unexpired period. The user can decline research and continue to access the personal service. Ordinary builds without the native service do not show a personal-account balance.

The candidate settings include **查看個人帳號今日額度**. The screen opens locally; only **核對今日額度** sends an App Attest-signed, authenticated POST to `/api/v1/lite/service/balance` on the native service. It sends the existing installation and expected account digest. The native service derives the account and current verified subscription grant, reads that account's UTC-day ledger and returns a timestamped snapshot. Unknown baselines, unavailable entitlements, closed accounts and inconsistent rows do not produce a balance. A paid lookup may require Apple verification under the ordinary entitlement policy.

Free and Plus limits are 2 and 10 successful recognitions per day in total. Reservations and running work occupy capacity; elapsed unstarted reservations are discounted on a validated in-memory copy. A separate attempt limit can prevent new work even if successful uses remain. A read does not create a day row, spend a recognition, start a model, reserve an operation, send an image, or modify research consent. The existing security challenge/counter and admission limiter still apply. Records and balances may change on another device after the observation. Backgrounding, account changes, the freshness window and the UTC reset hide stale snapshots.

Run `test_lite_personal_balance` for signed HTTP and paid/free comparisons. `LitePersonalBalanceTests` covers the request body, account and origin binding, timestamps, and research receipt separation. `NativeEntryUITests.testPersonalQuotaHasItsOwnEntryWithoutResearchOrDeviceRegistration` checks the settings navigation before enrollment. Live App Attest, Apple Sandbox transactions and actual provider behavior remain external tests.

## Continuing a photo after an allowance refusal

The measurement screen reads the signed-in account’s current balance when the saved operation advances. It offers Plus when the free successes have been spent and an upgrade would add capacity. When a photo is waiting, its retry control shows the next check time in the device’s locale.

Admission can return HTTP 429 with `reason=success_quota` and an optional `success_quota_state`. Both the initial check and the reservation under contention use the same rules.

| State | When the app may check the original operation again |
| --- | --- |
| `pending` | Work in progress is holding the available uses. Retry after 30 seconds or at the UTC reset if sooner. |
| `used`, with additional uses possible after an upgrade | The current grant is spent. Allow another check on the same schedule so a verified upgrade can take effect. |
| `used`, with the full Plus allowance already spent | The server reports the time to the UTC reset. |
| `attempt_quota` | The independent attempt limit takes precedence and lasts until the UTC reset. |

The retry time specifies when to ask again. A new request rechecks the current grant and ledger before sending the photo. The iOS journal preserves the operation ID, image digest and retry deadline across relaunch, with the existing one-hour cap on client backoff. Older servers and saved refusals without the additional state use the general allowance message.

## Viewing Plus across a plan or UTC-day change

While the Plus page is open for the same signed-in account, it reads the balance again when the first snapshot expires. The deadline is the earlier of the next UTC day and five minutes after the server timestamp. If this read fails, the page offers a manual retry. If a second successful snapshot expires, the page also waits for a user action. This bounds background work while someone leaves the offer open. Returning to the foreground, changing accounts, or confirming a transaction starts a fresh check.

A routine account verification can overlap the local session read. The page may continue to display the previously verified account until its deadline. A busy read cannot establish a new purchasing identity. A change of account generation, expiry, or a confirmed unavailable session invalidates the display. Showing the purchase button requires a fresh free-plan balance, and the tap checks freshness again before the existing StoreKit purchase flow runs.
