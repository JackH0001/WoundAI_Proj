# Native Sandbox startup and independent image profile — 2026-10-31

This change closes the gap between a graph constructed by tests and an explicit
runtime factory. It does **not** authorize deployment or declare public launch
ready. Legacy `Backend/Flask/Dockerfile` still starts `app:app` unchanged.

## Before / after

| Before | After |
|---|---|
| Tests manually supplied account, usage and identity objects. | `lite_native_startup.application()` builds the same graph from bounded, hash-pinned mounted settings. |
| Native imports pulled `store` through an exception, and the HTTP gate referenced the research reviewer module. | Shared exception is independent; the legacy research blueprint installs its existing JWT/audit checker. The native package does not contain or import `app`, `api_lite`, `api_flywheel`, or `store`. |
| Only a legacy image profile / dependency lock existed. | Separate `Backend/LiteNative` profile, explicit file allowlist, pinned models and a separate hashed dependency lock. The legacy lock is unchanged. |
| Startup error could be mistaken for permission to initialize an empty policy. | Missing/contradictory activation evidence stops startup. No activation, migration, external observer publication, cleanup or key provisioning occurs. |

## Reviewed settings contract

The factory requires all five environment values. No local ADC, `.env`, service
account JSON key or automatic lookup of another service is supported:

- `LITE_NATIVE_CONFIG`: absolute regular mounted JSON file, at most 256 KiB.
- `LITE_NATIVE_CONFIG_SHA256`: digest of those exact bytes.
- `LITE_NATIVE_SOURCE`: exact 40-character application source commit.
- `LITE_NATIVE_IMAGE_SHA256`: exact 64-character built image digest.
- `LITE_NATIVE_SECRET_DIR`: explicitly mounted directory; references are single
  filenames, bounded regular files, not symlinks, checked against their digests.

Configuration is schema `lite-native-startup/1`, with `enabled: true` and exact
field sets (unknown fields rejected):

| Section | Required contents |
|---|---|
| `namespace` | Stable native account binding namespace; never silently changed to make old state disappear. |
| `deployment` | Source/image pins, project ID/number, named runtime SA in that project, KMS keyring in `asia-east1`. |
| `app` | `com.woundai.lite`, numeric Apple app ID, team, **one** allowed build, App Attest audience. TestFlight/App Store attestation only. |
| `apple` | Sandbox subscription environment, reviewed monthly product, separate OAuth/API key IDs, issuer UUID, bounded pinned root-certificate references. Official verifier keeps online certificate checks. |
| `budgets` | Explicit per-minute/day transport-abuse limits; independent of the free-2 / Plus-total-10 success ledger. |
| `buckets` | Exact bucket names and reviewed metagenerations, not a discovery prefix. |
| `stores` | Every one of 36 distinct state/audit lanes; bucket + non-overlapping `lite-native/<scope>/<lane>` prefix. |
| `controls` | Independent operator/observer **public** keys, durable release manifest, usage plan and baseline records; all hashes/identities must agree. No private operator keys in the web service. |
| `secrets` | Three distinct mounted references: 32-byte identity HMAC, P-256 Sign in with Apple key, P-256 App Store Server API key. |
| `model` | Preprocessing digest; three ONNX artifacts additionally checked against source-controlled artifact hashes. |

The config hash is a deployment integrity pin, **not** an authorization token or
proof of appropriate IAM. Review the root certificates independently before a
real deployment. The web process uses the named Cloud Run metadata identity.
After a real bucket reload it checks identity, project, region, UBLA, public-access
prevention, no retention/default hold, versioning off, explicit soft-delete 0,
no lifecycle rule and exact metageneration. It does not change bucket policy.

These are dedicated mutable native-service stores, not the existing MMH, Demo,
Lite research-media or seven-year WORM audit buckets. An immutable audit record
means create-only application writes; this profile does **not** claim an enforced
WORM retention period. A deployment proposal must separately specify retention,
backups, per-prefix create/get/update permissions and deny access to unrelated
buckets. Startup metadata checks do not replace those IAM tests.

## Startup, restart and failure behavior

1. Validate all settings, mounted secret types/hashes and signed-control pins.
2. Validate/load the three pinned models before making cloud clients.
3. Read only the named buckets and verify the named runtime identity.
4. Create the concrete state adapters, official Apple API/verifier and existing
   account/usage/identity graph. Validate already activated policy evidence.
5. Write only the existing create-only component binding markers where needed;
   preserve every prior account, request, quota, result and audit row. Repeating
   startup with the same binding is idempotent. A conflicting binding fails.
6. Expose only the declared signed device/account/purchase/service routes.
   Research, reviewer, operational and cleanup-worker routes return 404.

Paused/unpublished **service release** can start if the subscription policy was
already initialized, allowing deletion intake while new work stays blocked.
An absent subscription activation does not become an automatically approved
fresh deployment. `RuntimeRolloutProvider` refuses all operational transitions.
The runtime KMS transport permits metadata/encrypt/decrypt only and refuses
provision/destroy/restore; the deployed SA must independently lack those IAM
permissions. Operator/worker credentials must never be shared with this service.

Startup failures retain evidence and do not delete resources. Provider bodies,
keys and account rows are not included in startup error messages. Rollback of a
future deployment is to the previously reviewed **compatible** image and settings,
with the durable release paused when compatibility is uncertain; never reset the
ledger, change namespaces or resurrect old writers to make a health check pass.

## Local validation and reproduction

Use the same dependency overlay as the existing commerce tests, with
`PYTHONDONTWRITEBYTECODE=1`, `WOUNDAI_STORE=local`, and
`PYTHONPATH=tools:engineering/phase2:Backend/Flask:.`:

```sh
python -m unittest -v test_lite_native_startup test_lite_native_package
python -m unittest -v test_lite_attest_http test_lite_build_context
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

The first group has 39 tests. Nine signed-HTTP scenarios run through mounted
settings: free-2, Plus-total-10 and restore, session renewal, restart/replay,
cross-midnight recovery, settlement response loss, pause/delete intake, late
result after closure and blocked research/worker routes. They use generated
Apple/App Attest roots, synthetic KMS, model transport and temporary SQLite.
They are not live Apple Sandbox, GCS, device or clinical-accuracy evidence.

Packaging uses a new output directory outside both checkout and model source:

```sh
python tools/stage_lite_native_candidate.py --repo /path/to/checkout \
  --models /path/to/verified/models --output /path/to/new-context
cd /path/to/new-context
PYTHONDONTWRITEBYTECODE=1 python verify_native_package.py
```

The build-time verifier checks the exact file set and hashes, imports the native
factory and verifies that no legacy application module loaded. It does not start
the service or claim a deployment was accepted. Models, original images, mounted
keys and runtime databases are not committed to the repository. The tool copies
only the explicitly pinned model artifacts into the local build context.

## Remaining gates before Apple Sandbox

- A real Linux container build and staged-source/runtime validation. Resolving
  Linux wheels with hashes is useful evidence, **not** a successful container run.
- Fixed-source candidate review and separately authorized cloud resources/IAM;
  actual bucket readback, isolation, bounded load and rollback rehearsal.
- Authorized policy/release initialization and per-account KMS provisioning.
  The web service deliberately cannot create a missing account key; login will
  remain unavailable until the separate provisioning flow exists and is accepted.
- Sign in with Apple configuration, mounted key custody/recovery, real Apple
  Sandbox S0–S9, native UI and retained-record tests against that same candidate.
- Complete deletion, research-disposition linkage and retention/backup validation.
  HTTP 202 means intake accepted, **not** that the account or all data is deleted.

No patient images, research data, real tokens or keys are required for this local
validation. No deployment, IAM mutation, real data deletion or store update was
performed by these tests.
