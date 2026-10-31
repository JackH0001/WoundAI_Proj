# Account deletion: durable closure of linked research admission

Parent: `237f3dfa49fd52ab957eaebdde47954d14cac565`. This change is local and
opt-in. It does not deploy a worker, execute real withdrawal/cleanup, delete
media/keys, update a store build, or claim complete account deletion.

## Gap and behavior

Account closure already fences service login, subscription use and new model
operations. Independent Lite research participation lives under an App Attest
installation and its consent epochs. Closing the service account alone did not
stop those research endpoints or a later explicit rejoin. The same offline probe
now reproduces that gap on the parent and checks the candidate.

`AccountResearchClosure` traverses the account's frozen grant inventory, at most
five grants per invocation, using a durable cursor. Each association must match
the existing registration, account identity, account token and original grant
membership receipt. It never treats an email or similar-looking anonymous ID as
an association. Unknown installation formats and unregistered reservations remain
unresolved. Counts are **grant entries**, not unique installations or patients;
multiple logins retire the same installation idempotently.

Before mutation the worker requires the original account-closure audit and saves
its own intent. It then retires the linked research installation's active consent
pointer and closes the current epoch's write admission. Progress/checkpoints and
node receipts are immutable-audit-bound and resumable after restart or a lost
reply. Status reads do not advance the worker. Missing evidence fails closed;
corrupt state is not reset. No public route can invoke the worker or retirement.

## Rejoin/closure ordering

`Reconsent` continues to read existing v1 records. Its new retired v2 record
points to a content-addressed retirement audit containing the original active
pointer, closure hash and verified association hash. Original consent events,
previous-epoch links and disclosure versions remain unchanged and readable.
The existing account-data inventory now records the retirement proof's exact
generation/hash too; its later drift invalidates that inventory observation.

Retirement and rejoin update the **same CAS object**:

- Rejoin wins first: retirement retries against the new generation and includes
  that newly accepted epoch. It cannot leave the new epoch open.
- Retirement wins first: the delayed rejoin CAS fails; a retry sees the terminal
  state and cannot create another epoch or restore participation.
- Retirement response lost: read back the identical proof/pointer before success.
- Retirement saved but current-epoch fence temporarily unavailable: new HTTP
  research/rejoin requests are already denied; the same worker resumes the fence.
- Ordinary independent research withdrawal remains reversible through the
  existing explicit future-only reconsent path when **no account deletion** is
  associated. Account closure is not silently cancelled by research consent.
- Signed DELETE of the original epoch remains available after retirement; the
  existing ownership guard still applies. Research admission/rejoin uses the
  existing conflict response. Distinct account-deletion UI treatment is still a
  composition/physical-device acceptance gate, not added by this backend change.

Older readers reject the v2 state rather than reopening it. Before activation,
all participating research writers must use the reviewed admission path. Rollback
means stopping worker advancement and keeping terminal records, not downgrading
records, reopening an account, or inventing a fresh anonymous identity.

## Deliberate data boundary and remaining work

This component does **not** call `seal_pending`, `complete_withdrawal`, delete
objects, scan buckets, decrypt tokens, invoke Apple/KMS, run the model or change
quota. Original RGB, depth, confidence, metadata, labels and historical versions
remain byte-for-byte unchanged in the synthetic tests. It closes admission,
not media erasure. Existing admitted write plans remain for the separate sealing
executor. A generation-pinned write admitted before the fence can still land;
the saved plan must later be sealed. This case is explicitly tested and must not
be described as completed writer drain or data deletion.

Every result retains `account_deleted=false`, `media_disposition=not_started`,
`historical_epochs_verified=false`. Finishing the grant scan is not finishing
account deletion. Unresolved associations require an explicit reconciliation;
this version does not guess links or automatically rescan a completed unresolved
inventory after a late registration. A separately reviewed reconciliation is
needed before any whole-account completion claim.

The new worker is not attached to `NativeBackend`, `DispositionProgress`, legacy
startup, a cloud scheduler or the candidate image. It is an internal component
for the forthcoming reviewed private cleanup composition. No schema claims
replaces the public v2 deletion receipt's existing pending-lane list.

Remaining release gates: full epoch/media inventory and generation-aware sealing;
historical/soft-delete/backup handling; association migration for old installations;
retention basis and minimized identity/subscription/audit data; approved private
worker composition/IAM/scheduler with failure recovery; actual Apple Sandbox and
physical-device acceptance. Until those are resolved, this is not App Store
account-deletion compliance or a complete erasure claim.

## Reproducible verification

```text
python -m unittest -v test_lite_account_research_closure
python tools/lite_account_research_closure_probe.py --output after.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

The 37 new cases cover real SQLite CAS, multi-batch restart, two workers,
retirement/rejoin races, lost replies, missing/tampered audit, foreign/unlinked
installations, original-media preservation, old admitted write plans, and signed
App Attest HTTP. Six cases use the real HTTP guard with synthetic App Attest
cryptography. None reaches live GCS, Apple, KMS or a user's device.

Run the same probe file with the parent's `Backend/Flask`, `tools` and
`engineering/phase2` on PYTHONPATH. Before: closed-account legacy/renewed research
admission and closed-account rejoin remain possible. After invoking the explicit
worker: all three are denied; ordinary withdrawal/rejoin is unchanged. Original
six-object synthetic RGB-D/label bundles and history remain identical. Provider
and model calls during the closure worker/probe observation are zero. Account
closure authentication itself is performed before that measurement boundary.

Claude priorities: verify the CAS winner cases; confirm current/history reads are
not confused with erasure; check exact verified association and duplicate-login
semantics; verify old admitted writes remain an explicit pending obligation;
confirm no default activation, new public route or cloud deletion is introduced.
