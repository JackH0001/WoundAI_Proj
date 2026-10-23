# Claude handoff: isolated GCS runner and console availability (2026-10-23)

Baseline: `7074e6745e6b7365eee55ea8306c68a908244f9e`, draft PR #35.
The reviewed candidate is the commit containing this document; the external
manifest pins its full hash and individual source files. No merge/deployment is
implied. Previous-head CI has now completed: all eight runs passed.

## What changed

- `tools/medical_consent_gcs_smoke.py` implements the first bounded real-SDK
  acceptance runner. The default is preparation only; execution requires a clean
  exact source snapshot, unexpired manifest and canonical digest, dedicated
  executor credentials, and separately authorized new synthetic buckets.
- `medical_consent_smoke_scope.py` pins project, bucket names, namespace, IAM
  role bindings, effective permission probes, policy and budgets. Checks precede
  credential discovery or mutation as appropriate; every mutation rechecks
  bucket policy/permissions. Audit writes are create-only; media deletion is
  limited to observed generation-pinned synthetic media. No bucket/IAM changes,
  teardown, automatic retries or existing MMH/Demo/Lite data access are provided.
- The tools-only adapter reuses production consent/migration/cleanup algorithms
  without adding an unlocked production audit mode. Its receipts are **not
  WORM**. The production adapter still rejects these disposable audit policies.
- The same runner executes against an offline SDK model: restore/purge CAS,
  competing workers, reconsent with new media, generation cleanup, additive
  migration readback and completed-run replay. Final object inventory hashes
  live/noncurrent synthetic bytes, but never restores soft-deleted generations.
- A future cleanup Job validator checks proposed configuration only. It does
  **not** prove runtime identity, provision a Job or bypass formal locked-audit
  requirements. Those deployment gates remain open.
- The shared console now hides the Lite tab when the service has not registered
  all three Lite reviewer endpoints. Role checks remain independent. Old `#lite`
  bookmarks fall back to an available tab without issuing a Lite data request.
  The separate public Lite service still rejects `/console` and reviewer routes.
  This is a UI fix, not permission to enable Lite API on medical/demo services.

## Actual storage clarification

Read-only Cloud Run configuration on 2026-10-23 confirms three distinct media
buckets and runtime identities for MMH, synthetic review and Lite candidate.
MMH/review have separate audit/security buckets; Lite has its own security bucket
and `lite-public-v1` namespace. They remain in the same GCP project: this is not
project-level isolation, nor a fresh complete audit of all inherited IAM grants.
MMH and demo have `WOUNDAI_ENABLE_LITE_API=0`; Lite candidate has it set to `1`.
The legacy shared tab only used `audit.read` to become visible, explaining its
404 on medical/demo services. It is not evidence of shared Lite data.

The proposed `woundai-consent-smoke-20261023-*` resources are **new medical consent
operations test resources**, not WoundAILite's existing buckets. They have not
been created. Lite candidate remains internal TestFlight infrastructure, not a
publicly released product. No independent Lite administrative website is
activated by this patch.

## Verification

| Selection | Local result | Scope |
|---|---:|---|
| Medical consent/migration/operations/plan + exact smoke runner + staging/research guards + console | 165 passed, 124 subtests | 161 medical plus four console tests |
| Cloud test guard, MMH/review policy, store abstraction, Lite build context | 89 passed, 117 subtests | Isolation selection; not live IAM acceptance |
| Admin console executable checks | Passed | Role separation, disablement, audit and API enforcement |
| Embedded console JS syntax | Passed with Node | Rendered JS parses |

The four console regressions exercise rendered navigation in Node (including an
old Lite bookmark and an admin role), both feature availability states, per-app
rendering and the public Lite route fence. No patient media is needed.
The exact smoke test is 24 tests within the 165, not an additional 24.

The first object-inventory test assumed exactly two soft objects; the
restore/purge race can legitimately produce a third when purge wins. The
corrected assertion compares every soft generation with the independent SDK
state instead of assuming a race winner. This was a test expectation correction,
not a change to consent/cleanup semantics; the initial log remains in evidence.

```sh
python -m pytest -q engineering/phase2/test_medical_consent_order.py \
  engineering/phase2/test_medical_consent_rollout.py \
  engineering/phase2/test_medical_consent_operations.py \
  engineering/phase2/test_medical_operations_plan.py \
  engineering/phase2/test_medical_gcs_smoke.py \
  engineering/phase2/test_p0_4_staging.py \
  engineering/phase2/test_research_format_guards.py \
  engineering/phase2/test_console_capabilities.py
python -m unittest discover -s engineering/phase2 -p test_medical_gcs_smoke.py
python engineering/phase2/test_admin_console.py
python engineering/phase2/test_console_js.py
```

## Review priority and remaining gates

Review exact namespace/generation pinning, create-only run claim under two
independent operators, live SDK pagination/options, IAM inheritance probes,
mutation-free failures, and no formal adapter registration. The offline shared
SDK is not a distributed server; a live two-client race is still required.

No live smoke run, real-media deletion, index migration, Cloud Build, cloud
policy/IAM update, scheduled cleanup or store distribution was performed.
Only deployment metadata was read. True soft-retention expiry (seven days),
forced process crash, late external writes, held objects, permission loss and
policy-zero transitions are **not** passed live cases. The prior real policy-zero
LIST failure still blocks current MMH cleanup; this tool does not change that
policy automatically. Do not infer clinical readiness from local tests.

See [runner contract](../engineering/contracts/medical-consent-gcs-smoke-v1.md)
and [operations acceptance gates](../engineering/contracts/medical-consent-operations-acceptance-v1.md).
Latest-head CI and any future execution authority are separate evidence; earlier
mobile CI success is not relabelled as a new mobile build.
