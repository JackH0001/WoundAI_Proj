# Claude follow-up: UBLA permission-probe failure (2026-10-24)

Baseline: `42631b6984ce91ef989c404962211fc3ce217384`, draft PR #35.
The candidate is the commit containing this document. No merge, application
release, production migration or cleanup schedule is implied.

## Failure confirmed

The approved synthetic provisioning attempt stopped **before audit-bucket
creation and before any fixture writes/deletes**. After IAM propagation, a
600-second dedicated-executor access token was issued, but the bucket readback
raised HTTP 400. The temporary TokenCreator binding was removed; policy readback
and effective `getAccessToken` denial were confirmed. Existing MMH, Demo and Lite
service hashes were unchanged. There is still no live withdrawal/cleanup pass.

A separate read-only probe of the operator's own permissions reproduced the
exact API error, without impersonation or reading bucket contents:

> Cannot test storage.objects.getIamPolicy or storage.objects.setIamPolicy on buckets with uniform bucket-level access enabled

The preflight included `storage.objects.setIamPolicy` as a negative probe. GCS
rejects the **whole** request on a UBLA bucket; it does not simply return that
permission as denied. The old offline double only intersected permission sets,
so previous unit passes did not cover this real API contract.

## Change

- Keep object ACL mutation in `FORBIDDEN_PERMISSIONS`, but exclude the two
  unsupported object-ACL probes from `PROBE_PERMISSIONS`.
- Continue to require UBLA, exact bucket bindings and the exact effective set of
  supported permissions. Disabling UBLA, extra supported rights, missing rights
  or unrelated API errors still stop execution. No policy is changed to make a
  test pass.
- Evidence distinguishes the requested probes from the two **not tested** ACL
  operations. Their inapplicability follows from the required UBLA configuration;
  it is not reported as an effective-permission denial or complete IAM inventory.
- The offline storage double now rejects the invalid probe. A test using the
  real installed Google Storage SDK and intercepted HTTP responses independently
  checks request serialization, the old 400, the new query and both bucket
  preflights. It uses anonymous credentials and no network.

## Validation

`148 passed, 68 subtests passed` locally, covering the exact smoke runner,
consent ordering, rollout, cleanup operations, planning, store abstraction and
cloud-test isolation. The smoke selection has 30 tests within that total.
The first real-SDK test needed its synthetic Response.request initialized;
that test-fixture repair is separate from the live GCS failure.

```sh
python -B -m pytest -q \
  engineering/phase2/test_medical_gcs_smoke.py \
  engineering/phase2/test_medical_consent_order.py \
  engineering/phase2/test_medical_consent_rollout.py \
  engineering/phase2/test_medical_consent_operations.py \
  engineering/phase2/test_medical_operations_plan.py \
  engineering/phase2/test_store_abstraction.py \
  engineering/phase2/test_test_isolation_from_cloud.py
```

The external evidence package retains the stopped run, corrective request
comparison, IAM grant/removal audit and raw original records. Latest-commit CI
and any future cloud-run authorization are separate evidence. A new fixed-source
manifest is required; the old authorization does not permit rerunning altered
code. Seven-day natural soft-delete expiry, formal deployment and app acceptance
remain unverified.

## Review focus and references

Review the distinction between unsupported probes and denied permissions,
continued UBLA enforcement, no ignored API errors, and immutable source/run pins.
Do not interpret local or query-format tests as a dedicated-executor GCS pass.

- [GCS uniform bucket-level access](https://docs.cloud.google.com/storage/docs/uniform-bucket-level-access): ACL operations are disabled.
- [Bucket testIamPermissions](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/testIamPermissions): read-only caller-permission check; no bucket permission required.
- [IAM access-change propagation](https://docs.cloud.google.com/iam/docs/access-change-propagation): removing a policy binding does not establish immediate effective revocation.
