# Lite review image refresh preparation

The closed portal and reader were deployed from `065c328` / image
`sha256:23a38262ac509da954bc3b313ab8ffb34fade9fb39770d6c2ad7dc91b7f80d07`.
The source/error feedback added in `208f6da` is not in that image. The B1 owner
login planner and transaction controller deliberately still accept only the old
image. Do not replace their constants and call the old A7 acceptance a test of
the new code.

## Change

The portal now uses an explicit minimal build context generated from a clean
commit. The tool reads the exact Git blobs and compares the working files,
including files hidden by `assume-unchanged`. It rejects links, source changes,
and an existing output. It never copies workspace data, models or credentials.
The manifest binds twelve runtime files and the Docker recipe to the source
commit. Both the Docker build and isolated runtime probe check the exact payload.
Runtime files are root-owned and not writable by the unprivileged service.

```sh
python3 -B tools/test_stage_lite_review_candidate.py
python3 -B tools/stage_lite_review_candidate.py --repo . --output /tmp/lite-review-context
docker build -t lite-review:local /tmp/lite-review-context
```

Use a new output path. The CI workflow performs these steps and then executes
both factories with signed synthetic identities, no network, empty roles and
no source storage access. It does not deploy or authorize a human login.

## Before owner login

1. Bind a reviewed source commit, Docker recipe, context manifest and immutable
   OCI image digest. Read the same twelve files back from the image. Record CI
   for the exact source separately; a local image build is not GitHub CI.
2. Obtain a fresh read-only baseline for the two existing services, identities,
   exact Secret versions and effective permissions. The October baseline cannot
   establish the current state. Keep MMH, Demo and all source buckets unchanged.
3. Prepare a separately authorized closed image refresh: same two private
   services, empty roles and catalogue, no human access or source data grants.
   Rehearse and pin a closed fallback revision; an expired catalogue can remain
   unavailable but must not open access. Do not delete prior resources or audit.
4. Verify the deployed digest, attached identities, each Secret mount's metadata,
   configuration pins, denial responses and absence of effective source rights.
   Save a new acceptance receipt. The old A7 receipt remains historical evidence.
5. Only then prepare a versioned B1 owner-login plan/controller with that receipt,
   a separate reviewed bounded live adapter and fresh owner/OAuth evidence.
   Source reads remain absent in B1; positive synthetic media access is B2.

The existing B1 planner/controller remain unchanged and reject this image.
This is intentional pending the new cloud acceptance and plan review. None of
the steps above are authorized by a local build or by a previous GitHub push.

## Review and limits

New tests cover ignored private data exclusion, dirty and assumed-clean source,
committed links, source changes during staging, output preservation, recipe and
payload tampering, and provenance-required probes. Existing portal, reader,
owner-login simulation and failure-containment tests remain applicable.

All data in these tests are synthetic. Actual IAP browser login, effective cloud
permissions, Secret mounts, positive research viewing, and current deployment
rollback are still unverified. No change to app records, study consent, cloud
data, permissions, Apple distribution or deployed services is made here.
