# Lite local capture discard and deletion recovery

This follows local `45f766b`. It is not included in the owner's TestFlight 38
(`fa827a5`). No deployment, remote cleanup or store delivery is part of this change.

## Problem and behavior

A measurement could be saved before its duplicate capture draft was finalized.
If the app stopped then, and the saved measurement was deleted before recovery,
the raw draft could appear again and recreate that same record. An unfinished
retake also had no explicit discard action.

`LiteStore` now persists a small encrypted retirement journal before removing a
record or accepting a draft discard. Each library has its own journal, containing
only IDs. The original measurement-index format is unchanged. Recovery, saving,
recognition and result application consult the journal; an open stale editor
cannot recreate the retired capture. A damaged journal fails closed and preserves
the original files instead of interpreting it as an empty set.

In the native candidate, history deletion waits for raw draft cleanup before
removing the saved measurement. Ordinary history deletion also saves the fence;
the next candidate recovery cleans any remaining raw draft. Failed cleanup keeps
the saved record visible and explains that deletion needs retry. The marker alone
does not silently complete a failed measurement-index deletion on next launch.

The capture screen provides explicit, confirmed discard for the current or a
pending capture. After verified cleanup the raw draft becomes a compact encrypted
receipt containing its ID and media hashes. Other captures, service receipts,
research consent and already used quota are unchanged. Neither discard nor local
record deletion calls a cloud deletion or withdrawal API.

## Evidence

The same regression body was run on an isolated `45f766b` snapshot and the fixed
source. The baseline successfully built and ran, then failed four assertions:
the deleted capture was listed, its image restored, its raw draft readable, and
the deleted ID could be re-added. The fixed source passes all four checks.

| Release configuration | Passed | Skipped | Failed |
| --- | ---: | ---: | ---: |
| Ordinary | 512 | 1 | 0 |
| Native candidate | 512 | 1 | 0 |
| Focused lifecycle/session suites | 60 | 0 | 0 |

Counts overlap and must not be added. Ten new regressions are included in each
full suite: five retirement-store tests and five capture/session tests. The skip
is the documented simulator limitation for physical file protection. Tests cover
failed writes, interrupted deletion, stale stores/editors, per-library isolation,
damaged journals, rollback of IDs already known to the running instance, cleanup
retry, lost acknowledgement, preserving other drafts and unchanged service receipts
and research consent. A synthetic completed service operation still has one run
after discard; this does not establish real billing behavior.

The initial focused run had a test compilation error using a nonexistent failure
case. Only the test fixture was corrected to the existing conflict case, followed
by the passing focused and full runs. Its log is retained separately, not counted
as a pass. Raw XCTest summaries, trees, baseline test body/hash, and the checker
are included in the review package.

## Limits and next gates

- Monotonic rollback detection is relative to IDs already read by the running
  journal instance. It does not establish anti-rollback across restored backups.
- The journal has no images or contours, but its IDs are retained without
  automatic compaction to prevent recreation. This is not an account-deletion
  completion receipt or a claim that every local or cloud copy was erased.
- Uncertain earlier index writes can leave allocated encrypted media copies;
  orphan reconciliation remains a separate task. See the following update for
  recovery of unaffected drafts when one file is unreadable. There is no
  automatic age-based draft purge.
- Already dispatched cloud work cannot be unsent. Its receipts remain for
  settlement/audit; new capture resumption is fenced after retirement.
- New UI, physical file protection, true service quota/readback and Apple Sandbox
  still need real-device acceptance. User confirmation of old records/PDF concerns
  TestFlight 38 only. No new raw research data was read or modified by this work.
- Independent, consent-bound research ingestion after personal recognition is
  still required without rerunning the model or charging another recognition.
- Public configuration must omit the saved-record internal acceptance entry.

### Mixed draft recovery

Opening the measurement screen checks the saved record index and the retirement
journal before listing unfinished captures. A change to the index from another
scene requires a fresh read. A directory-level read failure clears the list of
available drafts and prompts the user to retry. The current editor and on-disk
records are preserved.

Each encrypted capture or receipt is checked individually. An unreadable file
stays in place and contributes to the notice count. Other verified drafts can
be opened, outlined and saved. Recovery keeps only their IDs and dates in the
list. A committed draft is finalized after matching the stored JPEG and depth
bytes. A readable draft already marked for discard is retired as before.
Neither path opens a recognition job or enrolls the user in research.

## Review and rerun

Start with `LiteCaptureRetirement.swift`, then `LiteStore`, capture draft receipts,
the native deletion coordinator, and capture recovery/discard in `LiteMeasure`.
Run `LiteCaptureRetirementTests`, `LiteNativeSessionTests` and
`LiteStoreLifecycleTests`, followed by the full WoundLite Release suite generated
from `iOS/project.yml` and `iOS/project.lite-sandbox.yml`. The candidate build needs
its configured native origin; synthetic transports do not connect to that origin.
Use `tools/verify_xctest_results.py` on the actual summary and test tree. The
baseline negative control is intentionally failing and is checked separately.
