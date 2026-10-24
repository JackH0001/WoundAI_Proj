# Claude follow-up: Lite perimeter, independent review plan and smoke budget

Baseline: `f222cbc592ee6c1ffc3d05abc9125b0dedf3df38` (all 8 CI runs completed
successfully, including Android/iOS). Exact new source is pinned by the external
handoff manifest. PR #35 remains draft; no merge or deployment.

## Scope of this increment

- Found and reproduced two missing public-perimeter routes: signed research
  rejoin and epoch-scoped withdrawal returned `404 lite_route_not_supported`
  before reaching their otherwise passing App Attest/business tests. Added only
  the canonical POST rejoin and DELETE owner/epoch routes. No authentication,
  owner binding, epoch fence or medical/reviewer route was relaxed.
- Reconsent tests now install the perimeter before initial device registration.
  A separate DELETE regression arranges renewal directly, so a broken POST
  cannot obscure the missing DELETE route. Actual application startup verifies
  unsigned rejoin/epoch DELETE return 401, and malformed paths/methods remain 404.
- The completed console fix is retained: absent Lite reviewer routes hide the
  tab even for admins, and old bookmarks do not issue Lite requests. No runtime
  permission was loosened. MMH/demo UI deployment remains a separate operation.
- New [Lite review portal contract](../engineering/contracts/lite-review-portal-v1.md)
  specifies independent IAP human authentication, dedicated UI/reader identities,
  read-only v1, fresh owner/epoch consent fencing and source/hash/revision joins.
  It explicitly rejects copying the old global-ledger reader into the standalone
  Lite service. Current fenced media are not all under `lite-public-v1`.
- No portal implementation/HTTP endpoint or cloud policy is claimed complete.
  In particular, hashed shared security-state names cannot be selectively gated
  by IAM state type. The proposed narrow reader API and GET-only security grant
  leave a documented residual service trust boundary requiring review.
- Fixed a smoke-run scope-accounting gap: `max_writes=1000` previously counted
  uploads only, while pinned deletes consumed the wider SDK-call budget. Deletes
  now share the same mutation budget as uploads. Two regressions prove one
  delete consumes a slot and the next mutation at the cap leaves media intact.
  Production cleanup algorithms and deployed services are unchanged.

## Local verification

- Before the route change: two targeted HTTP regressions both returned the
  erroneous 404 (not a signature failure).
- Lite suite: **454 tests + 475 subtests passed**. Includes real synthetic
  cryptographic assertions, unchanged raw RGB-D/revision retries, stale old
  withdrawal versus renewed data, and withdrawal of the new epoch.
- Medical consent/operations and console selection: **167 tests + 124 subtests
  passed**. This includes smoke scope/budgets and the capability-gated console.
- Focused public-perimeter/reconsent/console/smoke suite: **63 tests + 188 subtests
  passed**, overlapping the totals above; do not add these as unique tests.
- These are local synthetic transports and HTTP tests, not live GCS mutation or
  TestFlight reconsent acceptance. CI for the new commit is tracked separately
  from the completed baseline in the external report.

## Evidence and review limits

The source bundle contains only code/docs and synthetic test evidence. A separate
read-only Policy Troubleshooter report checks the three deployed runtime
principals against the eight configured buckets (own reads and cross-service
get/list/create/delete denials). Its completeness/counts and any UNKNOWN results
must be read from the timestamped external report, not inferred from this design.
No token minting as a runtime, media reads, IAM changes or role creation were used.
This is not a full impersonation graph, group-membership or project-owner proof.

Please review: two-service boundary, IAP audience checks, the remaining hashed
state IAM limitation, stale catalogue handling, privacy check timing, generation
fallback refusal, and independent mask/area provenance. Inspect the budget change
and negative test without treating offline success as live GCS acceptance.

Deployment plans are intentionally separate: (A) synthetic medical consent
resource provisioning + bounded test-media cleanup, (B) existing MMH/demo console
revision, (C) new Lite review infrastructure. None is covered by an old one-time
build approval. No resource creation, cleanup schedule, real-media deletion,
external testing/public App release or review-portal activation occurred.
