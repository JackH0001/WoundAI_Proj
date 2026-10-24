# Preserving synthetic GCS continuation — Claude review reference

This change prepares a new authorized run; no live GCS execution or deployment
has occurred for this change. The stopped `7df6037` run produced an expected
claim and first synthetic media object before a real SDK iterator error. The
iterator correction was already verified in `6092748`. Do not erase or reuse
the old claim to force the first-run tool to succeed.

## Scope and behavior

- Keep original empty-bucket command unchanged in intent.
- Separate continuation command admits only the two existing dedicated buckets.
- Expect exactly the old claim (2,308 bytes) and synthetic image (21 bytes),
  pinned by SHA-256. Their cloud generations remain to be discovered under the
  next approved executor session. Missing/extra/noncurrent/soft/changed objects
  abort before writes; unknown bodies are not downloaded.
- New writes and inventoried version deletes use only
  `consent-ops-synthetic/20261024-r2/`. The old prefix cannot obtain a writable
  adapter. No audit version can be deleted. Create-only run claim prevents two
  runs from sharing the new namespace.
- Two-client preflight and final readback compare old generations, size and
  hashes. Interrupted/exhausted final readback is explicitly unverified.
- Budgets and 7-day soft deletion remain unchanged. Audit remains append-only by
  this executor's permissions, **not retention-locked WORM**. No patient objects,
  service deployment, bucket-policy mutation, scheduling or store release.

## Verification and limits

Local selected regression suite: **171 tests + 83 subtests passed**. New
continuation module: 20 tests + 15 subtests. Cases include manifest tampering,
expired scope, cloud guard before credentials, old-byte preservation,
wrong/extra/missing generations, foreign namespace refusal, changed current
generation during readback, failed-final-readback semantics, budget exhaustion,
partial-write failure with evidence, and no rerun into occupied namespace.

Real `google-cloud-storage` objects execute a complete synthetic consent,
reconsent/cleanup race and migration workflow through a strict **in-memory HTTP
transport**, including multipart writes, generation preconditions, checksum
downloads, stateful paginated listing and delete error translation. There is no
network or credential access. This catches SDK integration errors missed by the
previous list-based fake but cannot establish real server or IAM semantics.
CI explicitly includes the new tests. Exact head / CI links are recorded in the
external verification pack after completion.

The six production consent/store/staging modules are unchanged from `6092748`.
The adapter does not add a production audit mode. The preserved manifest fixture
is generated synthetic run metadata only. Review `medical_consent_gcs_continue`
before the external operator's one-use grant/cleanup wrapper.

## Execution proposal requirements

Pin this source commit and all runner/test inputs, fresh 24-hour manifest,
separate new run ID, external operator digest and passing CI. Reuse the existing
SA, roles and buckets; temporary TokenCreator only, maximum one hour, removal
and effective-denial readback on completion/failure. One 20-minute fixture run;
no automatic retry. Resource creation and bucket IAM writes must be impossible
through the continuation operator. New explicit user approval is required.

Post-run: distinguish bounded scenario success from pending soft-delete expiry.
Preserve failed/new objects and audit for review; no compensating cleanup of the
old prefix. Production deployment/migration/scheduler activation remain separate.
