# Two-installation native wound tracking acceptance — 2026-11-13

This extends the opt-in Swift/Flask loopback harness. Product code is unchanged
from `089fbb138cb734f6103331994d793284091e55bb`. Previously the two implementations
had separate wound-roster tests. This change exercises the actual Swift session,
wound clients, local record store and timeline selection against the composed
Python HTTP routes, using two independently registered synthetic attestation keys.
The account resolver is not patched: both logins resolve the same synthetic Apple
subject to one service account and subscription token.

## Observed results

Four Release integration tests passed, with zero failures or skips; five Python
fixture boundary tests also passed. These focused counts overlap earlier wire
scenarios and are not added to the full iOS suite or CI totals.

| Transition | Assertion / observed result |
|---|---|
| Two installations use free recognition | Two shared uses, daily limit two; independent signing keys and installation IDs |
| Free wound tracking | First active wound accepted; second refused with limit one |
| Buy Plus on one installation | Other installation sees daily total ten with the original two uses retained |
| Four active wounds, both clients read the same roster before opening the fifth | Exactly one open succeeds; the other receives the revision conflict; five active wounds, revision five |
| End a wound from the other installation | Refresh marks its local group ended; stale client cannot implicitly reopen it |
| Open a replacement | Five active wounds, revision seven; ended timeline still contains the original record |
| Synthetic subscription refund | All five wound IDs remain, but only one is selectable; another active group cannot obtain a free tracking permit |
| Restore synthetic active entitlement | Same five wound IDs are usable again; daily usage remains two |
| Reopen the local record store | Every field except the expected `woundEnded` refresh remains byte-equivalent after deterministic encoding |

The new scenario ends with exactly two model calls, two successful debits and two
attempts. There is one synthetic purchase call on the first client and none on
the second. The research privacy store and original synthetic JPEG are unchanged.
97 HTTP requests include 58 account-status reads across the deliberately long
scenario; this is a measured count, not cloud latency or a cost estimate.

The three existing scenarios also pass: free/Plus/repeated restore (3 model calls
and 3 debits), lost result response followed by process restart and renewal (1/1),
and server revocation between preflight and dispatch (0/0, rejected with 401).
Each scenario starts with an isolated synthetic store; their counts must not be
presented as one user's daily allowance.

## Fixture corrections and limits

The initial second installation reused the first test authorization-code serial;
the fixture now uses a separate serial. A negative assertion was corrected to
expect the router's actual 403 for an installation/body mismatch. Neither change
alters production admission rules. The first complete native run passed the
roster transitions but failed the final balance freshness check: only the server
clock had advanced by 62 seconds. Both synthetic clocks now advance together;
the existing 60-second future-timestamp guard is unchanged. Failed evidence is
retained alongside the successful run.

The native sessions run in one isolated simulator QA host, not two physical
phones. The synthetic HTTP server serializes requests; a test barrier makes both
clients obtain the same pre-open roster before the two mutations. This proves
stale-revision admission across native clients, not real multi-replica scheduling.
Separate two-process quota/closure tests remain separate evidence.

Apple identity, StoreKit, device proofs, KMS and inference are synthetic. The
normal signature verifiers and account resolver run against synthetic trust
roots. Real App Attest, Apple Sandbox, TLS, Cloud Run, GCS and provider permissions
remain unverified here. Record image/depth filenames are synthetic metadata:
this test does not claim to read or validate real historical media files, render
the PDF, or synchronize image histories between devices. No real keys, photos,
research consent or original TestFlight records are accessed.

## Reproduction and evidence

Run the commands in `lite_native_wire_acceptance_20261113.md`; the runner now
required exactly four passing native tests and four backend snapshots for this
run. It copies tracked application sources to a separate QA project and records
every source hash. The subsequent RGB-D extension now requires five native
tests and eight fixture tests; see `lite_research_wire_acceptance_20261113.md`.

Local evidence is in `woundailite-wound-wire-20261113/`: `attempt1` preserves the
clock failure; `attempt2` contains original xcresult, JSON test tree/summary,
source binding and body-hash-only HTTP observations. The review package includes
a verifier and the exact test sources. Full cloud/Sandbox acceptance still needs
owner-created encrypted operational materials and a separately authorized,
fixed-source initialization/deployment plan.
