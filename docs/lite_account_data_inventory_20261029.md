# Account disposition inventory — local planning, not deletion completion

This increment follows `226a967`. The last published PR36 source remains
`d6f5bdb`; no deployment, real cleanup, store update or new cloud resource is
part of this change. Original media and all research/audit are preserved.

## Concrete gap and change

The deletion coordinator can freeze grants, revoke Apple credentials and report
result/key disposition. It did not provide a bounded cross-reference from that
frozen account to the actual identity, installation, historical session,
subscription and independently scoped research state. A current-result seal
therefore cannot establish complete account deletion.

`AccountDataInventory` now uses the existing `AccountDeletion` authority and its
actual identity/registry/subscription stores. It observes up to **five frozen
grant nodes per call**, including historical logins and multiple installations.
An optional actual `Reconsent` coordinator traces up to **16 consent epochs per
installation**, following prior audit hashes without comparing old consent
versions against today's disclosure. No metadata is fetched by a client-supplied
account name, arbitrary file path or guessed image identifier.

Every observed source is identified by namespace alias, exact CAS generation
and content hash. Absence is recorded as an observation, never as proof that no
historic data exists. References are split into content-addressed chunks of at
most 32 entries so the state store's 16 KiB limit remains enforced. All original
values, Apple tokens, account tokens and photos stay out of the inventory report.
The independent audit namespace holds observation receipts; it is still
restricted account-related evidence, not automatically anonymous public data.

Reads are checked again before the page receipt is written. `verify_unchanged`
requires that original receipt and every reference chunk, and rejects a changed
generation even if the contents are identical. This detects observed drift;
it is **not a transaction across stores** and does not prove old writers have
drained. No method is a cleanup authorization. The module is not registered in
HTTP, legacy startup, a scheduler, or the candidate image.

## Disposition scope and outstanding decisions

| Actual data category | Current evidence / action | Still required before complete deletion |
|---|---|---|
| Apple grant material | Frozen grant references; existing revoke + per-account key executor | Live Apple/KMS verification and recoverability checks |
| Current service masks | Existing durable slot seal | Historical/soft-delete/backup verification using prior history executor |
| Identity account and installation associations | Generation/hash references, ownership checked | Minimized tombstone schema and writer-drain proof; reviewed deletion/retention policy |
| Current and prior login sessions | Current session plus each frozen login's family reference | Complete auxiliary refresh/event/intake audit inventory; retention policy |
| Stable and configured legacy subscription links | Existing readers validate owner, token and audit; no Apple request | Transaction ownership/proof retention basis, exact duration and access restrictions |
| Research consent epochs | Hash-linked current/prior epochs and write fences | Raw-media/label inventory, unlinked legacy installations, independent authorization and all write-race handling |
| Quota and security/deletion audit | Preserved; never reset by this tool | Minimal retained fields, justification, expiry and disclosure; no invented legal retention period |
| Local photos/history | Unchanged | User choice remains separate from service account handling |

A second registered device is not proof that unrelated anonymous installations
belong to the same person. Missing research authority, non-research installation
IDs and an epoch-chain limit are reported as unresolved. A corrupt or foreign
record blocks that observation; it is never repaired, skipped or reassigned.
`whole_account_inventory_complete`, `account_deleted`, `cleanup_authorized`
always remain false, including on the last grant page. The metadata writer drain,
historical versions/backups, independent research/media association and retained
transaction/audit policy remain explicit release gates.

Apple requires account deletion to cover associated data except information
legally required to be retained, and asks apps to explain retention and billing.
Disabling service alone is insufficient. This inventory does **not** establish
legal retention grounds or claim App Review compliance. Subscription billing is
cancelled through Apple's management flow separately; the App must not claim a
server-side account closure cancelled the subscription.
[Apple account-deletion guidance](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion)
(checked 2026-10-29).

## Verification and replay

- New **35 tests**: multi-device and historical sessions; 106 grants across pages;
  five devices with 16 consent epochs each under the state-object size limit;
  current/legacy subscription and research ownership; missing/corrupt audit;
  source drift; lost audit acknowledgment; restart; no provider or source writes.
- Same standalone probe against parent `226a967` reports no inventory implementation.
  Current code observes three grants on two devices in two bounded pages; a
  subsequent same-content/new-generation update invalidates the original report.
- No extra model call, Apple/KMS call, quota mutation, image/depth mutation or
  research withdrawal occurs while making or reading the report.
- Full Lite suite: **1,641 tests passed**, including the new 35. Prelaunch replay
  and source/packet evidence are recorded in the accompanying local report; test
  sets overlap and must not be added together.
- No native source changed here. Latest parent native evidence is 424 passed,
  zero failures, one documented simulator protection skip at `226a967`.
  No new device, Sandbox or remote-CI success is asserted for this increment.

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local \
  python -m unittest -v test_lite_account_data_inventory
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local \
  python tools/lite_account_data_inventory_probe.py --output inventory-probe.json
```

Claude review priorities: validate the bounded cursor and absent-record semantics;
confirm all references are derived from existing ownership authorities; ensure
research consent remains independent; check chunks and source-drift rejection;
confirm the last page cannot become an erasure or retention approval. Remaining
work is to resolve and implement the policy/auxiliary-state catalog and compose
its audited executor with the existing deletion worker before Sandbox/release.
