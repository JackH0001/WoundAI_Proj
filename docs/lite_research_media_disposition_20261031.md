# Bounded research current-media disposition

Parent source: `9dd82ee772e79a928419f9d1598d8572c694d773`. This is an
explicit private worker and synthetic acceptance change. No live cloud media,
identity, key, IAM, schedule, store build or subscription product is changed.

## What changes

The previous account worker retires linked research admission but deliberately
leaves every payload intact. The existing independent research withdrawal helper
can seal payloads, but lists the entire owner namespace twice and seals all
objects in one call. This change does **not** replace that public withdrawal
path. It provides a bounded, restartable account-deletion media lane instead.

`ResearchMediaDisposition` requires actual account-closure and research
authorities, separate job/audit stores, the original frozen grant inventory and
verified registration/account-token linkage. `advance(..., apply=True)` is an
explicit private operation; status reads cannot start it. No guessed association,
arbitrary prefix, anonymous-ID reset or direct public cleanup route is added.

1. Finish retiring all verified installations before processing media. Admission
   advances at most five frozen grants per call. Unresolved associations stay
   unresolved; they do not justify deleting another namespace.
2. For one linked grant, bind the worker to the retirement generation/hash and
   traverse the original consent-epoch hash chain, newest first. Original consent
   events and disclosure versions are neither rewritten nor recreated.
3. Close each epoch and durably freeze the pending-write inventory **before**
   sealing. At most 20 distinct planned names are sealed per call (test limit 4).
   Acknowledgement/finish races may remove plans but cannot introduce new ones.
4. Once plans are empty, fetch only one provider page, with `max_results` and
   `page_size` both bounded. Persist the opaque continuation and last object name;
   short or empty pages with a continuation are not completion. Scope, lexical
   order, duplicate names and token cycles are checked, including after restart.
5. Save an immutable intent before changing each current payload. Replace it
   with the existing permanent generation-fenced empty marker, then confirm that
   exact marker and save the outcome. Never delete the marker: an old pinned
   write must remain unable to recreate the payload.
6. Persist the next cursor only after outcomes are confirmed. Lost write replies,
   lost checkpoints and competing workers replay/join original evidence rather
   than creating new cleanup operations or skipping a page.

The limit bounds logical names and a single page per call, not an overall wall
clock deadline or the number of network attempts. Underlying object sealing has
its existing bounded generation-conflict retries and 10-second provider timeouts.
The future operational executor still needs a total runtime budget and monitored
retry schedule. A permanently rejected/expired page token stops for reviewed
reconciliation; this version does not discard the cursor and silently restart.

## Private composition and outward status

`DispositionProgress.attach_research(worker)` explicitly attaches a worker using
the same `AccountDeletion` authority and separate storage scopes. Default
construction remains detached. Public status reads remain read-only; the private
progress worker invokes one bounded research advancement before its other lanes.
The shared progress module defines an abstract internal lane contract; it never
imports the concrete research implementation. This keeps the native and key
worker packages closed without adding research modules to their allowlists.
The explicit composition is trusted application code, not an authorization
boundary accepting arbitrary caller-supplied Python objects or module names.
If that advancement cannot confirm original evidence/provider state, it stops
that invocation with the original cursor intact. Independent component workers
remain available, but no production retry/orchestration policy is activated here.

The existing public v2 deletion receipt and iOS pending-lane contract are unchanged.
Even after current research payloads are sealed, the outward research/history
lanes remain pending. The new internal receipt distinguishes an examined frozen
grant list from all linked epochs sealed and always retains:

- `account_deleted=false`;
- `historical_versions_erased=false`, `physical_backups_erased=false`;
- `writers_drained=false` (fenced writes do not prove old processes are dead);
- `consent_and_audit_preserved=true`.

Counts are grant entries, not patients or unique devices. Repeated grants for the
same installation join one owner checkpoint. A finished receipt records the
verified observation; it is not a fresh integrity scan on every status request.
An unresolved association prevents an all-linked-media completion claim. The
future reconciliation procedure must not rewrite that original frozen inventory.

## Verification and before/after interpretation

`tools/lite_research_media_probe.py` can run unchanged on the parent and candidate.
It creates 73 synthetic current objects and an unrelated owner, retains an old
pinned replacement, and uses real SQLite CAS/journals with a generation-aware
fake provider that deliberately retains history. The parent admission worker
leaves all 73 objects; the probe **separately invokes** its existing withdrawal
helper to measure the unbounded alternative. This is not a claim that account
deletion previously dispatched that helper automatically.

The parent helper seals 73 names in one call and performs two full lists. The new
worker, restarted every call, finishes the same verified namespace in 22 calls
with at most four seals and one bounded page per call. The count includes two
frozen grants, one intentionally unresolved; full deletion correctly stays false.
Both runs preserve original historical generations, unrelated owner data,
account identity, quota and original captures. The old pinned write is rejected;
no model, Apple, KMS or live GCS call occurs.

The regression suite also covers 19 consent epochs, 13 verified installations,
27 pending planned names, response loss before/after checkpoint commitment,
two concurrent workers, missing/corrupt audit, empty/short pages, token cycles,
an actually installed Google SDK's bounded request parameters (mock transport),
and the optional private progress attachment. Test logs and the unchanged probe
are included in the local Claude packet; independent replay remains required.

Local acceptance: full Lite **1,896 passed**; the workflow's Python test/tool
replay **1,864 passed across 66 commands**. The sets overlap and must not be
added. The 40 new tests are already included; the focused new-module/package
replay passed 56 tests. Initial full regression found two package dependency
errors because the shared progress module imported the concrete research module.
Those logs remain in the packet. After replacing that dependency with the
explicit private lane contract, both complete suites were rerun successfully;
no package allowlist was relaxed. This is local workflow replay, not remote CI.
No iOS/Android implementation changed and neither was rerun in this local batch.

```text
python -m unittest -v test_lite_research_media_disposition
python tools/lite_research_media_probe.py --output research-media-probe.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

## Remaining release gates

This worker intentionally retains original media generations in the synthetic
provider and never deletes historical versions, soft-deleted objects or backups.
It does not establish a legal retention basis or complete deletion compliance.
Historical research disposition, minimized identity/subscription/audit retention,
unresolved association reconciliation, public progress detail and independent
legacy withdrawal pagination still require work. So do the private runtime and
package binding, least-privilege bucket/IAM checks, runtime budgets and failure
recovery, real GCS/Apple/KMS and device acceptance. The native/key-worker images
are not granted research-media access or automatically enabled by this change.

Failure recovery means stop and resume the saved original cursor after evidence
is restored. It never means restore deleted payloads, reopen retired research,
erase audit or downgrade terminal consent state. No live rollback/cleanup is
authorized by the tests or this document.
