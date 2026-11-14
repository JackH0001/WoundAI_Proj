# Request-scoped native admission — 2026-11-13

## Problem and final behavior

The previous native scope transport called `canUseService()` both before sending
a protected request and after receiving its response. Each call made an
`account/status` HTTP request. The native wire baseline measured 41 status calls
for the free/Plus/restore scenario and nine for the lost-result/renewal scenario.

The old post-response check also accepted whichever login was active at that
moment. A deterministic regression logs out and signs back into the same account
while the response is in flight: the old code incorrectly accepts the response
from the earlier login. Two new tests against unchanged `ffad88e` reproduce both
the duplicate checks and this stale-response acceptance.

The revised transport:

1. Keeps a fresh remote admission check before **every** protected request,
   including automatic renewal when required. Nothing is cached across requests.
2. Captures the local identity generation and complete session for this request.
3. After the response, checks the same origin and installation, matching session
   and identity generation, local deletion journal, expiry and Apple credential
   state. A logout/relogin, account/installation change or deletion rejects the
   response instead of accepting a newly active session.
4. Does not issue a second `account/status` after the response. The protected
   backend route still independently calls `identity.require_session()` and
   checks lifecycle, grant, account mapping, expiry and logout evidence.
5. Keeps account/deletion routes outside ordinary admission, so a stopped service
   does not prevent signing out or following an existing deletion request.

This is not an authorization cache or an indefinite revocation grace period.
Remote state can change after any response/check; there is no atomic global
"still active forever" guarantee. A new request always rechecks. If the backend
revokes the session between preflight and dispatch, the protected route returns
401 and the client does not run inference or debit usage.

## Regression scope

Eight new XCTest cases cover one fresh check per request, unavailable preflight,
logout/relogin during a response, deletion during a response, revoked Apple
credential, changed installation, backend rejection and an outage after an
already-authorized response. Existing identity, purchase, capture, research and
file-preservation tests remain in the full Release suite.

The opt-in native wire harness adds a third scenario: server-side logout after a
successful client preflight but before the prepare request is authorized. Its
expected outcome is one 401, zero model calls, zero usage and no research change.
The existing lost-result/renewal scenario must still execute/debit exactly once.

The local verification report records the actual before/after request counts,
Xcode result bundles, the existing single simulator protection skip and the
selected backend/fixture test log. Counts from overlapping suites are not added.

## Limits and delivery

The wire fixture uses real Swift request serialization and loopback HTTP into
the Python router; Apple, App Attest origin, KMS and inference are synthetic.
It is not Apple Sandbox, device capture, cloud latency/cost or Cloud Run IAM
acceptance. No backend authorization, quota policy, research consent, saved
record, model, source identity or subscription price is changed by this fix.

No merge, deployment or TestFlight update is included. The local review package
is under `woundailite-request-admission-20261113/` in the existing review workspace;
the fixed commit and hashes are in its verification report. Actual native
initialization still needs owner operational custody and separately authorized
cloud execution.

## Recorded local results

- Ordinary and owner-candidate Release: each **654 passed, zero failed, one existing simulator file-protection skip**. The eight new native-session regressions are included.
- Actual Swift/Python loopback: **three passed, zero failed/skipped**; selected backend plus fixture suite: **41 passed**. These are different selections; do not present sums as unique coverage.
- Free/Plus/restore scenario: status HTTP **41 → 25**, total observed business/account requests **60 → 44**; model calls and debits remain **3 / 3**.
- Lost response, process reconstruction and expired-session renewal: status HTTP **9 → 6**, total **16 → 13**; model calls and debits remain **1 / 1**, with one automatic renewal.
- Server revocation between preflight and prepare: **401**, zero inference, attempts or usage; synthetic original and research state unchanged.
- Baseline on unchanged `ffad88e`: both targeted regressions fail, demonstrating duplicate status requests and an old response crossing logout/relogin.
- The first expanded loopback run had one test-only counter expectation error (expected five status reads; actual six). A deliberately lost response never reached the old post-response check, so only three reads are removed in that scenario. The assertion was corrected to six; the three-scenario rerun passed. Both result bundles are retained.

Request counts exclude synthetic fixture-control calls and live App Attest challenge/signing traffic. They do not establish cloud latency, server GET savings, Apple Sandbox success or a monetary cost reduction. No real records, provider credentials or deployments were used.
