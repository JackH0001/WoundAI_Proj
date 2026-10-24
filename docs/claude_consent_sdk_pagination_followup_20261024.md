# Claude follow-up: real SDK pagination failure (2026-10-24)

Baseline `7df6037487a251a30a4737a310ee040747fbe1bf`, PR #35.
The candidate is the commit containing this document. This is an acceptance-tool
fix, not an application deployment or a claim that cloud acceptance passed.

## What happened in the authorized single attempt

The approved review `69c9b77186411d46ef772846b7126204043e718b055590f93767b94bdf3f89b4`
ran once. Both dedicated synthetic buckets passed executor identity, bucket
policy, effective-permission and empty-bucket readback. Only the planned audit
bucket was created and given its dedicated binding; media IAM was unchanged.

The fixture then stopped before its first scenario assertion with `ValueError`.
The runner recorded 2 attempted immutable uploads, 2,329 attempted upload bytes,
41 SDK operations/page counters, and 4.87 seconds. These are instrumented SDK
counts, not a billing/HTTP trace. The run claim and first synthetic media fixture
were reached; no cleanup scenario ran. Do not report any withdrawal, reconsent,
race, migration, deletion or expiry case as a live pass.

The temporary TokenCreator binding was removed. Effective `getAccessToken`
denial was confirmed at 08:38:44 UTC, separately from policy removal. Existing
MMH, Demo and Lite service snapshots were unchanged. Already issued tokens have
a maximum 600-second lifetime; binding removal is not immediate token expiry.
No automatic retry, teardown, schedule or store release was performed.

## Reproduction and fix

`ScopedClient.list_blobs` previously evaluated:

```python
pages = iter(raw.pages) if hasattr(raw, 'pages') else iter([raw])
```

Google `HTTPIterator.pages` sets `_started` on property access. `hasattr` performs
that access, so the second access raises `ValueError("Iterator has already
started", ...)`, even before a list HTTP request. The former offline double
returned a list, and the earlier real SDK test covered bucket preflight only.
It never traversed this scoped listing path. This explains the coverage gap.

The fix retrieves `pages` once, preserving lazy page iteration and the existing
budget checks. It does not catch/ignore pagination errors, remove scope checks,
alter IAM or modify the production consent/cleanup/storage implementation.

Three added regressions fail on the old code and pass on the fix:

1. Real Storage SDK, two pages: exact prefix/query, string-to-integer generations,
   page budget and observed-generation inventory. Anonymous credentials and
   intercepted requests only.
2. Real SDK second-page denial: fail closed, never treat it as a complete empty
   listing.
3. The entire existing scenario suite and object readback using Google's real
   single-use lazy iterator, with fake object I/O and multi-page responses.

The initial test fixture assumed Python `page_size` serialized as REST
`pageSize`; the SDK correctly uses `maxResults=min(page_size, remaining)`.
That test assertion was corrected separately, with the first log retained.

## Validation and remaining gates

Local result: **151 passed, 68 subtests passed** (33 smoke tests included).

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

Fresh candidate CI is tracked separately. The failed run's source pins, review,
authorization, preflight, partial result, IAM evidence and prior artifacts stay
immutable in the external review package. No patient material is included.

The original buckets are now nonempty. Do **not** delete their run claim, rerun
the old manifest, relax empty-bucket validation or invoke tests next to a live
authorization file. Before another live attempt, prepare a separately reviewed
continuation with exact preserved-object readback and isolated new write scope,
or new dedicated resources, then obtain new authorization. No such follow-up
cloud execution is authorized by this stopped single attempt.

Seven-day natural soft expiry, formal migration/cleanup scheduling and Lite
TestFlight reconsent acceptance remain separate unverified gates. Existing
application services and store releases are unchanged.

References: Google API Core `page_iterator.Iterator.pages` in the installed SDK;
[Python iterator documentation](https://googleapis.dev/python/google-api-core/latest/page_iterator.html);
[GCS soft delete](https://docs.cloud.google.com/storage/docs/soft-delete).
