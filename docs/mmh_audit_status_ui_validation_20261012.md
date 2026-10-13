# MMH audit protection display — 2026-10-12

The live system page showed a green WORM check for MMH's explicitly mutable audit storage. `loadSys()` used `/WORM/.test(h.store)`, which also matches the literal `not WORM`. This was a display defect; no locked retention policy existed or was enabled.

The row now says 「稽核保留保護」 and reads the existing structured `audit_retention` result. Only a verified boolean lock with a positive safe-integer retention period reports a locked policy. Verified unlocked MMH reports 「MMH 驗證模式・未鎖定」 and explains mutable protection. An unlocked retention period remains explicitly revocable; missing, unreadable or malformed metadata is unknown. The actual readback takes precedence over the configured mode and free-text description. No health endpoint, storage, permission or write admission logic changes.

Validation: Node executed the actual production `auditProtectionRow` and `loadSys` render functions with **13 health-state fixtures**, all passed. Includes the exact `not WORM` regression, locked/unlocked periods, mode/readback disagreement, missing/unverified metadata, string booleans, string/fractional/negative/unsafe numbers and contradictory zero-length locked policy. Existing whole-console JavaScript parsing also passed. New regression is included in the audit CI workflow.

Local source only, based on `85775e8`. Not pushed, built, deployed or included in the current live console. The live mismatch remains until a separately reviewed release. This UI check does not establish audit-chain integrity, all compliance obligations, or research authorization.
