# Private account disposition startup and package

This is a local implementation milestone, not a deployment or deletion approval.
The separate factory composes the existing six-lane worker and signed finite
executor over previously accepted account closures. Public native startup does
not mount this route. No scheduler, public access, real cleanup or key destruction
is enabled by this change. Whole-account completion remains `account_deleted=false`.

## Before and after

Previously the worker/executor could be assembled by trusted Python callers and
synthetic fixtures. There was no dedicated production dependency graph or image
profile. Reusing the public native graph would unnecessarily require identity
HMAC material, model/service admission, and subscription verification dependencies.

`lite_disposition_startup.application()` now requires a bounded, hash-pinned
configuration, explicit source/image pins and a mounted OAuth signing key. It
builds read/revoke-only contexts using the original persisted-record decoders.
It exposes only the signed private executor route. It cannot accept a new
deletion request, register an account, run inference, change quota, exchange an
Apple authorization code or refresh an Apple token.

The implementation imports shared modules for their original validators; it does
not pretend those classes are absent from the package. The restricted graph and
routes, rather than Python module imports alone, enforce these operation limits.

## Startup contract

- Required environment: `LITE_DISPOSITION_CONFIG`, `LITE_DISPOSITION_CONFIG_SHA256`,
  `LITE_DISPOSITION_SOURCE`, `LITE_DISPOSITION_IMAGE_SHA256`, `LITE_DISPOSITION_SECRETS`,
  and `DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true`.
- The regular JSON file is at most 64 KiB; the mounted signing key is at most
  16 KiB and has an independent SHA-256. No secret value is part of the JSON.
- Configuration schema `lite-disposition-startup/1` has exact fields. Deployment
  pins the project, project number, named runtime identity, asia-east1 key ring,
  source commit and image digest. Lite issuer/audience/namespace and identity-key
  **digest** must match existing native bindings. The HMAC key itself is not loaded.
- `buckets` pins complete metadata snapshots of 2–4 named buckets. Reload must
  match the hash, project, region, public-access prevention and uniform access.
  Even a harmless metadata update requires a newly reviewed snapshot; this is an
  intentional conservative startup refusal, not an assertion that all changes
  are unsafe. Research media must occupy a separate bucket from every state store.
- `stores` names all 32 state locations. Declared prefixes cannot overlap, and
  resolved store locations are checked again. No bucket is silently discovered.
- Existing native lifecycle, account catalog, identity/grant, per-account-key,
  deletion and progress configuration must exist and match. Missing native
  history is never initialized as a new account. Every listed person must already
  be closing with original accepted closure evidence and the exact frozen plan.
- The original signed manifest bounds execution to 1–10 named accounts, at most
  one hour, 100 commands and 1,000 reserved component dispatches. Operator and
  drain public keys are independent and are read by role name, never JSON order.
- Startup may create missing **private component configuration markers** using
  create-only writes. These markers may remain after a later validation refusal.
  Startup performs no provider disposition and does not remove earlier evidence.

`CloudProviders.identity_checked` checks the configured credential identity. It
is not a substitute for a live metadata identity/IAM test or proof of effective
permissions. No such live claim is made by the synthetic tests.

The factory now also requires durable provider allocations before operational
startup reads. See [provider budget contract](lite_provider_budget_20261031.md)
for request/payload limits, non-refundable restart reservations and exclusions.

## Narrow dependencies and proposed permission boundaries

| Dependency | Application behavior | Required deployment review |
|---|---|---|
| Lifecycle, identity, account registry, quota | read-only store views; no registration/admission | exact prefix reads; deny writes |
| Component audit stores | create-only views; no replacement | append/create only; retention decision separately reviewed |
| Disposition/checkpoint stores | original CAS transitions | exact state prefixes only; no bucket policy changes |
| Existing service operations | original result decoder, no model/claim API; disposition may seal payloads and remove approved earlier generations | selected result names/generations; deny unrelated namespaces |
| Research media | original ownership/epoch/seal proof and bounded version scanner | exact reviewed media scope; preserve permanent current markers |
| KMS transport | describe approved version, decrypt that account key, schedule its version destruction | per-account resource restrictions; deny create/encrypt/restore/import/rotate |
| Apple OAuth | only revoke URL; authorization-code exchange and refresh URL refused | mounted OAuth `.p8`; no Store Server API or identity HMAC key |
| Private HTTP | signed operator commands; recovery also requires independent drain proof | private IAM invocation, hard execution supervision and audited operator access |

Application wrappers are not cloud IAM. A dedicated identity, positive/negative
IAM probes, approved mount/secret custody and enforceable provider budgets are
still deployment gates. The source includes no cloud provisioning or IAM changes.

## Package and CI

`tools/stage_lite_disposition_candidate.py` produces a new directory outside the
repo, with 47 explicit Python modules and four image-profile files. It checks
nested static imports, symlinks and copy/hash consistency. The 51-file manifest
records whether the source tree was dirty and does not approve release.
`verify_disposition_package.py` verifies exact contents before importing startup;
it does not instantiate the app or contact providers.

The independent `Backend/LiteDisposition` image pins its Python base digest and
Python dependencies, runs as a non-root user and invokes the private factory.
The new Linux CI job builds it and checks that missing configuration refuses
startup with networking disabled. A local source/package test is not evidence
that this new CI job or container build has run successfully.

Existing key/native packaging adds the shared context decoder because the original
deletion/result classes now accept the restricted context. Neither existing image
adds the new private disposition startup or executor route. Packaging regression
tests cover both existing profiles.

## Reproduction and review focus

Use dependencies and `PYTHONPATH` from `.github/workflows/lite-prelaunch.yml`.

```text
python -m unittest -v test_lite_disposition_startup test_lite_disposition_package
python tools/lite_disposition_startup_probe.py --output startup-probe.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
python tools/stage_lite_disposition_candidate.py --repo . --output /tmp/new-private-disposition-context
```

The original 31 startup/package tests and subsequent budget integration tests use generated data, real application factories and signed HTTP
commands, with synthetic storage/Apple/KMS transports. The independent probe
restarts the graph between calls, replays a completed request with worker execution
forbidden, and compares original capture, identity/audit and quota state. Six
observed lanes do not mean every account data category has been erased.

Review source/image/config pin failures before connection; missing native baseline;
configured/resolved storage aliases; original closure/plan binding; public-route
refusal; original decoders; read/create-only permissions; KMS/Apple method limits;
and completed-response recovery without additional model work or quota changes.

An initial test fixture used a `test` key-ring region and was corrected to the
required region. A subsequent integration run exposed JSON signer-map ordering:
the implementation now selects `operator` and `drain` explicitly. Original failed
logs are retained with the local evidence. Neither failure was bypassed by relaxing
the production policy.

## Remaining gates

No real provider calls, patient data, production secrets or keys were used. Before
deployment, finish hard deadlines/cancellation, control-plane and billing limits, real
process drain verification, IAM/secret custody, live provider failure recovery and
the fixed-source execution review. A Gunicorn timeout alone is not proof of safe
provider cancellation. Deferred soft-deleted objects, retention/holds, backups,
unlinked/orphan data and retained identity/subscription/audit categories still need
separate reconciliation and retention decisions. Complete native deletion progress,
Apple Sandbox, preserved-record device acceptance and external-test release remain
open. No generic full-account deletion or clinical accuracy claim is introduced.
