# First-party sponsorship placement candidate — 2026-11-14

This implements the previously agreed free-with-ads / paid-without-ads direction
in the native plans screen. It replaces neither service subscriptions nor research
consent. The earlier `LiteCommerceLab` remains a simulator-only demonstration;
this change is a reusable production presentation component, with **no approved
campaign shipped, no live advertising and no ad revenue**.

## What is implemented

- A single clearly labelled, dismissible sponsorship card in **Plus plans and
  pricing**. No placement in capture, annotation, history, research consent, PDF,
  notification, widget or essential workflow. There is no video, reward, countdown
  or requirement to view/click an ad to access the two free recognitions.
- Only a recently confirmed free service plan may display it. Plus, unknown/error,
  signed-out, closing or changed-account states suppress it. A StoreKit entitlement
  also suppresses it while a new purchase association catches up. Account/purchase
  state changes hide the previous result immediately; late responses cannot revive
  it. The snapshot expires in under 60 seconds without automatic refresh.
- A bundled `LiteSponsorCampaign.json` with strict bounded schema, dates, approved
  Apple storefront codes, advertiser, both language texts and one HTTPS URL. No
  network creative/image loader, advertising SDK, JavaScript, tracking pixel,
  impression/click upload, advertising identifier or cross-app matching exists.
  The reviewed bundle currently has **no such campaign file**.
- Query strings, fragments, user info, ports, encoded/ambiguous URLs, unexpected
  campaign keys, control characters, HTML and oversized content are rejected.
  A valid URL is not an endorsement or proof of advertiser behavior: every actual
  creative, destination and claim requires editorial/legal review before shipping.
- The user sees the selection basis (free plan, storefront, dates and language).
  No research participation, wound, image, depth, location, diagnosis, installation
  or account identifier reaches the campaign model or an advertiser.
- External navigation requires a separate confirmation showing the exact URL.
  The App adds no identifier or health data. The system browser/website can still
  exchange IP addresses/cookies or redirect; this is disclosed, not claimed to be
  anonymous browsing. Cancel opens nothing; stale eligibility cannot open a link.
- A report action prepares only public campaign ID, advertiser and project support
  address for the user's chosen share destination. It sends nothing automatically
  and explicitly asks not to include health data. A local hide action stores only
  one campaign ID in an independent protected, backup-excluded journal. Corrupt
  state suppresses the ad; failed hide persistence still hides the current card.
- Traditional Chinese and English copy are included. A missing/invalid campaign
  performs zero plan/StoreKit lookups and creates no dismissal file.

## Costs, measurement and activation

Absent a campaign, incremental advertising network traffic/revenue is zero.
When activated, an eligible plans-page state check can perform an existing service
balance lookup and StoreKit entitlement lookup. Those are **not ad-network calls**,
but they are not assumed cost-free. Rendering uses bundled text only. A confirmed
snapshot is not refreshed every second (the UI timer only hides expired data).
There are no billable impression counters; repeated onAppear is not called a
verified ad impression. No revenue or break-even claim is supported by these tests. A plans-only placement
has limited reach; no assumption is made that every daily active user will see it.

For initial direct sponsorship, a fixed term/placement agreement is simpler than
selling unverified CPM impressions. Its fee and expected traffic still require a
real sponsor; all forecasts must retain advertising income = 0 until then. If a
network SDK or measured impression billing is later chosen, it requires separate
consent/SDK privacy review, delivery measurement, failure/cap tests and cost inputs.
This component does not substitute a token experiment for that commercial work.

Before a live campaign: approve advertiser/claims/market/age suitability and fee;
review the landing site, privacy disclosures and reporting process; update and
publish the policy and Apple metadata, disclose the ad feature in review notes;
ship an explicitly reviewed campaign in an authorized build. First owner tests
continue without real advertising. No secret remote enable switch is included.

## Verification scope

Baseline: `d044614d27390ed5331269552ad81f48e1be8ae3` (local reminder candidate).
Evidence directory: `woundailite-sponsorship-20261114` in the local Codex workspace.

- 12 targeted XCTest cases passed, covering absent config, Plus/unknown/expired
  suppression, invalid URLs/payloads, late replies, concurrency, persistent hide,
  storage failure, generic report data and language resources.
- Final ordinary and Native Sandbox Release suites each passed 684 tests, with
  zero failures and one existing simulator file-protection skip. These overlap;
  the 12 new sponsorship cases are included, not an additional 12.
- Three isolated UI cases passed: English/Traditional Chinese label, exact URL,
  cancel with zero navigation, explicit confirmation with one intercepted open,
  hide/relaunch, and paid/unavailable suppression. The host uses the production
  card/model, synthetic eligibility and intercepted navigation; no live website,
  real service plan or Apple subscription is tested here.
- Initial candidate UI runs exposed inherited accessibility identifiers and a
  popover confirmation that omitted the cancel action and second message text.
  The final card has individual identifiers, readable leading alignment and an
  alert containing the complete URL plus explicit Cancel. Both earlier failures
  and all final raw XCTest bundles are retained. This was development testing,
  not evidence of a regression in the published app.
- 375 build-input hashes match the source and both QA copies (the isolated UI
  host changes only the App entry point). Final Chinese/English card and dialog
  screenshots were checked for readable labels, URL, cancellation and layout.
- Existing TestFlight 46, research consent, records, media and cloud services are
  untouched. This candidate has not been pushed, deployed or delivered to Apple.

## Primary references, checked 2026-11-14

[Apple App Review Guidelines 2.5.18 and 5.1.2–5.1.3](https://developer.apple.com/app-store/review/guidelines/)
require ad transparency/reporting and restrict sensitive health/research/depth data
in advertising. [Apple privacy and data use](https://developer.apple.com/app-store/user-privacy-and-data-use/)
explains cross-company tracking and SDK disclosure. This design avoids such
tracking; a future SDK integration would need a fresh assessment. A local test or
advertising disclaimer is not a regulatory clearance or App Review approval.
