# Shared rotating KEK: custody candidate, 2026-11-04

Status: local synthetic validation; **not connected to native startup or the public deletion receipt**. No cloud write, key import, key enablement/destruction, deployment, purchase, TestFlight upload or original-data change is part of this change.

The owner chose to validate shared rotation. The existing native candidate instead selects `AccountGrantCipher`, whose KMS version is dedicated to one account. The earlier 2026-11-01 two-version Google KMS experiment verified wrapping/rewrapping, but did not connect those operations to the indexed Apple login and revocation code. This change closes that **custody-core** gap. It does not complete service deployment.

## What is implemented

- `RotatingGrantKeyProvider` permits only a bounded list of versions of one explicit CryptoKey. Encrypt pins the write version. Decrypt uses the key endpoint and authenticated context including the intended version. It has no account-level key destruction operation.
- `RotatingGrantCipher` keeps a separate random DEK for each account. Its v3 token envelope binds scope, account, grant context and immutable DEK ID; the wrapping version is deliberately outside that token envelope, so rewrapping does not rewrite original Apple ciphertext.
- Key rows, dependency reservations, rotation proposals and receipts use generation-checked writes. A lost response resumes the saved proposal. A changed source is not overwritten. Rotation cannot go backwards or run for a closing account.
- Before **every** wrap, reserve its version in an auditable dependency inventory. This includes a wrap whose response is lost or whose proposal never replaces the current row. A closure inventory based only on committed rows would miss those decryptable copies.
- Once Apple revocation is confirmed, an immutable account closure intent fences both current and restored copies. The current wrapped DEK becomes a tombstone. Another account using the same KEK remains usable.
- The receipt explicitly reports `historical_key_access=pending_retirement`, `retirement_inventory_verified=false`, `grant_key_unrecoverable=false` and `account_deleted=false`. Current access denial, KMS disabling and ciphertext deletion are not permanent erasure.
- No plaintext DEK is persisted, cached or logged. The key store retains encrypted initial/rotation proposals for recovery; audit contains hashes and version dependencies, not usable wrapped DEKs. Python cannot guarantee memory zeroization.

Native composition and legacy deletion progress now reject an incompatible provider/cipher before writing their state. This is deliberate: the existing progress protocol can only express per-account KMS destruction. It must not interpret shared-key closure as `DESTROYED` or silently select this new scheme.

## Reproduce

Use the repository's pinned `engineering/phase2/requirements-commerce-test.txt` and prelaunch workflow dependencies. No credentials or network are needed for the experiment itself.

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask python -m unittest -v test_lite_rotating_grant_keys
PYTHONPATH=tools:engineering/phase2:Backend/Flask python tools/lite_rotating_grant_keys_probe.py --output /tmp/shared-kek-probe.json
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = 'tools;engineering/phase2;Backend/Flask'
python -m unittest -v test_lite_rotating_grant_keys
python tools/lite_rotating_grant_keys_probe.py --output "$env:TEMP\shared-kek-probe.json"
```

The 47 new tests exercise actual indexed Apple-authority code with synthetic Apple responses, real AES-GCM and SQLite CAS, plus a multi-version KMS transport double. Network is denied in the custody tests. Coverage includes two-account isolation, same-account devices, two rotations, lost write/proposal/receipt acknowledgements, concurrent workers, closure races, stale sources, missing/corrupt state, uncommitted key dependencies and production-composer refusal. These are not real Apple/KMS/Cloud Run acceptance tests.

The probe additionally decrypts a retained copy with independent AES operations, bypassing application access checks. It proves that an old copy remains decryptable while the old KEK is usable, while the second account's rewrapped token remains usable after the old KEK is disabled. It makes zero model calls/purchases and leaves the quota and identity registry unchanged.

## Before any service can use this scheme

1. Define a new disposition catalog and native UI state for shared-key closure / pending backup retirement. Existing clients must reject unsupported receipts without claiming completion. Never recycle the old per-account catalog hash.
2. Add an explicit startup schema and matching private-worker composition. Pin shared key/version inventory, deny per-account provisioning and grant only version inspection + encrypt/decrypt to the runtime. Include the module in the reviewed packaging allowlists only with that integration.
3. Implement a separately authorized retirement executor. It must account for active rows, initial/rotation proposals, versions, backups, pending writes and worker drain; migrate all surviving accounts; prove no live dependency remains; observe the agreed retention window and KMS destruction. The per-account reservation inventory here is supporting evidence, **not** proof that all external backups have been inventoried.
4. Define crash/backup restoration procedures that preserve account closure intent and never restore a deleted account's access. Shared KEK restoration can restore historical decryptability; do not promise instant cryptographic deletion or a fixed deletion deadline before this is specified and validated.
5. Inventory any existing per-account or legacy vault before a fresh shared-mode deployment. The modes intentionally cannot open each other's records. Do not overwrite the immutable mode record to force migration.
6. Complete the separately authorized owner Apple-key import, explicit service deployment review and real Apple login/Sandbox purchase/restore acceptance. The two Apple `.p8` signing keys are distinct from these KMS wrapping keys; the owner-import authorization does not authorize this deployment or retirement.

## Cost implication and references

Shared KEKs move version-retention cost from one KMS key per account to a reviewed number of shared versions. Each account still has a DEK and state/audit operations; total cost is not zero. No updated price or break-even claim is made from this synthetic test. A retirement/backup retention duration must be chosen before estimating the number of concurrently billed versions.

- [Google KMS encrypt REST contract](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys/encrypt): accepts an explicit CryptoKeyVersion and returns the version used, supporting a pinned write path.
- [Google KMS decrypt REST contract](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys/decrypt): key-level decryption selects the ciphertext's version; associated data must match.
- Existing experiment evidence (local): `woundailite-kms-provider-20261101/live-single-run/verification.json`. Its two synthetic versions ended disabled; permanent erasure was explicitly not verified. This change does not enable or reuse those cloud versions.

Claude review focus: retry after a proposal is saved but current-row CAS fails; closure crossing that CAS; inventory of an uncommitted new wrapping version; old ciphertext remaining decryptable; and guards preventing accidental use with the old deletion catalog.
