# Private account-key worker startup and package

This adds the independent factory/package for `lite_key_provisioning`. Default
application startup is unchanged. No deployment, IAM mutation, real account/key
creation, deletion, Apple call or store update was performed.

## Before / after

Previously the bounded key preparer was injected only by a test harness. Now
`lite_key_startup:application()` takes an explicitly pinned configuration, checks
the named Cloud Run identity, bucket policy generations, existing key ring and
existing native lifecycle baseline, then serves only the private prepare route.
Startup appends/verifies its immutable plan receipt; it creates no account/key.

All four state lanes are explicit. Lifecycle and lifecycle-audit views reject
writes, even if code accidentally calls enrollment/closure methods. Worker audit
allows create-only writes; mutable worker jobs/budget keep generation CAS. Their
physical paths cannot overlap. The shared physical-location helper retains the
native backend's previous SQLite/GCS identity rules.

There is no automatic baseline creation, account migration or policy activation.
A missing/changed lifecycle marker, identity, key ring or bucket policy stops
startup. A changed configuration must have a newly reviewed digest. The binding
contains only the identity-HMAC key hash, never the secret.

The worker transport allows exact key/key-version GETs and deterministic key
creation within one named ring. It rejects encryption, decryption, version 2,
destroy, restore, rotation, IAM, listing and foreign resources before transport.
Creation remains bounded to the approved window (at most one hour) and total POST
budget. Restart never resets the budget or abandons uncertain creation. Expiry
still permits previously prepared keys to be read back, but cannot create more.
This is a finite acceptance mechanism, not a complete continuous production
provisioning policy.

## Configuration and independent package

The factory requires `LITE_KEY_CONFIG`, `LITE_KEY_CONFIG_SHA256`, `LITE_KEY_SOURCE`
and `LITE_KEY_IMAGE_SHA256`. Configuration is <=32 KiB strict JSON with schema
`lite-key-startup/1`, enabled=true, deployment, binding, buckets, stores, manifest
and manifest_sha256. See `configuration()` in the synthetic startup test for a
non-deployable example. Source and image in the plan must match independent
deployment pins; project, asia-east1 ring and distinct web/worker identities are
explicit. The actual source/image digest must replace synthetic test values.

`tools/stage_lite_key_candidate.py` copies an explicit 33-module allowlist and four
profile files. Missing/new nested local imports, symlinks, changed source, existing
output and extra files are refused. No model, Apple .p8, HMAC, token, photo, depth,
credential or native public entry is included. Shared schema modules do bring
Apple SDK/raster dependencies; they are not activated and expose no extra routes.
The profile excludes ONNX runtime/model binaries. Dependencies are pinned and
hashed; Docker uses a pinned Python image, non-root user and explicit factory.
Mac import/hash tests and offline Linux wheel resolution do not prove a Linux
container boot. Actual Linux image build remains a separate gate.
The prelaunch workflow now defines a dedicated Linux image job, including a
network-disabled, read-only container check that missing pins refuse startup.
That new job is not claimed passed until the fixed source is pushed and CI runs.

## Deployment and minimum-permission acceptance (not executed)

Use a dedicated private Cloud Run service/identity; do not reuse MMH, Demo or Lite
review identities. Only the declared native web identity receives service-level
`roles/run.invoker`. Its Google ID token audience must equal the private origin.
`invoker_account` records the intended IAM principal; the Flask factory does NOT
validate an external caller itself or configure IAM. Anonymous access must be
denied by Cloud Run. Do not run this factory as a public WSGI endpoint. [Google
service-to-service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).

Proposed custom permission sets, each limited to the exact resource/prefix:

| Identity / resource | Allowed intent |
|---|---|
| Worker / named ring | keyRings.get, cryptoKeys.get, cryptoKeyVersions.get, cryptoKeys.create (all under cloudkms) |
| Worker / listed buckets | storage.buckets.get for policy verification |
| Worker / native lifecycle + audit prefixes | storage.objects.get only; no list/create/delete |
| Worker / own audit prefix | storage.objects.get + create; no overwrite/delete/list |
| Worker / own job/budget prefix | storage.objects.get/create/delete for generation-CAS replacement; no list |
| Web / this private service | run.invoker only; no key-creation permission |

GCS replacement requires more permission than create-only auditing; that delete
permission must never extend to lifecycle, research or audit prefixes. These are
proposed permissions, not proven effective grants. Validate exact custom-role
support and conditional resource behavior before deployment. [KMS permissions](https://docs.cloud.google.com/kms/docs/reference/permissions-and-roles),
[GCS roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles).

Required live checks: anonymous and unrelated identities denied; named web caller
accepted with correct audience; wrong audience rejected; worker cannot decrypt,
destroy, rotate, restore, change IAM or access other rings; cannot list buckets or
read/write MMH/Demo/research media; native lifecycle writes and audit overwrites
denied. Check named-resource positive reads/create only on a separately authorized
synthetic account. Read back actual key policy/version, audit, budget and billing.
No broad project role is a substitute for a failed prefix-bound permission test.

## Failure containment and restart

1. Keep existing apps/services/data untouched. Deploy private, reviewed image and
   config only after separate authorization; keep web invocation disabled until
   negative IAM checks pass. Do not overwrite prior config evidence.
2. Bad config/baseline/provider read => stop before plan write/key creation. Plan
   audit unavailable => startup fails. Do not invent a new namespace to bypass it.
3. Creation reply lost => preserve job/audit, restart the same source/config and
   read back the deterministic key/version. Only then may the web exchange the
   one-use Apple authorization code (still subject to its own expiration).
4. Concurrent closure => do not exchange code or reopen the account; retain the
   created key as a recorded cleanup obligation. Never auto-destroy it on failure.
5. Deployment failure => remove only this new service's caller grant / stop new
   creation, return traffic to the previously verified configuration if applicable,
   preserve all jobs/audit/keys. Read back the containment state. If no previous
   verified worker exists, keep it unavailable; do not roll back to a public entry.
6. New source, plan window/budget or real-key cleanup needs the matching approval.
   Rehearsals here do not authorize Cloud Build, IAM changes or a deployment retry.

## Reproducible validation

With the repository's test dependencies and PYTHONPATH:

```text
python -m unittest -v test_lite_key_startup test_lite_key_package
python tools/lite_key_startup_probe.py --output probe.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

The 35 startup/signed-login cases plus seven package cases cover independent pins,
bad policies/baselines, read/create-only views, restart after response loss, expired
window, preserved budget, closure during creation and native/research route
exclusion. The four-scenario probe reports one key version, one Apple exchange,
one model invocation and one quota use after successful retry; closure produces
none of the latter three. These are signed synthetic HTTP, local SQLite and fake
KMS/Apple transports, not true Sandbox, GCS/KMS or physical-device acceptance.

Remaining: fixed-source CI; actual Linux image; live IAM/KMS and bounded deployment;
continuous provisioning budget/review; complete account deletion and retention;
true Apple Sandbox S0-S9; physical-device preservation/restore and external review.
