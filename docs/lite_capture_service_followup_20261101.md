# Lite candidate capture, personal recognition and local recovery

Scope: local candidate implementation after `dc84e52612bc06410860029f430f6f12443fe156`.
The last published integration source is `53ed92b668ac69f6a7b96229d2fd02c062b2a47b`.
This change is not in the owner's TestFlight 1.0 (38), which remains the already
delivered `fa827a53246f43d321db4a08ed624ec4e149c7a8` artifact.

## Resulting behavior

The opt-in native candidate can now use the personal recognition service from
the capture screen. Previously only an already saved record had an internal
service acceptance entry. The ordinary configuration keeps its existing
recognition path; it does not construct the candidate session.

Before any candidate recognition request, a stable capture ID, normalized JPEG,
lossless depth sidecar and contour provenance are written and read back from an
encrypted, backup-excluded local draft. The first cloud request still requires
an explicit button. Research consent is unchanged. Retaking keeps an unfinished
draft available for recovery; cancelling the camera keeps the existing screen.

Recognition uses the existing durable service operation. A lost run reply is
recovered by status lookup, not by creating another operation. Foreground
continuation obeys the saved retry deadline, stops on a completed result and
does not initiate a first request or an explicit expiry successor. Result use
requires the exact JPEG hash, capture identity, origin and dimensions.

Manual edits are retained when a cloud answer arrives. Replacing them requires
confirmation and uses the saved answer without another model request. An
unchanged contour completion does not create a draft revision. These guarantees
are separate from server-side quota settlement and optical measurement accuracy.

The capture screen and tests now share the same measurement save path. It writes
the image, depth and index before clearing the screen or navigating to history.
An index write that reports failure after actually committing is checked against
the exact newly allocated media pair. An unconfirmed save keeps the capture
screen and draft. A confirmed save replaces the duplicate draft media with a
small encrypted receipt; recovery also reconciles an interrupted finalization
against the saved measurement's exact JPEG and depth.

## Local validation

Both Release configurations completed on the same iOS Simulator:

| Configuration | Passed | Skipped | Failed |
| --- | ---: | ---: | ---: |
| Ordinary WoundLite | 502 | 1 | 0 |
| Opt-in native candidate | 502 | 1 | 0 |

The same tests run twice; do not add the totals as unique tests. There are 17 new
regression cases, included in each total. The single documented skip is the
physical file-protection class check that this simulator cannot provide.
`tools/verify_xctest_results.py` verified the actual summary and test tree.

New coverage includes exact RGB-D persistence, disk errors before transmission,
ambiguous completed writes, restart, lost responses, manual contour preservation,
retakes, stale edits, corrupt service receipts, unreadable measurement indexes,
saved-result reapplication, retry deadlines, repeated saves and interrupted
draft finalization. The synthetic HTTP recorder observes one prepare and one run
after retry/restart, with a status lookup after a lost run reply. This is a client
dispatch assertion, not a live model-count or billing measurement.

The first full candidate run failed three assertions in one new test because its
injected writer was still configured to fail every write when the test attempted
a subsequent normal deletion. The deletion phase now opens the same index using
the normal writer. The failed result is retained in local evidence, followed by
the passing complete rerun. No failed result is counted as a pass.

## Review and rerun

Review `LiteCaptureDraft.swift`, the capture methods in `LiteMeasure.swift`, and
the 17 new `LiteNativeSessionTests`. Review the earlier `dc84e52` RGB-D binding
change before this one if starting from the published `53ed92b` source.

Generate the ordinary Xcode project using `iOS/project.yml`, and the native
project using `iOS/project.lite-sandbox.yml`. Run the WoundLite Release
tests on a supported simulator with testability enabled. The candidate project
requires its configured native origin. Network fixtures use synthetic transport;
they do not contact that origin or Apple.

The local handoff includes both patches, changed source, raw XCTest summaries and
test trees, the result checker and SHA-256 manifest. It contains no patient media,
real depth samples, credentials or private model weights.

## Remaining boundaries

- The owner reported that TestFlight 38 installed over the existing app, retained
  records and exported PDF correctly. That report validates build 38, not this
  candidate change or native service connectivity.
- No candidate backend was deployed, no cloud data was changed, and no purchase,
  research upload, physical camera acceptance or new TestFlight delivery ran here.
- Candidate personal recognition has no research ingestion binding yet. Final
  RGB-D and annotations must later be connected to independently consented
  research ingestion without a second inference charge.
- Real device file protection, actual Apple service authentication, service quota
  settlement and full camera/UI acceptance remain required.
- Drafts remain when abandoned or a save is uncertain. Draft retention and an
  explicit discard flow still need implementation before public rollout. A
  committed measurement deleted before an interrupted draft finalization is
  reconciled also needs a durable deletion-tombstone integration; do not claim
  that deletion race is covered by the current normal-deletion test.
- An unreadable draft stops listing rather than silently pretending the library
  is empty. Per-item recovery and orphan media reconciliation remain follow-up.
- The saved-record internal acceptance entry is still internal candidate UI.
  Public release must hide it and use the normal capture/service workflow.
- These local commits have not been pushed and have no new remote CI result.
  Existing fixed-source push authorization does not include them.
