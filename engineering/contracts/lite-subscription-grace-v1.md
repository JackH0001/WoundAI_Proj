# Lite subscription billing grace — local implementation, not activation

Date: 2026-10-26. Baseline: `07f4fab`. This resolves the **signed-grace portion**
of G4. Policy/product-link migration and real Apple Sandbox remain open gates.
No Apple setting, cloud service, account, quota row or deployed image is changed
by this contract. Commercial service remains opt-in/default-off.

## Decision and evidence

A current Server API status 4 alone is insufficient. Use the same response's
`signedTransactionInfo` and `signedRenewalInfo`. Verify both using the pinned
official Apple library, then bind renewal environment, original transaction and
current product to the verified transaction and server account. The SDK renewal
verifier checks signature/environment, not all application owner constraints;
those remain our responsibility. See [Apple's verifier v3.1.2](https://github.com/apple/app-store-server-library-python/blob/v3.1.2/appstoreserverlibrary/signed_data_verifier.py).

Renewal `appAccountToken` is optional. If present, it must match. Absence is
permitted only because the verified **transaction** must always carry the exact
server-bound token. No client-selected owner, original transaction or paid flag
is used. Validate strict raw JSON types and duplicate keys *after* signature
verification to avoid model coercion. Required renewal fields: string original
transaction/product/environment; integer grace expiry/signed date. Optional
token/future product are strings, retry flag a Boolean, auto-renew status 0 or 1.

Status 4 must be consistent with an expired, unrevoked transaction and a signed
grace deadline still in the future. Renewal signing cannot precede transaction
expiry or exceed the current clock by more than the existing 60-second skew
tolerance. Explicit `isInBillingRetryPeriod=false` is contradictory. Missing
proof or contradictory/expired status requests a fresh check (503); it is **not**
interpreted as a free tier. Invalid signatures/claims fail closed. Fresh
status 2/3/5 retains the existing free floor without resetting usage.

For valid grace: `limit=10`, grant expiry is
`min(floor(gracePeriodExpiresDate / 1000), verified_at + 30)` and must be strictly
future. The total status+verification deadline remains 30 seconds. No 3/16/28-day
duration is invented locally. Apple says grace retains paid access and existing
grace users retain service even after the setting is turned off; changing the
next renewal (`autoRenewStatus`, `autoRenewProductId`) does not itself terminate
the current signed grace. See [Apple's grace setup/testing guidance](https://developer.apple.com/help/app-store-connect/manage-subscriptions/enable-billing-grace-period-for-auto-renewable-subscriptions/)
and [gracePeriodExpiresDate](https://developer.apple.com/documentation/appstoreserverapi/graceperiodexpiresdate).

Grace evidence is immutable `apple-status-query` **v2**, including both original
signed payloads, `entitlement_basis=billing-grace`, and `grace_expires_ms`. Existing
active/expired/refund evidence remains v1, byte semantics unchanged. Proof keys
remain content-addressed; ownership and audit originals are not overwritten.
A newly signed Apple observation may create a new proof; this does not mean a
new model operation or quota charge. Retention remains a separate rollout gate.

## Service behavior

- One current Apple status query per new paid operation; renewal verification
  is local and uses the same response, not another status call.
- Completed-operation retries use the retained receipt, no Apple call, no new
  inference or quota settlement. Apple outage does not erase readable receipts.
- Active → grace → expired/refunded → active preserves all used/pending counts.
  A validly reserved in-flight operation keeps its original grant and executes
  once. It is not silently re-admitted or reset during a status change.
- Service account binding, research consent, research withdrawal and data stores
  remain separate. Paid grace is not consent and cannot reverse withdrawal.
- `DailyGrant` and service HTTP success shapes are unchanged; iOS/Android source
  was not changed or rebuilt in this stage. No new public error UI is claimed.

## Link identity and rollback boundary

`ApplePolicy` still has precisely bundle/app_id/product/environment. The existing
policy hash and subscription link keys/acknowledgments remain unchanged. Do not
put a grace setting or implementation version into this identity. Regression
tests freeze the old identity dictionary and key prefix independently.

This is **not** a policy migration implementation. Before changing product,
bundle, app ID or environment, require a reviewed stable identity and explicit
bounded legacy mapping, immutable migration intent/completion, conflict checks,
lost-ack recovery and a compatible reader. Unknown legacy policies cannot be
treated as unbound/free. Sandbox and Production must never share entitlement or
owner state. Such a change remains blocked; no scanning or automatic migration
is introduced here.

No storage migration is required for this grace patch. Rolling back to the old
authority preserves records but again makes valid-grace new requests unavailable;
that is **not a fully functional rollback**. Any live rollout must keep current
receipt/binding readers, stop new admissions if necessary and retain all data.
Do not disable subscription links or manufacture a free tier as a rollback.

## Validation and real rollout gates

New 34 tests: 24 authority, 7 authenticated HTTP flow, 3 probe assertions. Real
synthetic EC chains/JWS go through Apple's official verifier; certificate roots
and Server API responses are synthetic, online OCSP disabled only in tests.
Production factory still requires explicit roots and enables online checks.

Coverage includes missing/forged/duplicate/type-coerced claims, wrong owner and
product/environment, expiry/deadline crossing, status contradictions, auto-renew
off, optional renewal token, immutable proof lost acknowledgment, same-account
limit preservation, current refund/retry status, old receipt replay during an
outage, and research withdrawal independence. No real purchase is simulated as
an observed Apple Sandbox result.

Before live use: independently review this source; complete policy migration,
verified account enrollment/restore and native purchase flow; configure/test
grace **in Apple Sandbox first** with owner authorization; verify transitions
and online certificate checks; then propose fixed-source backend/App cutover.
Do not enable billing, alter StoreKit/App Store settings or deploy from passing
local tests alone.
