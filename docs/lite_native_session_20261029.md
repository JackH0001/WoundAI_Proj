# WoundAILite native Sandbox composition — local candidate

## Scope and before/after

Previously the identity, purchase, service-result and deletion components had
independent injection points. There was no single candidate composition in the
settings/history flow. `LiteNativeSession` now supplies one existing App Attest
installation/origin, shared identity/deletion state, and separate purchase and
recognition journals. `LiteRootView(nativeSession:)` exposes the candidate in
settings and in an existing saved record. Ordinary `WoundLiteApp` still calls
`LiteRootView()` with no candidate; shipping startup is unchanged.

The factory `LiteNativeSession.sandbox(client:anchor:directory:)` uses the real
App Attest, native Apple authorization, StoreKit and protected file-journal
adapters. Construction sends nothing. Device registration is a separate explicit
button; ordinary request/retry paths never silently register a new installation.
The factory is a composition seam, not a deployed backend or an installed build.

## Existing record flow

1. Open an existing record; opening the candidate page reads its local service
   journal only. A missing row differs from a corrupt/unreadable existing row.
2. Explicitly send that saved JPEG. Its UUID is the stable capture/initial
   operation ID. Pin the exact JPEG hash before any request. A changed JPEG under
   the same ID fails rather than creating a new operation or replacing evidence.
3. Show the service result separately. Original area, annotation, depth, record
   date and research receipt are not rewritten. The result is not research consent
   or proof of research storage, and no new area is calculated here.
4. On a lost run response, query the same work. Completed local results require
   no network. Reservation expiry uses the existing explicit resubmit component;
   simply viewing/retrying cannot manufacture a successor operation.

This does not yet replace the normal camera recognition route, automatically
apply a new mask, or enable paid service for installed users. Candidate JPEG
recognition deliberately does not send depth to the service endpoint. Existing
depth/research paths remain separate and unchanged.

## Identity and request boundaries

- All candidate HTTP paths are exact POST allowlists with a canonical HTTPS
  origin; query override, foreign URL, response URL mismatch and research routes
  are rejected. Installation and permission are checked before and after sending.
- Ordinary service/purchase requests require the same identity store. The real
  App Attest adapter checks again after assertion preparation and before dispatch.
  Logout/deletion progress remains callable when ordinary service is fenced.
- Identity and deletion intentionally share the same protected journal namespace.
  A saved deletion intent therefore also blocks a reconstructed session. It never
  claims the account or data has been deleted merely because a request is accepted.
- Purchase and recognition namespaces are independent; every namespace includes
  origin. Changing service origin/installation does not adopt old saved identity.
- Server-side installation-to-account ownership remains mandatory. The client
  gate is not a replacement for `lite_account_identity.py`'s durable binding;
  different users cannot implicitly switch the same installation's association.
- Sandbox context is required before invoking StoreKit. A lost purchase-confirm
  response reconciles the original unfinished transaction instead of buying again.
- Research consent/preferences are not used to authorize this service candidate
  and are not modified by login, purchase, restore or recognition.

## Local evidence and limits

`LiteNativeSessionTests` has 20 integration cases using the real HTTP clients and
protected file adapter with synthetic Apple/StoreKit/HTTP responses. It covers
explicit enrollment, disabled default entry, same-account purchase/restore,
response loss/restart, logout races, deletion fencing, cross-origin/installation,
disk failure, unchanged original files, changed JPEG rejection, corrupt journal
handling, and namespace persistence. Recognition replies use the existing
Python-generated `lite_service_operation.json` contract fixture.

Passing these tests counts dispatches, not real model executions or billed quota.
Neither Apple cryptographic verification, real App Attest signing, GCS provider
composition nor S0–S9 Sandbox acceptance is simulated into a claim of completion.
The fixture backend does not prove production account ownership; its source-side
binding and quota suites remain separate evidence.

Local Release results: 20/20 new integration tests, and the full Lite suite
444 passed / 0 failed / 1 existing simulator file-protection skip. The focused
20 are included in 444, not added to it. Structured XCTest summary and tree were
checked with `tools/verify_xctest_results.py`; actual iOS file-protection remains
a device check. Backend/native contract, purchase and account-identity tests:
55/55 passed, including the signed Flask fixture matching the native fixture.
These are local results, not GitHub CI or actual Apple Sandbox results.

## Next acceptance gates

1. Complete and validate the opt-in backend composition/build profile, source and
   signing configuration, including account deletion disposition and key custody.
2. Review a concrete fixed-source Sandbox deployment/build plan. No new resource,
   actual key/data destruction, subscription product, purchase or store update is
   authorized by this local change.
3. Verify entitlement/profile/audience and a limited installed candidate, then
   execute [Apple Sandbox S0–S9](lite_apple_sandbox_acceptance_20261027.md).
4. Confirm the preserved-record flow on device before enabling normal capture or
   publishing. Keep synthetic HTTP replay, local StoreKit and actual Apple
   Sandbox results separately labelled.

PR36 stays draft; no merge, deployment, store update or original data deletion.
