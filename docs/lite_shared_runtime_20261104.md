# Shared-KEK runtime and deletion progress, 2026-11-04

Local candidate integration only. This does not authorize cloud provisioning, service deployment, secret enablement, shared-key retirement, data deletion, purchase or an App Store/TestFlight update.

## Problem and resulting behavior

The previous custody core could wrap an independent account DEK using a shared rotating KEK, but the startup factories and deletion UI could only represent destruction of an account-specific KMS version. Connecting those components unchanged would either reject the shared provider or misrepresent historical backup decryptability.

| Component | Before this change | New explicit shared mode |
| --- | --- | --- |
| Native startup | Schema 1/2, account-specific KMS keys | Schema 3 requires one CryptoKey, a write version and a bounded read-version list; no per-account preparation endpoint |
| Private worker | Account-specific describe/decrypt/destroy | Schema 3, same custody pins and immutable baseline; describe/decrypt only, never encrypt/create/disable/restore/destroy |
| Public receipt | Account key scheduled/destroyed | Separate catalog; `backup_pending`, `shared_key_retirement` pending, `account_deleted=false` |
| iOS | Original account-key catalog only | Accepts either known catalog; refuses cross-catalog changes or shared-mode claims of `unrecoverable` |
| Packaging | Shared module absent | Explicitly reviewed native, key-worker and disposition dependency allowlists include it |

The old catalog stays unchanged. The new shared catalog is `777efba0a2965937c94677a5d617a9ec134173eeb2736e339576fc6b57de0068`. Neither the existing custody-mode record nor progress-catalog record may be overwritten to force a migration. Current installed build 44 does not contain this new parser; no shared-mode server may be enabled for that client as part of this work.

## Configuration and authority

New configuration includes exactly these additional fields (resource names here are placeholders):

```json
{
  "schema": "lite-native-startup/3",
  "grant_keys": {
    "scheme": "shared-kms-envelope-1",
    "crypto_key": "projects/PROJECT/locations/asia-east1/keyRings/RING/cryptoKeys/KEY",
    "write_version": "projects/PROJECT/locations/asia-east1/keyRings/RING/cryptoKeys/KEY/cryptoKeyVersions/2",
    "read_versions": [
      "projects/PROJECT/locations/asia-east1/keyRings/RING/cryptoKeys/KEY/cryptoKeyVersions/1",
      "projects/PROJECT/locations/asia-east1/keyRings/RING/cryptoKeys/KEY/cryptoKeyVersions/2"
    ]
  }
}
```

All prior source/image/configuration hashes, separate read-only secret mount checks, bucket checks, provider identity checks, scoped stores and signed worker budgets still apply. The private worker uses `lite-disposition-startup/3` and matching `grant_keys`. Missing or malformed pins are refused before provider access. This is a code-level transport restriction; live IAM and Cloud Run mount behavior still require separate acceptance.

Native operations can inspect listed versions and wrap/unwrap using the pinned provider. The private disposition worker can only describe listed versions and decrypt on the named key to revoke already indexed Apple grants. It cannot retire a shared version. No shared-key creation or rotation scheduler is activated by this configuration.

## Deletion semantics

The private account worker treats confirmed current DEK access closure as the end of its bounded `grant_key` lane. That does **not** mean backup erasure. Public/private receipts retain `shared_key_retirement` in pending work. The public state is always `backup_pending` for an observed shared closure, and iOS displays:

> 目前存取已停止；歷史加密副本仍待處理

Receipt validation is read-only: it requires the original closure intent, the closed current key row and the matching audit observation. It cannot fabricate missing evidence or make KMS/Apple calls. A hash comparison rejects type changes such as JSON `false` becoming numeric `0`. Missing evidence blocks a completion claim. The old per-account receipt path is preserved.

## Validation and reproduction

- 24 shared-runtime scenarios: the same nine signed native HTTP service scenarios plus shared-mode-specific preflight, private-worker and receipt cases. Free usage, subscription/restore without resetting used quota, cross-day successor, session renewal, lost settlement response, late results and route isolation execute through the actual factories with generated transports.
- Eight added XCTest cases verify the shared catalog, pending language, monotonic progress, original operation persistence, and rejection of misleading or incompatible receipts. Both normal and candidate Release suites passed: 578 passed, zero failures, one existing simulator file-protection skip per suite.
- The independent workflow probe recreates the private factory, rejects an unsigned command, replays a signed command without additional work, completes the bounded account lanes, and confirms one model execution, preserved quota/identity/source capture, zero shared-key destruction calls and backup-pending progress.
- Full Lite discovery passed 2,374 tests with zero failures or skips; exact output is in the local verification package. Counts overlap; do not add the new scenarios to the full suite total. Remote CI for this source remains pending a separately authorized push.

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask python -m unittest -v test_lite_shared_runtime test_lite_native_package test_lite_disposition_package test_lite_key_package
PYTHONPATH=tools:engineering/phase2:Backend/Flask python tools/lite_shared_runtime_probe.py --output /tmp/shared-runtime-probe.json
```

On Windows set `PYTHONPATH` using semicolons. Use the reviewed repository dependencies. Provider transports, certificates and image fixtures are synthetic; no real Apple login or purchase is performed.

The first run had one test-harness mistake: it attempted account resolution after closure rather than inspecting the saved quota ledger. Two packaging tests also needed their explicit expected file count increased by one for the new reviewed module. Their failed logs are retained; the product protection was not relaxed.

## Owner Apple import and remaining work

The owner's separately authorized import completed on 2026-11-04. Metadata-only readback found exactly one checksum-confirmed, disabled version in each of the two existing Apple secret locations, with no direct secret IAM binding. No secret value was read during that check. The Apple signing keys are separate from the KMS KEK/DEK scheme in this document. Importing them does not enable a service or prove live Apple authentication.

Before a live shared-mode acceptance: review a pinned deployment and rollback plan; inventory old mode records; deliver a compatible client; explicitly authorize exact secret-version access; verify native and worker IAM; then run Apple login, purchase/restore and deletion using the agreed synthetic account. Existing data and audits remain preserved.

Still incomplete: global dependency/backup inventory, retention deadline policy, surviving-account rotation across all backups, crash-restoration procedure and a separately authorized shared-key retirement executor. Until then there is no promise of instant cryptographic erasure or completed account deletion. Medical N1/N2/L1/N4, external review and broader launch tasks are outside this change.

Claude review focus: startup/schema opt-in, replay of closure without writing audit, legacy catalog preservation, same shared key with another live account, signed private-worker restrictions, false-versus-zero receipt tampering, and the distinct meaning of an observed account lane versus pending shared-key retirement.
