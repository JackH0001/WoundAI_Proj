# Claude d642235 review follow-up — local stage, 2026-10-26

Base: `d642235e0d0df45d1daa440b8c582a5745bb4009` on
`codex/lite-commerce-cohort-20261025`. This stage does not deploy, enable commerce,
alter cloud IAM/quotas, clean existing media, or publish an App. Source changes
and synthetic tests only; original research data and audits remain untouched.

## Fixed and replayed

| Review item | Before | After |
|---|---|---|
| B1: optional imports break the legacy build | Packaging suite: 7 pass, 1 error at missing `lite_service_operations.py` | 10/10 packaging tests pass; isolated subprocess loads only the exact legacy allowlist and starts its gate with all commerce modules absent |
| G4: duplicate entitlement query | Same signed synthetic paid prepare/run: 2 Apple status API calls | 1 call while the grant remains valid; completed prepare/status replay adds 0 calls |
| ba970b1/N3: Windows local inventory | Windows-formatted fixture finds 0 of 8 existing synthetic files | 8/8 match POSIX inventory, with matching hashes and all input files preserved |

B1 keeps the explicit deployment file list unchanged. Imports are behind the
default-off `commerce is not None` branch. The static import validator exempts
only the two reviewed direct imports inside that exact guard in that exact
default-None function; it still rejects unguarded, inverse-guard, else-branch,
other-handler and unrelated missing imports. The separate staged-subprocess
test is essential: AST acceptance alone is not an image startup claim. Missing
dependencies with explicit commerce injection still fail closed.

G4 does not cache a paid entitlement across requests. A delayed preflight past
the grant expiry queries again; a refund during that delay blocks the third
recognition after two previous successes. Failed reservation retries fetch a
fresh grant, and existing receipts remain readable without querying Apple.
Tests use Apple's actual verifier with signed synthetic chains and fake API
responses; no real purchase, Apple latency or monetary saving was measured.

N3 is a portable-path regression test on macOS, not native Windows execution.
It calls the actual inventory with `PureWindowsPath` relative-path results;
no real media deletion or cloud cleanup was performed.

## Verification

- Full `engineering/phase2/test_lite*.py`: **666 tests passed**.
- Medical order/rollout/operations/GCS-smoke selections: **125 passed**.
- Local replay of the updated Lite prelaunch workflow: **10 shell steps passed,
  633 tests across 9 groups**; counts overlap the suites above and must not be
  added together. Actions checkout/setup/install/upload were not executed;
  the existing recorded Python environment was used.
- Four new review-gap characterization tests reproduce G1/G2/G3 and assert the
  reduced G4 query count. They are not claims that G1/G2/G3 are fixed.
- `git diff --check` passed. No iOS/Android source changed in this stage; no new
  Swift/Android build, Docker model-image build, live GCS or TestFlight test is
  claimed. Prior branch-wide mobile changes still need the stack PR checks.

The workflow now includes packaging and isolated startup, the review probes,
and a path-filtered PR trigger on Ubuntu. **Remote CI for this new commit has
not run**; d642235's earlier push-only scan/integrity checks are not substitutes.

## Still blocked, with reproducible evidence

`tools/lite_commerce_review_probe.py` writes JSON without patient data:

- G1: next day without an approved baseline returns 503; model calls 0.
- G2: two prepares with no run give used=0, pending=2, third prepare=429;
  model calls 0. No automatic slot cancellation is enabled yet.
- G3: pre-midnight reservation failure returns 503; after midnight prepare/run
  return 409 while status says awaiting reservation. Still unresolved.
- G4 remaining: billing grace policy and product/policy-link migration remain
  blocked. This stage fixes query duplication only.

Implementation order, atomicity requirements and acceptance cases are in
[`lite-commerce-rollout-gates-20261026.md`](../engineering/contracts/lite-commerce-rollout-gates-20261026.md).
In particular, a single "no reservation" read cannot authorize a new cross-day
attempt: a late pre-midnight CAS must be fenced first. No fixed quota reset or
automatic rerun is proposed for unknown running work.

All six old ba970b1 items were recovered from the supplied report: N3 is fixed
here; N1/N2/N4/L1/L2 remain open with explicit acceptance requirements. They are
not silently covered by the older consent-version fix with the same N1 label.

## Reproduce and hand off

Use dependencies pinned in `.github/workflows/lite-prelaunch.yml` and
`engineering/phase2/requirements-commerce-test.txt`. From the full checkout:

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=tools:engineering/phase2:Backend/Flask:.
python -m unittest -v test_lite_build_context test_lite_service_http test_lite_service_subscription_links test_lite_commerce_review_probe test_medical_consent_rollout
python tools/lite_commerce_review_probe.py --output /tmp/lite-commerce-review-new.json
```

The review ZIP includes exact changed sources, dependency files, before/after
probe results and logs, plus a Git bundle based on d642235. Do not execute the
Mac-specific workflow replay helper on Windows. No Windows command is required
if the exact commit becomes available through GitHub.

Suggested stack PR base is `codex/lite-reconsent-20261021` (#35), with the current
commerce branch as head. Publishing requires a new exact push/PR scope because
the prior explicit authorization named only d642235. No merge is requested;
deployment, App builds, user access and cleanup remain separate gates.
