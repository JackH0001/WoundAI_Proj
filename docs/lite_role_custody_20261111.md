# Role-specific operational custody and bootstrap signing

This is an offline, synthetic-validated implementation. It does not create real
keys, assign real custodians, replace account identity, deploy, or retire any
original backup. Cloud KMS keys and Apple's two `.p8` files are separate systems.

## Change and boundaries

Previously the bootstrap signing CLI had to decrypt all seven operational seeds
to use the operator seed. `lite_role_custody.py export-role` now derives an
individually encrypted role package from a verified existing seven-role backup.
It copies the existing seed; no identity or signing seed is regenerated. The
full **public** manifest is retained so its independent fingerprint and existing
configuration pins remain identical. The encrypted plaintext contains exactly
one role seed, its role and the original manifest hash.

The existing AES-256-GCM/scrypt implementation and bounded parameters are reused.
Each role export gets a fresh salt and nonce. The CLI requires a different
passphrase from the combined source, hidden terminal input and explicit export
confirmation. It does not accept passphrases in arguments or piped input.
Backup and receipt are create-only 0600 files in separate owner-private 0700
directories. A partial write stops and preserves the receipt for reconciliation;
it does not silently overwrite or delete anything. A new receipt must be
independently verified rather than trusted solely because it accompanied a file.

The bootstrap CLI accepts an operator-only package and its receipt. Wrong-role
packages are rejected before requesting a passphrase. Signed requests are byte
equivalent to those made with the original bundle, and independent observer proof
is still required by the executor. Signing does not create that proof or execute
the request. Combined backups remain supported for owner-only recovery. An invalid
role package never falls back to the combined restore path.

## Custody handoff still required before external testing

Role separation in a file is **not** two-person custody. The original combined
backup still decrypts all seven roles and remains a powerful recovery path. This
change intentionally preserves it, including after role export. The report must
not claim that one person can no longer obtain all roles.

Before any real handoff, record accountable custodians and independent recovery
locations. At minimum separate operator from observer, and disposition operator
from disposition drain observer; keep the research issuer separate from the
native-service issuer/operator access. The identity HMAC needs its own restricted
recovery procedure because replacing it changes account linkage. Each of the
seven roles can be exported separately, allowing stricter organizational groups.
One person choosing different passphrases is not proof of independent custody.

Decide who may access the retained combined recovery package and passphrase, how
dual approval is enforced, and how access is audited. Its retirement (if selected)
is a separate explicitly approved operation only after verified backup recovery;
this tool cannot destroy it or claim its copies are gone. No actual custodian has
been selected or verified by the synthetic tests. Memory zeroization, Windows
native ACL handling, disaster-site recovery and real-cloud readback are not
claimed. POSIX terminal testing supports macOS/Linux; Windows can review code and
use WSL for this CLI, not assume native Windows ACL equivalence.

## Future owner-operated sequence (not an execution authorization)

Use original verified source and independently held receipt. Place destinations
outside the repository, research archive, shared sync and logs. Do not send keys
or passphrases in chat. Use new absolute file paths for each role:

```sh
python tools/lite_role_custody.py export-role \
  --backup /absolute/private-source/materials.json \
  --receipt /absolute/independent-source/receipt.json \
  --role operator \
  --output /absolute/operator-backup/operator.json \
  --role-receipt /absolute/operator-receipts/receipt.json

python tools/lite_role_custody.py verify-role \
  --backup /absolute/operator-backup/operator.json \
  --receipt /absolute/operator-receipts/receipt.json --role operator
```

Repeat only the approved roles with independent passphrases and their intended
custodians. Reverify a copied backup in a new terminal, compare the original
manifest fingerprint and configuration, record custody outside the key material,
and keep all original files. The bootstrap `sign` command can then use the role
package via its existing `--backup` and `--custody-receipt` flags. Read-only
inspection, fresh observer evidence and cloud execution authorization remain
separate prerequisites in `lite_bootstrap_commands_20261110.md`.

## Validation

With the existing repository Python test dependencies and
`PYTHONPATH=Backend/Flask:tools:engineering/phase2:.`:

```sh
python -m unittest -v test_lite_role_custody test_lite_operational_materials \
  test_lite_bootstrap_commands test_lite_fresh_bootstrap \
  test_lite_fresh_bootstrap_factory test_lite_fresh_bootstrap_runner \
  test_lite_bootstrap_package test_lite_bootstrap_iam test_lite_fresh_inventory
python tools/lite_bootstrap_commands_probe.py --output /new/combined.json
python tools/lite_bootstrap_commands_probe.py --role-separated --output /new/roles.json
```

Local result: **231 tests passed**, including **23 new tests** (20 role tests and
3 bootstrap tests). Seven-role export/restore preserves original seeds and
fingerprints; tampering, incorrect credentials/roles, excessive KDF work,
cross-role substitution, partial writes, unsafe files and overwrite are rejected.
The prelaunch workflow now invokes the new tests and terminal probe; this does
not imply that CI has run on this unpushed revision.

The two synthetic terminal runs use 9 combined-mode and 17 role-mode subprocesses.
Both complete subscription/quota/release initialization, replay the same request
and preserve prior records/audits. Each invokes the synthetic model once and
uses one quota unit. The role run exports/restores operator and observer through
the real interactive CLI, rejects the original passphrase for a new role and
rejects observer-as-operator signing. Socket connections are denied by the test
wrapper. Apple, cloud KMS/GCS, real customer keys and production model accuracy
are not exercised. The existing dual-process ONNX closure tests are separate
evidence (`lite_closure_race_acceptance_20261111.md`), not included in this count.
