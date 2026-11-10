# Owner-reviewed initialization commands

This change prepares a real operator workflow for the private bootstrap runner.
It does not deploy, enable customer traffic, grant permissions, generate real
keys, or copy fixture state into an installation. Existing data and audit remain
unchanged. The CLI is deliberately absent from the cloud image: the operator
seed stays in the owner's existing encrypted backup.

## Before and after

Previously, the runner supported execution but only tests constructed its signed
requests. Its inspection returned bootstrap progress without the quota pause or
service release state required to recover an uncertain step. An operator would
have had to reproduce signing and recovery logic manually.

The runner now emits `lite-bootstrap-runner-receipt/2`. A read-only inspection
includes its timestamp, quota control and release control. The bootstrap state
is read before and after those reads; a change or an unreadable record stops
inspection. This is not a transactional snapshot of all stores. The executor
still independently checks signatures, expected state, current observations and
CAS conditions when the command runs. Execution receipts have `inspection: null`.

`tools/lite_bootstrap_commands.py` adds two local actions:

1. `prepare`: check separately pinned runner/native configuration and inspection;
   construct the next ordered step or explicitly requested recovery; write a new
   unsigned draft. It makes no cloud calls and creates no observer proof.
2. `sign`: reconstruct that same draft from the pinned inputs, restore an existing
   owner backup using its separate recovery receipt, verify scope and the pinned
   operator public key, and sign a single request. It requires an interactive
   terminal, explicit `SIGN_BOOTSTRAP`, and a hidden passphrase prompt. It does
   not execute the result or create replacement keys.

Public review shows project, namespace, source, image, plan, stage, action and
expiration. Only the operator role is used; it must differ from the observer.
Outputs are create-only files with mode 0600 in an owner-private, nonsymlink
directory. The passphrase and private seeds are not arguments or output. As with
the existing custody implementation, this is not a memory-zeroization guarantee.

## Recovery boundaries

- Inspection must be no more than 60 seconds old when preparing **and** signing.
  Reinspect if owner interaction takes longer; an old draft is not silently renewed.
- Signed requests expire within five minutes, the approved provider budget, and
  the original UTC day, whichever ends first. Independent execution observations
  retain their existing 30-second freshness rule. Signing never supplies them.
- Recovery is explicit for the same unfinished stage; eight uncertain attempts
  still require reconciliation. New-stage, wrong-plan and reordered commands stop.
- Subscription recovery uses the existing core's receipt; it does not reactivate
  or regenerate accounts. A paused quota recovery pins the inspected pause hash.
- If a release publication landed but its confirmation was lost, the prepared
  inner request is `service-release-confirm`, not another publication.
- A changed/paused release needs separate review. Old signed requests cannot
  overwrite newer control state. A rejected execute can consume its operational
  budget slice, but cannot debit customer usage or recreate customer records.

## Operator sequence for a future authorized run

This is a preparation procedure, not permission to execute in Google Cloud.

1. Pin the approved runner/native files, source and rebuilt bootstrap image. Use
   the runner's `inspect` operation and retain its complete receipt and SHA-256.
2. Run `prepare --config … --native … --inspection …` with independent hashes,
   source/image, `--stage subscription|quota|release`, and a new output path.
   Add `--recover` only after reviewing an unfinished step.
3. Review the unsigned draft, then `sign` with those same three input paths,
   `--draft`, `--draft-sha256`, existing `--backup`, `--custody-receipt`, explicit
   `--key-id` and a new output path. Confirm locally; never paste a passphrase into
   chat, command arguments or a configuration file.
4. Retain the signed request hash. Separately refresh actual independent observer
   evidence and invoke the private runner within its authorized scope. Preserve
   uncertain outcomes; do not automatically create another request or replay a
   fixture. Reinspect before the next step or recovery.

The provider observer/writer-fence collection, real material custody, approved
resource/IAM plan and real cloud acceptance remain prerequisites. Version-1 runner
receipts are intentionally rejected. A new runner image must be built for this
receipt change; previous `a175a15` image digests are not relabeled.

## Reproducible validation

Use the repository's Python requirements and
`PYTHONPATH=Backend/Flask:tools:engineering/phase2:.`:

```sh
python -m unittest -v test_lite_bootstrap_commands
python tools/lite_bootstrap_commands_probe.py --output /new/path/commands.json
```

The probe uses a synthetic encrypted backup and nine real terminal subprocesses.
Only its temporary test wrapper fixes the fixture clock and rejects socket
connections. The product CLI has no clock override. It checks all three stages,
same-request replay, cancellation, wrong passphrase, no overwrite, hidden private
material, unchanged originals and one synthetic model invocation/one quota debit
through the native service core. This is not Apple Sandbox, production IAM,
segmentation accuracy or real user acceptance.
