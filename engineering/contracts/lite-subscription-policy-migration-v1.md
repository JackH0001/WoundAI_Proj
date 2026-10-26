# Stable subscription policy transition — local, explicit, opt-in

Baseline: `137c884`. No startup/public route enables this component. No real Apple
product, cloud resource, purchase, account, quota day or research record is created.
Free2 and Plus10 total daily successes stay the same. US$1.99/month is the intended
price, not an entitlement input.

## Identity and trust boundary

The v1 association key contains the entire Apple policy hash. A changed fingerprint
can select an empty slot. The new stable reader requires an explicit bootstrap for
EVERY account, including unbound/free accounts. Missing inventory never means free.

The stable slot uses bundle/environment/subject. The row additionally validates app
ID, subject, token and audit identity. An app-ID typo cannot select an empty slot.
Cross-app/environment migration, changing an existing original transaction and
cross-account transfer are forbidden. All policy authorities share the SAME
ownership-proof store, preserving subscription ownership across policy changes.

Default Apple product validation still allows only the existing product. Trusted
startup may explicitly provide at most eight product IDs; each authority verifies
its exact current signed product. No client field or future renewal product selects
a tier. `com.woundai.lite.synthetic.next` is a fixture, not a real store product.

## Explicit control protocol

1. Independently authenticate and resolve the existing service account. App Attest
   identifies an installation; it is not human identity or account authorization.
2. Obtain a COMPLETE legacy-policy inventory and externally fence ALL old link
   writers, including in-flight processes. The library cannot infer completeness
   from missing opaque keys. Preserve independently verified inventory/fence evidence.
3. `plan` reads at most eight supplied legacy keys and their intent/completion,
   plus the stable generation. It returns a reviewable plan: persisted operation ID,
   exact registration/inventory hashes, prior row, target and writer-fence hash.
4. `execute` requires independent injected control authorization and live fencing
   checks (literal True), before processing and immediately before pointer CAS.
   Hashes only link to evidence; they are NOT authentication or a real cloud fence.
5. Verify the target with fresh signed Apple transaction/status and, for grace,
   renewal. Append one immutable intent per exact plan; establish an account-wide
   legacy-reader guard; recheck grant deadline and legacy snapshot after I/O.
   CAS the exact stable generation, read back, append deterministic completion.
6. Explicit retries use the SAME plan. A committed pointer can finish its receipt
   without another Apple query. Completed historical plans return historical
   receipts, even after newer migrations, without restoring an old pointer.

The v1 reader now checks the guard before and after I/O. This is a backstop, NOT a
distributed fence: an old process past its last check, or a binary predating the
guard, must be fenced externally. Tests simulate this trusted provider; actual
control authentication, inventory completeness, IAM and deployment are still gates.

## Failure, rollback and preservation

- Guard without stable row: no old/new admission. Row without completion: lookup
  unavailable until explicit retry. Neither state silently falls back to free.
- Expired uncommitted intent: keep it immutable, obtain a newly reviewed plan.
- Changed registration, snapshot, catalog or generation: reject stale plan.
- Competing plans preserve both intents; one generation-CAS pointer wins.
- Keep the stable reader during rollback. A reader without the newer policy can
  validate account identity and return old operation receipts; NEW grants fail
  with a migration condition (503). Do not remove the reader/guard or restore an
  old writer. Rollback pauses admissions; it does not reset quota or map accounts.
- Retain old v1 links/audit, registration, Apple proof, previous stable row inside
  the new intent, original operation/result IDs and usage. A previously reserved
  operation may settle once on its original ticket. Migration cannot refund or
  initialize a quota day. Expiry/refund retains used and pending counts.
- No ledger, research consent/withdrawal, image, depth or annotation mutation
  interface is available to the migration module. No delete/list/cloud client.
- Individual records/plans are bounded; total explicit audit retention is still a
  separate gate. This implementation does not add or enable cleanup.

## Verification limits

Official Apple verifier with synthetic certificates/Server API responses; real
SQLite CAS and thread races; actual GCS adapter over fake SDK; signed synthetic
App Attest HTTP. Scenarios cover unknown/bootstrap states, products, ownership,
grace/refund, four lost-write responses, concurrent/stale plans, corruption,
deadline/fence loss, two devices, pending work and reader rollback.

Not verified: actual Apple Sandbox/product transition/OCSP, live inventory or
writer-fence, GCS IAM, native purchase/account recovery or shipping UI acceptance.
Native source/build numbers and public routes are unchanged. Product allowlisting
is not permission to create or launch another subscription.

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. python -m unittest -v test_lite_subscription_migration test_lite_subscription_migration_probe
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. python tools/lite_subscription_migration_probe.py --output /tmp/lite-migration.json
```
