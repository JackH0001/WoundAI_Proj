# Claude PR #37 a3a3bed follow-up — 2026-11-07

Status: local candidate only. Based on a3a3bed7330aae13e08ce133880938a488bf557b. No merge, push, cloud mutation, key deletion, TestFlight delivery or public release in this round. The November 7 Windows report/probes were not attached to this Mac; this work addresses the findings pasted by Jack and uses new local regressions, not a claimed replay of Claude's missing probe package.

## Findings and disposition

| Finding | Local result | Remaining release gate |
|---|---|---|
| Student raw scores treated as probabilities | Reused the already isolated main-based a68eaa960700bee9442b7d37ce756d29535ec078 (parent main 4b398498ce9d372f441d7e8319a3c5296dfbb33e); reran 20 parity checks and the original ten approved synthetic images | Separate draft PR and review; no merge/deployment authorized. Real CoreML artifact parity and representative segmentation acceptance remain unverified |
| Unknown cloud contour may be small | Measurement, history, single-record PDF and timeline PDF now say the original cloud contour/area may be small. Stored areas and masks are unchanged; old/new contour contracts still cannot form a common trend line | New candidate has not been delivered to a phone |
| Shared KEK leaves initial/rewrap proposals | Register proposal slots before wrapping, CAS-freeze inventory on closure, replace current initial/all rewrap proposals with permanent hash-only tombstones, count in receipt | Historical object generations/backups and old KMS versions still recoverable until separately verified retirement; legacy unindexed rows explicitly block completion |
| Single custody passphrase | Two-custodian plan prepared; owner-only use remains scoped | Real independent custodians, split materials and witnessed recovery drill not yet established |
| Secret mounts | Metadata-only inspection tool added, six tests; contained/escaping links classified without reading secret payload | Actual Cloud Run mount observation still required. Production pinned_file remains strict |
| Build provenance | Source, source-project hashes, signed IPA and delivery evidence recovered for 40/43/44 | Future upload must record a signed-file manifest and source tag; historic generated-project hashes cannot be invented |
| CI error annotations | The four exact injected tracebacks found in successful a3a3bed audit annotations are asserted and captured in their individual tests | New GitHub CI has not run; unrelated real errors and other fault logs are not globally suppressed |
| Privacy | Local tested Release bundle HTML is byte-identical to repository policy | Public policy publication/readback remains pending |
| Medical ba970b1 N1/N2/L1/N4 | Left separate, not claimed fixed | Original probes and a dedicated medical change/review required |

## Student comparison and interpretation

Fresh read-only cloud checks on November 7 still show research revision `woundai-lite-candidate-00009-kid` and medical revision `woundai-backend-mmhps20261007-opt-4b39849`, each serving 100% of its service traffic. They match earlier revision evidence. The November 1 image/source inspection established the missing sigmoid in those images; this round rechecked revision identity, not another image extraction. Main is still 4b39849.

The old cutoff on logits at 0.4 corresponds to sigmoid(0.4) ~= 0.5987, not the intended probability cutoff 0.4. This is an output-contract bug, not a universal area correction factor. The isolated seven-file fix also carries contract provenance and CI/mocks needed to test it; no commerce/account changes are included.

Ten previously consent-tagged paper-target photos were hash-checked before/after replay; five at each original distance. Reference masks are saved color segmentation of ONE printed rectangle, not expert clinical masks. The stored legacy per-image mm/pixel was held fixed in each pair; absolute physical area calibration is not validated by these numbers.

| Route, mean over 10 images | Old Dice | New Dice | Old IoU | New IoU | Old area cm² | New area cm² |
|---|---:|---:|---:|---:|---:|---:|
| Student only | 0.000612 | 0.001245 | 0.000307 | 0.000625 | 1.7625 | 2.0124 |
| Lite on_weak | 0.095227 | 0.095635 | 0.078192 | 0.078397 | 7.7062 | 7.8289 |
| Medical always | 0.095227 | 0.095635 | 0.078192 | 0.078397 | 7.7062 | 7.8289 |

Both old/new Lite and medical paths ran the ensemble on 10/10 images and selected its result on 5/10. Weak-fraction thresholds 0.005/0.01/0.02 all escalated all ten images. Therefore retain LITE_WEAK_FRAC=0.01: this sample provides no discriminating evidence for changing it. Segmentation is poor on these synthetic rectangles; the mathematical fix passes, wound segmentation accuracy DOES NOT. No auto-rewrite of historical masks, areas, training selections or trends. The fresh paired JSON equals the November 1 result exactly.

The 20 parity checks cover the mathematical output contract and CPU test adapters. They do NOT establish parity with an actual exported CoreML model on a device. Model files and original images are excluded from public review material.

## Current proposal erasure

New candidate keys have a CAS proposal index, bounded at 256 registered slots. Before a wrapping operation can write any proposal, its slot must be registered. Closure writes a durable destroy intent, closes the current key and freezes that same index. It tombstones every registered slot, including absent ones, so a delayed create-only writer cannot resurrect data after closure. Audit records contain identity hashes, slot hashes and previous-record hashes; no wrapped DEK is copied to audit.

Receipt adds `current_proposal_tombstones`; it still says `grant_key_unrecoverable=false`, `historical_key_access=pending_retirement`, `physical_backup_deletion_confirmed=false`. The count refers to proposal slots, not historical object versions. Old shared-key rows without the new index fail closed with an explicit migration requirement; no silent inventory guessing.

Independent synthetic regression: before close, current row + initial + two rewrap copies = 4 wrapped DEKs. Old code retained 3 proposal copies after close. New code retains 0, with 3 proposal tombstones and 0 audit wrapped values. Tests cover missing inventory, failed tombstone write, lost response/restart, late proposal write, late registry CAS, and unaffected second account. A separate rotation probe deliberately confirms an old backup remains decryptable before KMS retirement. No real key was disabled/destroyed.

## Verification actually performed

| Verification | Result | Scope |
|---|---:|---|
| Full Lite backend discovery | 2,504 passed | Includes seven new erasure cases; local loopback fixtures, no cloud calls |
| Focused key/disposition suite | 88 passed | Overlaps full Lite |
| Changed HTTP fault-injection tests | 41 passed | Rerun after narrow logging assertions; overlaps full Lite |
| Secret mount metadata | 6 passed | Local synthetic mounts only |
| Main-based Student parity | 20 checks, zero failures | Existing isolated main fix |
| iOS Release selected PDF/grouping/quality suites | 44 passed, zero failures | Simulator; NOT a claim of the full iOS suite or real-phone acceptance |
| Paired synthetic inference | 10/10 records replayed | Source/image/mask hashes retained; poor segmentation explicitly recorded |
| Shared rotation probe | Passed | Backup still recoverable correctly reported |
| PDF visual review | Five-page synthetic timeline generated; first and warning page inspected | New footer fits; original numbers retained |
| Local privacy parity | Equal SHA-256 | Repository HTML vs tested app resource |
| New remote CI | Not run | Requires authorized push |

Initial full run inside restricted sandbox had 13 loopback permission errors and one nondeterministic test assertion (expected remaining proposal count depended on hash order). Final unrestricted-local-loopback run passed all 2,504 after asserting the specific blocked proposal instead. An intermediate PDF assertion failed because a draft footer dropped “免貼紙”; final footer restores it and all 44 tests pass. These intermediate logs are retained, not erased.

For CI, capture only the anticipated log in the four fault cases while asserting the exact injected message and response/recovery semantics. No blanket logger disable, matcher removal, ignore-errors or success-forcing filter. Remaining unrelated negative-case tracebacks are intentionally visible.

## Build provenance corrections

See [machine-readable ledger](lite_build_provenance_20261107.json).
- 40: 9ba47559408645384e0d18607d8fb8117a630604; IPA 5e853bb5…736b42. Local export only, not an observed TestFlight delivery.
- 43: dfb5ca45ad069aab770025b47fe8add511795933; IPA 37dbabe5…10c01ce. Owner TestFlight receipt recorded November 3. Preliminary 7b15390/1b7f7da exports must not be substituted.
- 44: 53b4742b8d046558947628baa806fac274d7d177; IPA 98e10674…4dad7bc6. Owner TestFlight receipt recorded November 4; Jack confirmed installation. This round did not repeat Apple portal login.

The ledger records source project.yml / project.lite-sandbox.yml hashes and effective candidate overrides. It explicitly does not pretend all old generated Xcode projects or contemporaneous backend readbacks were captured. Backend revision identity is recorded separately from the App build.

## Operational plan and remaining work

[Retirement, custody, mounts, release order and authorization plan](lite_external_retirement_release_plan_20261107.md).

No additional capture is needed for this round. Original synthetic records, images, depth, masks and audits were left intact. Updated warning is a presentation change only. Do not infer that passing these tests authorizes clinical research, external beta, paid subscription availability, or a deletion deadline.

References: [PR #37](https://github.com/JackH0001/WoundAI_Proj/pull/37), [PR #36](https://github.com/JackH0001/WoundAI_Proj/pull/36). Local evidence and patches are provided separately in the November 7 Claude review archive.

