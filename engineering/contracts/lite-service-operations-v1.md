# Lite service-only operation protocol v1

Status 2026-10-26: opt-in backend implementation, synthetic signed HTTP and
localhost tests only. Application startup does **not** pass `commerce`, so these
routes remain absent in the shipped configuration. No deployment, native client
cutover, real Apple purchase, ads, identity migration or cleanup occurred.
An opt-in native journal/client is now implemented and tested in the iOS target;
the shipping measurement UI still does not instantiate or call it. See
`lite-native-service-v1.md` for native retry and data-preservation rules.

## Purpose and trusted boundary

Recognition service use and optional research participation are separate.
Research withdrawal still fences the existing research media/annotation routes;
it does not reset the service allowance or authorize creation of another account.
The new routes cannot receive depth, consent epochs, a paid flag or a research
subject. Their dependencies include no research media writer.

Every call uses the existing App Attest registration, fresh signed challenge,
monotonic assertion counter, exact request body and installation owner check.
The existing general request budget also applies to control and media traffic.
Signature retries need fresh security tokens but the **same operation ID**.
The withdrawal lane remains separate. Optional route matching checks both the
canonical path and the resolved Flask endpoint; adding a URL alone cannot bypass
the research fence. `ServiceAccounts` is trusted create-only provisioning, not a
public account registration endpoint. App Attest installation identity alone
does not establish a unique person, service account or purchase owner.

## Calls and states

All are POST under `/api/v1/lite/service/`, with `Cache-Control: no-store` on the
blueprint response. Error responses from the upstream admission guard retain
that guard's contract. Never include media or purchase tokens in error text.

| Path | Exact input | Result |
| --- | --- | --- |
| `prepare` | JSON `anon_id`, `operation` (32 lower hex), `image_sha256` (64 lower hex) | Validated service account; current grant; durable original-day mapping; reserve before sending image |
| `run` | multipart text `anon_id`, `operation`; sole file `image` | Verify original JPEG hash; decode/privacy check; claim once; run injected model; persist bounded result then settle |
| `status` | JSON `anon_id`, `operation` | Read the same receipt/result, or reconcile a durable result whose settlement response was lost; never run model |

Control bodies are at most 4 KiB. The run's upstream signed body is bounded by
the existing 32 MiB cap; its JPEG is separately bounded to 8 MiB, 16 million
pixels, 8192 per side, EXIF orientation 1. Native clients must normalize before
hashing. The signer must sign the exact multipart bytes sent.

States are `awaiting_reservation` (retry prepare), `ready` (run once), `pending`
(status only), `completed`, and `cancelled`. A completed result includes image
dimensions, bounded polygons, route and the existing model result convention;
the field named `confidence` preserves the existing mask-occupancy convention,
not a calibrated probability or an accuracy claim. It is not a clinical quality
gate. No surface/volume accuracy is inferred from service completion.
Completed replies also carry `result_json`, the exact canonical UTF-8 JSON text
whose SHA-256 is the receipt's result hash. The client checks those original
bytes and equality to the decoded `result`; it does not reserialize floating
point coordinates to try to reproduce a Python digest.

- Pending returns 202 / Retry-After 5. Unknown persistence or execution returns
  503 with `retry_action=status`; do not silently make another operation.
- Quota/capacity returns 429. Quota reservations count toward free 2 or Plus
  total 10 daily successes; attempts retain their separate 30/day bound.
- Invalid/ambiguous input or changed image/model returns 409; no replacement.
- Unknown operation returns 404 / `lite_operation_unknown`. A native client may
  repeat prepare with the same ID/hash only if it has no pinned request receipt.
- Rejected preflight image returns 422; no model, unstarted success reservation
  cancelled, attempt and cancellation receipt retained.
- Expired result read window returns 410 and `keep_local=true`; do not rerun.

Daily reset uses the existing server UTC day. The operation lookup is keyed by
service subject and operation, **not the current day**. A job prepared before
midnight keeps its original reservation and day after midnight. If reservation
did not finish before midnight, it cannot silently become a new-day reservation.
No implicit zero baseline is created on missing/malformed state. Provisioning
and verified daily baseline initialization are prerequisites, not user requests.

## Persistence, retries and the remaining privacy gate

The service account, quota ledger and result store are distinct injected stores.
Local tests use SQLite; cloud deployment must use durable CAS storage, never
ephemeral Cloud Run disk. A raw JPEG is processed in memory by this component;
no raw depth is accepted. Result polygons and operation hashes **are persisted**.
They are service-result state, not long-term research enrollment.

`RESULT_TTL=86400` currently limits result **reads**, not physical retention.
No purge implementation or data-lifecycle approval is supplied in this change.
Deployment must wait for explicit service-cache disclosure, bounded result and
provisional-operation cleanup, race tests, and sufficient durable tombstones to
prevent an old operation ID being reused after payload purge. Deleting the entire
mapping is not a safe TTL implementation. Research withdrawal currently concerns
research data; service-cache erasure and audit retention need their own contract.

Persist-result then settle permits response-loss recovery by status, including
after a process restart. Reusing run still uploads the HTTP body although it
does not execute/charge twice; avoiding image bandwidth requires the native
client to use status. The 96-byte status body in the probe is a synthetic example
and excludes challenge/assertion headers, TLS and response bytes.

Claim response loss or a model failure without a saved result remains pending;
there is deliberately no automatic takeover or invented failure/refund. Before
rollout add a bounded, auditable operator reconciliation process so such a job
does not leave the user waiting indefinitely. Reservations prepared but never
run also need cancellation/expiry reconciliation. Concurrent prepares can leave
provisional mappings that never received quota; cleanup must distinguish them.

## Cutover prerequisites

1. Implement verified service-account enrollment/restore, paid activation and
   account linking, with native appAccountToken and immutable ownership audit.
   The current provision method cannot change a free binding into a paid binding.
2. Persist the native operation ID, normalized image hash and returned day before
   upload; recover through status after response loss/relaunch. Add clear
   local-saved / waiting / confirmed UI without resetting research consent.
3. Wire service results to separately consented research annotation/depth writes
   without rerunning recognition or charging twice. Fixed 50% enrollment stays
   off until the separate recruitment contract is validated.
4. Freeze/migrate the old allowance, initialize verified new-day baselines, and
   disable or adapt legacy recognition through the same authority. The old
   `/segment` default five/count-append race remains; do not enable both and
   accidentally grant old five plus new two. Failure must close new admission,
   retain receipts and reconcile, not revert to an empty legacy balance.
5. Implement cache cleanup/tombstones and unknown-job reconciliation above.
6. Verify real Apple Sandbox purchase/renewal/refund/restore and signed renewal
   grace policy. Current tests use Apple's real verifier on synthetic chains and
   mocked Server API status, not an Apple-hosted transaction.
7. Complete least-privilege cloud isolation/latency/billing and bounded capacity
   acceptance under a new fixed-source authorization. No such action is here.

## Replay and evidence boundaries

From checkout root, install pinned test dependencies in the manual workflow and
set `PYTHONPATH=tools:engineering/phase2:Backend/Flask:.`:

```sh
python -m unittest -v test_lite_service_http test_lite_service_loopback
python tools/lite_service_flow_probe.py --output /tmp/lite-service-flow-probe.json
```

38 coordinator/Flask tests plus one real localhost HTTP test are new. Concurrency
tests contend at the coordinator/CAS boundary rather than reorder App Attest
counters. HTTP signatures, owner mismatch and security replay are tested
separately. No AI segmentation accuracy or live-cloud stress result is claimed.
The probe uses the actual GCS adapter with fake transport: prepare+run causes
5 conditional write attempts/37 reads, completed status 0 writes/10 reads.
App Attest, budgets, Apple/OCSP and retention costs are excluded from these counts.
The inherited financial sensitivity assumptions are not a new tariff quote.
