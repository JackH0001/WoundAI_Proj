# Lite verified-account subscription association v1

Status: local opt-in core, not installed in application startup or public HTTP.
Free remains 2 successful recognitions per UTC day; Plus remains a **total of 10**.
US$1.99/month is the intended commercial price, not an entitlement input.
Research consent, withdrawal, cohort allocation and medical records are separate.

## Trust boundary

`ServiceAccounts.provision` already requires a trusted, independently verified
installation-to-service-account mapping. App Attest proves an installation, not
a unique human, and must not silently create or recover a service account.

`ServiceSubscriptionLinks.attach` is a control operation, not a public endpoint.
It resolves the existing immutable registration and takes the subject/token
**only from that registration**. The caller must first authenticate the person
and authorize this account action. `authentication_proof_sha256` links to that
decision's audit evidence; accepting a correctly formatted digest is NOT an
authentication implementation. Deployment needs an actual authentication provider,
proof retention/readback, request authorization/rate limits and recovery policy.

The candidate original transaction is validated by `AppleSubscriptionAuthority`:
fresh Server API status, official 3.1.2 verifier, app/environment/product/token,
ownership and chronology checks, and durable ownership/proof records. No client
price, paid flag, StoreKit local configuration, research consent or JWS alone
can authorize a grant. Tests use real cryptography with synthetic certificates
and fake status responses, not a live Apple purchase.

## State and operation

All namespaces are explicitly injected. No credentials, listing, deletion,
automatic migrations, schedulers or network clients are created.

1. Look up the verified installation mapping. Reject unknown installations and
   mismatching legacy paid bindings.
2. If the account already has the same association, validate its intent and
   complete/read back the same acknowledgment. This historical acknowledgment
   does not claim the subscription is still paid; it contains no daily limit.
3. Otherwise obtain a short-lived verified Apple grant. Persist an immutable
   audit intent containing policy/subject/token hashes, candidate transaction,
   registration/authentication/Apple proof hashes, timestamp and observed tier.
4. Validate freshness again after audit I/O. CAS-create a single account-level
   association. Its key includes the fixed Apple policy fingerprint and subject.
5. Read back and validate the winner. Same transaction is idempotent; a different
   original transaction conflicts. Never overwrite or discard competing proofs.
6. Create/read back a deterministic completion receipt. An association without
   confirmed completion blocks normal lookup until explicit control retry.

The completion receipt is type-sensitive canonical JSON. Boolean `true` must
not compare equal to integer schema version `1`.

`ServiceAccounts.resolve` overlays this association on every previously verified
installation of the same subject/token. Old registration records are unchanged.
No canonical association is copied into a client-controlled field. Every **new
quota grant** still uses fresh Apple verification. Repeated association/restore
does not replay a paid grant; completed operation readback needs no entitlement
query. A refund after admission honors the original reserved operation once;
new operations use the current tier. This is not instantaneous distributed
revocation: the authority's short verification window remains applicable.

## Invariants

- No quota initializer/reset method is reachable from the association module.
- Upgrade after two free successes leaves eight successes available, not ten.
- Verified devices share the service subject's ledger; reinstall/restore cannot
  mint a new allowance in this core. Human/device enrollment is still a gate.
- Refund/expiry keeps the subject, mapping, original operation IDs and receipts.
  If already used more than two, free remaining is zero, not a negative number
  or a reset. Billing-grace behavior remains blocked pending a signed renewal policy.
- Research withdrawal before or during service use remains withdrawn. No
  subscription method has a research write or withdrawal-cancellation interface.
- Missing/corrupt mapping, token mismatch, unavailable state or invalid audit
  never falls back to a guessed free/paid entitlement.
- Lost intent/link/completion responses can be retried explicitly without new
  inference, quota writes or replacement associations.
- Existing registration without this opt-in module keeps legacy behavior. Once
  associations exist, rollback **must retain the reader and fixed policy**.
  Disabling it would incorrectly show free service for linked paid accounts.

## Boundaries before rollout

Not implemented: human sign-in/device linking, public activation/restore route,
native purchase UI/server `appAccountToken` delivery and acknowledgment handshake,
alternative original-transaction replacement/account transfer, actual Sandbox
purchase/restore/refund, account deletion, or automatic repair workers.
Neither cloud IAM/physical namespace isolation nor live WORM retention has been
tested in this stage. Append-only behavior is enforced by this module's create-only
operations; infrastructure privileges/retention need separate review.

The existing opt-in service-only routes still need native/history integration,
legacy quota freeze and verified baseline migration. Do not enable old/new quota
writers in parallel. Do not roll out active purchases while incomplete links lack
a bounded user-visible recovery workflow. No startup enablement was added here.

## Reproduction

Install the pinned Python dependencies from `.github/workflows/lite-prelaunch.yml`.
Set `PYTHONPATH=tools:engineering/phase2:Backend/Flask:.` and run:

```sh
python -m unittest -v test_lite_service_subscription_links test_lite_service_binding_probe
python tools/lite_service_binding_probe.py --output /tmp/lite-binding-probe.json
```

The 32 new tests are part of the 462-test relevant regression set. SQLite uses
real persistent CAS and concurrent threads. GCS adapter calls use fake transport.
The fake-GCS probe reports request counts, not latency, a bill or profitability.
