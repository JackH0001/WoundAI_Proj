# Independent WoundAILite review portal — design v1, 2026-10-23

Status (2026-10-24): standalone read-only candidate implemented in
`Backend/LiteReview`; local synthetic acceptance, still no portal deployed. No new
service, bucket, IAM grant, OAuth client or background task has been created.
This does not authorize cloud costs, changing existing services, deleting media
or releasing an App. The existing Lite candidate App Attest perimeter remains.

## First release and visible workflow

Independent branding: **WoundAILite 檢視管理**. Start with explicitly approved
internal reviewers. Initial proposed access is the project owner only, pending
verification of the Google subject during installation. MMH/Demo roles, JWTs,
sessions, account lists, URLs and cookies confer no portal access.

The intended first release is read-only with three views. The bounded candidate below implements a subset; it does not yet provide a complete live catalogue or deletion-history console:

1. **收件與同步**: paginated captures, synthetic/real source, immutable capture ID,
   research epoch, latest confirmed annotation revision, timestamp, image/depth
   receipt completeness and incomplete/pending status. Synthetic records stay
   separate from real research counts. No model inference or reupload is triggered.
2. **影像與標註核對**: same capture's RGB, original AI mask and each user revision;
   label provenance, generation/hash binding, source resolution/orientation,
   LiDAR surface area and camera projection area as separate fields. Missing data
   stay missing; no invented zero, sticker calibration, volume accuracy or
   training eligibility. RGB-D metadata can be checked, with a separate explicit
   permission for previewing media. No bulk/raw-data download in this first release.
3. **同意與處理狀態**: epoch-specific withdrawal/reconsent receipts, media payload
   disposition, retained tombstones/audit and pending cleanup. A new epoch never
   revives old media. Distinguish access blocked, current payload cleared,
   recoverable history, purge pending and scope unverified. Do not display
   “全部永久刪除” based on a list being empty.

Metadata-only `lite.review.metadata` and image-capable `lite.review.media` are
separate capabilities. Neither permits deletion, reconsent on someone else's
behalf, modifying annotations, promoting labels into clinician GT, running model
training, or changing budgets. Operational write actions require a later design,
explicit authorization and independent audit.

## Deployment and authentication boundary (proposed)

Use two private components so the browser-facing portal has no storage rights:

| Component | Proposed identity/access | Allowed data access |
|---|---|---|
| `woundai-lite-review` UI/BFF | `woundai-lite-review@…`, Cloud Run direct IAP | Only invoke the dedicated reader; no GCS role |
| `woundai-lite-review-reader` | `woundai-lite-review-reader@…`, Cloud Run IAM; only portal SA invokes | Lite media GET, derived Lite security-state GET, review-audit create |
| Existing Lite upload service | Existing `woundai-lite-runtime@…` | Existing App Attest routes unchanged |
| MMH / synthetic review | Existing distinct identities | No new Lite grants or routes |

All proposed SAs are in `woundai-jackh001`; the region remains `asia-east1`.
No new project-level storage roles, `allUsers`, `allAuthenticatedUsers`, runtime
impersonation or private key files. Distinct cookies/session namespace and
browser origin. Dedicated backend build allowlist: do not import medical Flask
bootstrap or enable its account/consent routes to reuse a console.

Direct Cloud Run IAP is the preferred entry, with a dedicated custom OAuth client
if required for the owner's non-organization Google account. Do not reuse MMH's
OAuth client or mutate its consent screen. Resolve organization/client eligibility
and authenticated subject before deployment. Use the verified signed IAP JWT,
not unsigned email headers: signature/algorithm, issuer, audience, time bounds
and application subject allowlist. The intended audience is
`/projects/421209514056/locations/asia-east1/services/woundai-lite-review`.
A Google login alone does not grant a portal role.

The reader independently verifies the Google-issued service identity token for
its exact audience and expected portal SA. The portal forwards the original signed human IAP assertion through that authenticated hop. The reader verifies it again and generates its own request ID and fixed purpose; the browser cannot choose/override those values. Do not treat arbitrary forwarded headers
as authentication. No browser-direct signed GCS URLs, bucket credentials or state
records. Any future mutation must add CSRF and origin defenses as well as RBAC.

## Exact storage scope

Existing Lite media: `woundai-lite-media-421209514056`.
Existing Lite state: `woundai-lite-security-421209514056`.
Proposed separate access-audit bucket: `woundai-lite-review-audit-421209514056`;
policy/retention must be reviewed before creating it; do not claim WORM unless
configured and independently verified. Audit writes create-only; no delete.

The actual fenced object format is important:

- Legacy enrollment: `lite_fenced/v1/<owner>/<kind>/<identifier>`.
- Renewed epoch: `lite_epochs/v1/<epoch>/lite_fenced/v1/<owner>/<kind>/<identifier>`.
- Security state: `lite_security/v1/<derived-key>.json`, including distinct
  derived keys for write fences and reconsent pointers/audit.

`WOUNDAI_GCS_PREFIX=lite-public-v1` is a service configuration value, **not proof
that all current fenced objects are under that prefix**. Use production
`FencedObjects`, `FencedManifest` and `Reconsent` derivation/validation rules.
The old `/api/v1/lite/records` global-ledger reader and `lite_research_archive.py`
legacy archive are not sufficient for current owner/epoch-scoped fenced data.
Do not expose those endpoints by weakening the public Lite service perimeter.

Reader IAM: media `storage.objects.get` only; security `storage.objects.get`
only (no LIST); review audit `storage.objects.create` only. No writes/deletes on
source buckets, no bucket administration, no MMH/Demo read permissions or keys.
Application adapters expose only read methods and only derived state keys.
Because security objects share hashed names in one namespace, IAM cannot encode
which hash represents a fence versus App Attest state. The reader's GCS GET
privilege is broader than its API capability. This residual boundary must be
reviewed and negative-tested; do not claim state-kind isolation at the IAM layer.
If that scope is unacceptable, separate consent read-state into a purpose-specific
store with a migration plan; never grant the browser-facing SA access instead.

## Correctness and withdrawal before rendering

- Every record identity is `(owner, epoch, image_id)`. A revision alone or an
  image ID without owner/epoch is insufficient. Join only matching receipt and
  source generation/hash. Conflicting duplicates are quarantined, not arbitrarily
  resolved. AI mask and lay mask never overwrite each other's provenance.
- Resolve current consent pointer plus its linked audit; verify the requested
  epoch and its write fence using fresh state. Missing/corrupt/unavailable state
  means no image or metric display. A newer active epoch does not authorize
  an older withdrawn epoch. This review path must not create any state.
- Buffer and validate one bounded response; re-read the consent/fence revisions
  and current object generation before release. Change => 409/refetch, withdrawn
  => inaccessible, unknown => 503. Never fall back to a historical generation if
  the live one has been sealed. Check stored hash, byte count, type and dimensions.
- No long-lived positive consent cache, CDN/image cache, service-worker cache or
  offline export. Return `Cache-Control: private, no-store`, prevent embedding,
  use a restrictive CSP and escaped text. The authorization point is the final
  state check: already rendered bytes cannot be recalled, and a withdrawal just
  after that check cannot retroactively erase an in-flight response. Test and
  document this concurrency boundary rather than promising impossible revocation.
- Create an attributable, minimal access-audit receipt before media release;
  audit failure blocks media viewing. Do not log image bytes, masks, bearer
  tokens, Apple challenges or raw security objects. Trace by opaque IDs.
- Pagination: 50 records/page, a signed cursor bound to filters, source epoch and
  viewer permission. No full-bucket/whole-study scan per HTTP request. A derived
  catalogue may aid discovery but never authorize viewing or override fresh
  consent. Bootstrap/catch-up is a separately bounded, restartable read job;
  ambiguous ownership and catalogue lag are visible, not invented completeness.

## Required acceptance before a real review entry

- No login, wrong IAP audience/issuer/signature, expired token, unlisted subject,
  MMH/Demo JWT and forged identity headers denied before data access.
- UI SA cannot read any source bucket. Reader cannot read MMH/Demo, write Lite,
  list security state, impersonate existing runtimes or modify bucket policy.
  Check inherited IAM and effective direct permissions, not just bucket names.
- Legacy + renewed epochs; unchanged revision replay; stale annotation; labels
  from another owner; consent version upgrade; sealed data and current generation
  mismatch; reconsent racing a view; failed state/audit reads all fail safely.
- Pagination/cursor tampering and source updates; browser back/reload after
  withdrawal; no cache reuse; no new inference charge or RGB-D upload on view.
- Cloud audit evidence and targeted cross-identity denial tests must pass before
  authorized people view real research data. Synthetic fixtures first.

This is distinct from the medical `consent-smoke` cleanup acceptance plan. Both
preserve original evidence; neither authorizes production deletion or publication.

## Implemented bounded first version (2026-10-24)

The initial reader consumes a protected, digest-pinned catalogue (max 2,000
records), expires it after 24 hours, and displays its snapshot boundary. It does
not have media LIST in HTTP or claim newer revisions are already indexed. A
separately reviewed bounded inventory supplies the catalogue;
`tools/build_lite_review_catalogue.py` validates exact pins locally without GCS
calls. A future continuous catalogue job is not introduced here. Separate
catalogue operator LIST/GET permission is not granted to the reader.

Runtime validates its exact project/service/metadata SA. Both hops verify the
same signed IAP assertion; the reader also verifies the portal service token's
audience, numeric subject and email. Account email alone grants no access.
Media reads use a fresh unversioned SDK Blob plus a generation precondition:
reusing a reloaded Blob would select its historical generation.

The view supports original AI and individually selectable in-snapshot user polygon overlays,
revision summaries, separate surface/projected areas and storage pins. RGB-D
receipt/hash binding is storage evidence only; raw depth geometry accuracy is not
re-certified by a dashboard request. Source `synthetic` is an operator assertion
in the reviewed catalogue, otherwise `unverified`; neither means training-ready.

See `Backend/LiteReview/README.md`, `tools/plan_lite_review_rollout.py` and the
synthetic two-hop/JWT/fence tests. Live IAP/IAM, verified owner subject, OAuth
client scope (must not mutate MMH project settings), access audit and catalogue
inventory require a separate fixed-image deployment/acceptance review.

References checked 2026-10-24:
[direct Cloud Run IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run),
[IAP signed JWT validation](https://docs.cloud.google.com/iap/docs/signed-headers-howto),
[GCS object listing permissions](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/list).

The outbound hop uses `X-Serverless-Authorization` for Cloud Run IAM plus the full signed token in `Authorization` for reader verification; the platform may strip the consumed header signature. [Google service-to-service guidance](https://docs.cloud.google.com/run/docs/authenticating/service-to-service). This is unit-tested; real two-hop ingress remains a deployment acceptance gate.
