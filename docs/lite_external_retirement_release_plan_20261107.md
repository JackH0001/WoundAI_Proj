# WoundAILite external-release preparation — 2026-11-07

This is a design and acceptance plan, not authority to deploy, merge, delete objects, destroy keys, publish policies or upload another build. Preserve existing data and audits. The current round uses synthetic fixtures and read-only cloud metadata.

## 1. Separate Student hotfix

Prepared source: a68eaa960700bee9442b7d37ce756d29535ec078, parent main 4b398498ce9d372f441d7e8319a3c5296dfbb33e. Seven files, output-contract fix/provenance plus tests/CI. Review separately against main, not by merging the large Lite branches.

Sequence:
1. Authorized source push and small draft PR; require its exact-source CI. The local 20-check suite is not remote CI.
2. Review paired reference masks, Dice/IoU, area, ensemble selection and preprocessing contract. Ten paper rectangles are a pipeline smoke test, not clinical segmentation acceptance. Preserve originals, never relabel an algorithm change as wound growth.
3. Compare real cloud ONNX and exported CoreML on matched image/resize/threshold fixtures before claiming cross-platform parity; this remains outstanding.
4. Build a pinned candidate image and record source/model SHA, helper contract, dependencies, environment and full image digest. Retain current service revision/configuration for rollback.
5. Separate bounded deployment authorization must name project, service, image digest, permitted config/IAM changes, test data, traffic switch, rollback and budget. Not yet prepared as an executable permission request because no final CI-approved image exists.
6. First validate synthetic data in an isolated service. Confirm raw logits become sigmoid probabilities BEFORE resizing; image orientation, coordinate mapping, metadata and contract survive upload/readback; no double sigmoid; medical policy=always and Lite on_weak both checked.
7. If approved, update Lite research first. Medical service uses the same model path and should receive the same correction, but requires its own matched validation and authorization before changing clinical workflows. Do not update both silently.
8. Stop on startup/model-hash/contract mismatch, new 5xx, missing original-media bindings or changed consent/access behavior. Restore the pinned previous revision/configuration; leave validation artifacts intact and flag the rollback output contract. No mass recomputation or deletion.

Read-only baseline November 7: research woundai-lite-candidate-00009-kid; medical woundai-backend-mmhps20261007-opt-4b39849. Exact deployed image/config readback must be refreshed immediately before any authorization and execution. An old revision still has the model bug; rollback is operational recovery, not an accuracy certification. Keep new “may be small” warning and trend separation.

## 2. Shared KEK closure and retirement

Current candidate closure now tombstones all indexed current wrapped-DEK proposals. Historical generations/backups may retain ciphertext and wrapped keys. A CAS overwrite is NOT deletion of all GCS generations.

Before enabling shared-key storage:
- Inventory key-vault current/noncurrent generations, soft-deleted objects, lifecycle/retention/holds, backups, replicas and disaster-recovery exports; collect metadata only.
- Require a complete per-account proposal index. Existing unindexed accounts need an explicit bounded migration before this code can report closure. Current candidate refuses them.
- Keep long-lived audit hash-only. Never put recoverable wrapped DEKs, Apple credentials or key export payloads in a seven-year immutable audit bucket.
- Define version/backup retention and disable indefinite exports. Do not change actual bucket settings as part of a code rollout without approval.
- Review all versions referenced by live accounts, closed-account historical copies and retry proposals; no destroy while a live account or recovery dependency still requires that version.

A deadline must include ALL of:
`remaining rotation interval + rewrap window + required old-key backup retention + scheduled destruction delay + operational delay allowance`.

Illustrative engineering target only: 14 + 2 + 7 + 30 + 2 = 55 days; a 60-day promise would require measured enforcement of every bound, complete inventory, alerts/escalation and a policy for missed deadlines. It is NOT selected, verified or promised to users. With indefinite backup retention or delayed rotation there is no defensible fixed upper bound. Do not substitute an eventual rotation estimate for a deletion receipt.

Google documents disabled keys as reversible; scheduled destruction is also reversible until completion. The default scheduled duration is 30 days, configurable at key creation within provider bounds. Read the actual key settings, then compute the bound; do not assume an existing key can be changed to a shorter schedule. [Cloud KMS states](https://docs.cloud.google.com/kms/docs/key-states)

GCS overwrites with versioning can leave noncurrent versions; disabling versioning does not remove already retained versions. Soft delete adds a separate recoverability period. Therefore current-value tombstones must not produce a historical-erasure claim. [GCS Object Versioning](https://docs.cloud.google.com/storage/docs/object-versioning)

Required synthetic provider acceptance before external beta:
- Live A/B accounts on a shared version; close A, inventory all A current proposals, ensure zero current wrapped DEKs and no A resurrection.
- Rewrap B to next KEK, verify B decryption/recovery and failure rollback without reopening A.
- Restore an old snapshot intentionally in an isolated environment: A must remain denied by durable closure even when historical material is technically recoverable.
- With a separately authorized test key, schedule/cancel destruction as planned, then eventually prove DESTROYED and failed decrypt on retained test ciphertext. This round did not create/destroy a key.
- A scheduled destruction receipt alone is never “unrecoverable”; final provider state and inventory are required. Real Apple token revocation and service/research cleanup have independent receipts.

## 3. Custody and disaster recovery

Owner-only internal tests may retain the current scoped setup. External beta must not use one passphrase or one authority able to impersonate every approver.

At minimum two independent human custodians, distinct MFA-protected principals, distinct encrypted archives/passphrases and no common recovery copy that collapses separation:
- Group A: identity HMAC/Apple sign-in and server API operational access, under the operator's controlled runtime path.
- Group B: research issuer signing material and its recovery authorization, held by the independent custodian.
- Drain-observer signing material must be separate from the research issuer in access policy and recovery archive. It can be a third compartment managed by a separate principal; an operator may request an observation, not sign both evidences.
- KMS administrating destruction and runtime decrypt permission are separate roles. A key custodian approves lifecycle operations, not general model/service deployment.
- Store private material in approved secrets/KMS storage; repo/CI artifacts/review bundles contain only metadata and hashes.

Before external authorization, record named custodians, concrete resources/roles, independent storage, rotation/revocation triggers, loss-of-person procedure and access review. Run a synthetic restore drill where one custodian alone cannot reconstruct both issuer and observer authority, while both can recover and validate service identity with preserved audit. Existing tests that load all seven keys with one passphrase do not prove dual control. Names and live split-storage deployment remain pending Jack's decision and explicit authorization.

## 4. Secret mount inspection

Run tools/inspect_lite_secret_mounts.py inside the exact candidate container, UID and mounted service context. Save sanitized lstat/stat metadata for each configured mount: role label, mode, UID/GID, file type, symlink status, target-contained flag and effective access. Never cat secret payloads or put raw credentials in logs.

The helper is a read-only probe, not a permission bypass. Production pinned_file retains its strict rejection of symbolic links. If the real platform exposes links, stop; review a separate implementation that resolves only inside the same pinned mount, refuses escaping/linked parent directories, verifies regular/read-only effective files and protects against replacement races. A successful synthetic contained-link probe does not authorize relaxing production validation.

Cloud Run documents secret mounts but does not establish the concrete link layout for this service; first-deployment readback remains required. [Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets)

## 5. Build and merge traceability

For every future Apple upload, before transmission:
- Commit clean source and source project configuration; capture generated project hash, Xcode/SDK version, scheme/configuration and all nonsecret overrides.
- Record signed IPA hash/size, version/build/bundle, entitlement metadata and exact source commit.
- Create an annotated source tag tied to that build only as part of the authorized delivery. Pushing a new tag is a remote mutation and needs to be in the approved scope. No retrospective tags were fabricated in this round.
- Record Apple build ID, processing status, target tester group and backend revision separately. A backend switch changes the ledger even if the App build stays the same.
- Archive previous evidence append-only; preliminary exports stay explicitly superseded. Never edit a former receipt to imply a later binary was uploaded.

Reconstructed 40/43/44 ledger is in lite_build_provenance_20261107.json. It distinguishes local-only 40 from delivered 43/44 and distinguishes source config from unavailable historical generated-project hashes.

Read-back of PR bases on November 7: #29 and #30 both target main; #31 depends on #30; #32→#31; #33→#32; #34→#33; #35→#34; #36→#35; #37→#36. They are not nine strictly sequential layers beginning at #29.

Recommended merge review:
1. Student hotfix from main first, independently authorized/reviewed.
2. Review #29 and #30 separately against updated main; check conflicts and exact-source CI after rebasing rather than assuming earlier CI still applies.
3. Review #31, #32, #33 in dependency order, then #34/#35 including consent/index/cleanup boundaries.
4. Keep #36 and #37 draft until external-beta blockers are closed. Use a local integration branch to expose conflicts, not as a substitute for reviewing each behavior or approving a 166-commit bulk merge.
5. Medical ba970b1 N1/N2/L1/N4 belong in a dedicated change with original reprobes. Current Lite improvements must not imply those are fixed.

## 6. Privacy and validation gates

Local Release bundled policy equals the repository resource. External release must publish an approved policy, fetch that public URL and compare it with the bundle (including version/effective date, controller Jack Studio operated by Hou Hsien Chi, deletion/research withdrawal, recovery limits and data categories). No public page changed here.

Apple requires an account-deletion path for apps offering account creation. Account closure, research withdrawal and recoverability receipts must state the actual stage, not “all deleted” while backups/KMS retirement remain pending. [Apple account deletion](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion)

This round preserves the user-selected policy A: closing an account also starts linked research withdrawal; research-only withdrawal may keep the service account. The plan does not silently extend cleanup to a Mac research archive or unidentified legacy buckets. Every linked location needs inventory, disposition authority and accurate progress messaging before claiming completeness.

