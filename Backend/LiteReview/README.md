# WoundAILite 檢視管理 — independent read-only candidate

Implemented locally; **not deployed**. No new cloud identities, OAuth settings,
buckets, IAM grants, migrations, cleanup jobs or store releases are performed.
The upload service's App Attest perimeter and MMH/Demo runtimes are unchanged.

Two factories: `Backend.LiteReview.runtime:portal()` (IAP-protected HTML/BFF)
and `Backend.LiteReview.runtime:reader()` (private IAM service). Both require an
exact Cloud Run service/project/runtime identity. Reader independently verifies
the original signed human IAP JWT and the portal SA's Google ID token; forwarded
email/subject headers and MMH/Demo tokens are not accepted. Each service uses the
same explicitly reviewed subject/capability map; no default owner or role exists.
An explicit empty `roles: {}` is a closed bootstrap/containment mode: even a
valid signed IAP token is denied at both hops, before source reads or audit writes.
Missing/malformed roles are still configuration errors. Empty roles do not enable
a development bypass or public health endpoint.
The portal identity has no GCS access. Reader source access is GET-only;
separate access audit is create-only. Actual IAM/inheritance remains a live gate.

## Supported first release

- 50 records/page; signed cursors bind viewer, permissions, source filter,
  snapshot digest and expiry. No bucket or whole-study listing in an HTTP request.
- Exact `(owner, epoch, image)` joins and generation/size/SHA-256 pins. Current
  consent pointer, linked renewal audit and epoch fence are freshly read.
- AI and user polygons remain separate. User labels stay `lay`, never clinician
  ground truth. Surface and projected area are separate, with no inferred zero,
  sticker-calibrated area, volume accuracy or training admission.
- Optional JPEG/SVG overlay requires `lite.review.media`. Metadata role gets no
  pixels or polygons. There is no raw RGB-D download or operational write route.
- An access audit is acknowledged before release; source generations and
  consent are checked again. Withdrawing during reads/audit blocks the response.
  Data already delivered cannot be recalled; a withdrawal after the final check
  does not retroactively remove in-flight bytes. No browser/CDN cache is allowed.
- A corrupt subject is visibly blocked without hiding healthy listed subjects.
  Missing fence is unknown, never inferred initial consent. Completed reconsent
  validates its own stored versions, not the current mobile disclosure constant.

## Discovery snapshot and configuration

Initial catalogue is a protected mounted JSON snapshot, not a mutable global
ledger. Schema `lite-review-catalogue/1`: integer `captured_at`, `records` (max
2,000). Each record contains `owner`, `epoch`, `image_id`, `source` (`synthetic`
or `unverified`), and `objects`. Objects contain `kind`, `identifier`, current
`generation`, `bytes`, `sha256`; image + metadata are required, RGB-D optional,
up to 100 bounded label receipts. Unknown source is never classified clinical.

A separately authorized **bounded read-only inventory** supplies those pins;
the HTTP service cannot discover arbitrary bucket objects or derive App Attest
keys. `tools/build_lite_review_catalogue.py` validates a reviewed input digest,
creates a new mode-0600 snapshot and refuses overwrite. It does not fetch GCS or
certify completeness. New captures/revisions after the snapshot are explicitly
not included; refresh is an operator task. Runtime refuses snapshots older than
24 hours, including in a long-running process. Old records remain under fresh
consent controls. Do not publish a real catalogue or roles file in Git/Claude ZIP.

`LITE_REVIEW_CONFIG` points to protected JSON with exactly:
`roles` (verified Google subject -> capability list), `reader_url`,
`portal_subject` (verified SA numeric subject), `catalogue_path`,
`catalogue_sha256`, `cursor_key_path` (32 random bytes). No sample real credentials
or permissive fallback. Backend URL is limited to the dedicated reader service.
The reader digest must match the mounted catalogue. Both service identities,
the IAP OAuth client scope, human subject and effective IAM need live verification.

Build context is repository root with `Backend/LiteReview/Dockerfile`; explicit
COPY and a minimal hash-locked dependency subset exclude inference models,
medical bootstrap and all research data. The reader deployment must override
the WSGI factory to `reader()`. No deployment commands are issued by the planner.

## Migration and cleanup boundaries

`tools/plan_lite_review_rollout.py --output <new-path>` requires a clean commit
and emits a fixed-source **review-only** JSON. Lite catalogue creation is not
medical consent-index migration. Medical backfill applies only to individually
reviewed MMH/Demo scopes, with paused/drained writers and additive receipts.
The proposed medical cleanup job must never target Lite epoch storage: Lite's
permanent generation-fence markers are not cleanup targets. No default retention
period is chosen and the proposed Scheduler remains absent/paused until approved.

Run `python -B engineering/phase2/test_lite_review_portal.py`. Tests use actual
ES256 signatures, two Flask hops, the existing production fenced writer/renewal
format, and a generation-aware synthetic bucket; no credentials or network.
These tests do not prove deployed IAP/IAM, real catalogue completeness, cloud
latency or physical erasure. Synthetic GCS consent evidence from 28c350e remains
separate and preserved; natural seven-day expiry is still pending.

The portal sends `X-Serverless-Authorization` for platform IAM and preserves the signed `Authorization` token for reader verification. Only a live synthetic acceptance can confirm the deployed header path. Role-map changes require a new protected configuration/revision; IAP revocation is bounded by token validity, not instantaneous.
