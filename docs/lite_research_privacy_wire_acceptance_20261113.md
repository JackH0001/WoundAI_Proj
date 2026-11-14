# Native research withdrawal and rejoin wire acceptance — 2026-11-13

This extends the opt-in Swift/Python HTTP harness. Product App and backend
sources are unchanged. The earlier native research test covered capture receipt
recovery and annotation revision order; withdrawal/rejoin previously had separate
Swift scripted-transport and Python HTTP tests. This scenario joins those paths
through the real `LiteWithdrawalModel`, persisted JSON journal with iOS file protection,
`LiteSignedCloudTransport`, `LiteAttestClient`, research delivery and two composed
backend routers, with synthetic device proofs and storage.

Final local result: **six Release native tests and ten fixture tests passed**,
zero failures and zero skips. All 368 copied App/harness source hashes are
recorded for exact-source verification. The new scenario produced two model
calls, two debits and two attempts; two rejoin requests returned the same epoch
and audit generation. This is local evidence, not a CI or provider result.

## Acceptance cases

| Action | Required observation |
|---|---|
| Save and upload one synthetic RGB-D capture | Research installation is distinct from the personal-service installation |
| Withdraw through the native model | Upload consent stops; legacy media markers are sealed with zero payload bytes; local record index and depth remain unchanged |
| Recognize another image while research is withdrawn | Personal recognition succeeds; total model calls, debits and attempts are two; no research scope is created |
| Rejoin succeeds on the server but its reply is lost | Local withdrawal remains blocking; the exact saved renewal intent is retained |
| Recreate the native withdrawal model and retry | The same epoch, state generation and audit hash are returned; no extra consent event or installation is created |
| Attempt old local capture/revision retry after rejoin | Journal fence prevents any HTTP request |
| Save a new capture to the same wound | Only the new consent epoch receives the new manual RGB-D; no third model invocation/debit |
| An old revision arrives after the new capture | Backend returns 409; neither epoch's stored state/payload hashes change |
| A delayed old withdrawal arrives after rejoin | Repeated legacy DELETE is idempotent and leaves the new epoch unchanged |

The new consent flag in the test is an explicit synthetic action after confirmed
renewal. It is never enabled by purchase/restore. Research withdrawal does not
imply account deletion or reset service quota. The local old photo, depth bits,
polygon and wound timeline survive. Old local data is not silently re-enrolled.

## Harness corrections

The first run exposed two test-fixture assumptions, not product regressions:

* Ledger rows with the same timestamp can be enumerated in identifier order.
  Readback now sorts all rows by image/revision for comparison, without dropping
  duplicates. Separate HTTP event observations assert revision requests occur as
  `1, 1, 2`, and persisted rows still must be exactly revisions `1, 2`.
* The second capture must select the existing wound. Creating another free wound
  correctly fails the production tracking limit; the fixture now retains the
  same wound instead of changing the limit.

A Werkzeug test client also omitted an empty DELETE's content length; its fixture
request now sets the WSGI length explicitly. The loopback server still rejects
unbounded/nonempty privacy DELETE requests. Failed logs remain in local evidence.

## Reproduction and boundaries

Use `lite_native_wire_acceptance_20261113.md` commands. The updated runner requires
six native Release tests, zero failures/skips and six backend scenario snapshots.
The fixture suite has ten tests. Counts include previous scenarios and must not
be added to historical totals as unique tests. Full App suites and CI are separate
evidence, not implied by a local harness result.

Only the isolated simulator App and temporary synthetic stores are used. The
real Swift request/receipt serialization runs; a synthetic challenge/device proof
adapter and loopback middleware supply test App Attest signatures to the ordinary
backend verifier. Real Apple App Attest, Apple Sandbox, TLS, GCS/IAM, backup
destruction, KMS retirement and physical depth accuracy are not covered. Sealed
zero-byte live media is not proof that all historical copies have been destroyed.
`research_unchanged` in the observation file describes the personal backend's
separate privacy store, while `privacy_readbacks` describes the research store.

Original local evidence is under `woundailite-research-privacy-wire-20261113/` in
the review workspace: xcresult, request hashes/counters, persisted privacy
snapshots, source hashes and fixture logs. The Claude package contains a patch,
SHA-256 manifest and offline verifier. No real consent, records, cloud resources,
deployment, App Store update or new research enrollment is performed.
