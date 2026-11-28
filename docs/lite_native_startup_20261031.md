# Native Sandbox startup and independent image profile — 2026-10-31

This change closes the gap between a graph constructed by tests and an explicit
runtime factory. It does **not** authorize deployment or declare public launch
ready. Legacy `Backend/Flask/Dockerfile` still starts `app:app` unchanged.

## Before / after

| Before | After |
|---|---|
| Tests manually supplied account, usage and identity objects. | `lite_native_startup.application()` builds the same graph from bounded, hash-pinned mounted settings. |
| Native imports pulled `store` through an exception, and the HTTP gate referenced the research reviewer module. | Shared exception is independent; the legacy research blueprint installs its existing JWT/audit checker. The native package does not contain or import `app`, `api_lite`, `api_flywheel`, or `store`. |
| Only a legacy image profile / dependency lock existed. | Separate `Backend/LiteNative` profile, explicit file allowlist, pinned models and a separate hashed dependency lock. The legacy lock is unchanged. |
| Startup error could be mistaken for permission to initialize an empty policy. | Missing/contradictory activation evidence stops startup. No activation, migration, external observer publication, cleanup or key provisioning occurs. |

## Reviewed settings contract

The factory requires all five environment values. No local ADC, `.env`, service
account JSON key or automatic lookup of another service is supported:

- `LITE_NATIVE_CONFIG`: absolute regular mounted JSON file, at most 256 KiB.
- `LITE_NATIVE_CONFIG_SHA256`: digest of those exact bytes.
- `LITE_NATIVE_SOURCE`: exact 40-character application source commit.
- `LITE_NATIVE_IMAGE_SHA256`: exact 64-character built image digest.
- `LITE_NATIVE_SECRET_DIR`: explicitly mounted directory; references are single
  filenames, bounded regular files, not symlinks, checked against their digests.

Configuration uses `lite-native-startup/2` for separate mounted secrets, or the existing
`lite-native-startup/1` flat layout, with `enabled: true` and exact
field sets (unknown fields rejected):

| Section | Required contents |
|---|---|
| `namespace` | Stable native account binding namespace; never silently changed to make old state disappear. |
| `deployment` | Source/image pins, project ID/number, named runtime SA in that project, KMS keyring in `asia-east1`. |
| `app` | `com.woundai.lite`, numeric Apple app ID, team, **one** allowed build, App Attest audience. TestFlight/App Store attestation only. |
| `apple` | Sandbox subscription environment, reviewed monthly product, separate OAuth/API key IDs, issuer UUID, bounded pinned root-certificate references. Official verifier keeps online certificate checks. |
| `budgets` | Explicit per-minute/day transport-abuse limits; independent of the free-2 / Plus-total-10 success ledger. |
| `buckets` | Exact bucket names and reviewed metagenerations, not a discovery prefix. |
| `stores` | Every one of 36 distinct state/audit lanes; bucket + non-overlapping `lite-native/<scope>/<lane>` prefix. |
| `controls` | Independent operator/observer **public** keys, durable release manifest, usage plan and baseline records; all hashes/identities must agree. No private operator keys in the web service. |
| `secrets` | Three distinct mounted references: 32-byte identity HMAC, P-256 Sign in with Apple key, P-256 App Store Server API key. |
| `model` | Preprocessing digest; three ONNX artifacts additionally checked against source-controlled artifact hashes. |

### Individual secret volumes for the candidate

Cloud Run mounts one Secret Manager secret at each directory. Items in a single volume select versions of that one secret. Use this layout with the v2 schema. The mount paths below are examples for a candidate revision. Use an individually approved, numeric version of each named secret.

| Config reference | Example mount directory | File inside the volume |
|---|---|---|
| `LITE_NATIVE_CONFIG` | `/etc/lite/config` | `settings.json`, with the reviewed configuration digest |
| `secrets.identity` | `/etc/lite/inputs/identity` | `value`, the 32-byte account identity key |
| `secrets.oauth` | `/etc/lite/inputs/oauth` | `value`, the Sign in with Apple signing key |
| `secrets.api` | `/etc/lite/inputs/api` | `value`, the App Store Server API signing key |
| `apple.roots[0]` | `/etc/lite/inputs/root-1` | `value`, the first reviewed public certificate |
| Additional root entries | `/etc/lite/inputs/root-2` through `root-4` as listed | `value` in each directory |

Set `LITE_NATIVE_SECRET_DIR` to the common parent `/etc/lite/inputs`. Each reference contains its own leaf filename and SHA-256. Directory names are derived from the role and the certificate order. Access for the runtime identity must be limited to its required secrets. Keep the key preparation worker and the disposition worker on their own reviewed mounts and identities. An existing v1 configuration continues to read its flat files. Both layouts require separate Apple key IDs and key pairs. A missing or contradictory mount stops startup before cloud providers are connected. Restore a previous configuration together with its exact mount layout, image and version pins.

An isolated Linux run exercises the factory and signed requests with generated inputs. The actual provider mount permissions, runtime identity and live Apple credentials still require a candidate readback. [Cloud Run secret volumes](https://cloud.google.com/run/docs/configuring/services/secrets#limitations) · [SecretVolumeSource](https://cloud.google.com/run/docs/reference/rest/v2/Volume#secretvolumesource)

The config hash is a deployment integrity pin, **not** an authorization token or
proof of appropriate IAM. Review the root certificates independently before a
real deployment. The web process uses the named Cloud Run metadata identity.
After a real bucket reload it checks identity, project, region, UBLA, public-access
prevention, no retention/default hold, versioning off, explicit soft-delete 0,
no lifecycle rule and exact metageneration. It does not change bucket policy.

These are dedicated mutable native-service stores, not the existing MMH, Demo,
Lite research-media or seven-year WORM audit buckets. An immutable audit record
means create-only application writes; this profile does **not** claim an enforced
WORM retention period. A deployment proposal must separately specify retention,
backups, per-prefix create/get/update permissions and deny access to unrelated
buckets. Startup metadata checks do not replace those IAM tests.

## Startup, restart and failure behavior

1. Validate all settings, mounted secret types/hashes and signed-control pins.
2. Validate/load the three pinned models before making cloud clients.
3. Read only the named buckets and verify the named runtime identity.
4. Create the concrete state adapters, official Apple API/verifier and existing
   account/usage/identity graph. Validate already activated policy evidence.
5. Write only the existing create-only component binding markers where needed;
   preserve every prior account, request, quota, result and audit row. Repeating
   startup with the same binding is idempotent. A conflicting binding fails.
6. Expose only the declared signed device/account/purchase/service routes.
   Research, reviewer, operational and cleanup-worker routes return 404.

Paused/unpublished **service release** can start if the subscription policy was
already initialized, allowing deletion intake while new work stays blocked.
An absent subscription activation does not become an automatically approved
fresh deployment. `RuntimeRolloutProvider` refuses all operational transitions.
The runtime KMS transport permits metadata/encrypt/decrypt only and refuses
provision/destroy/restore; the deployed SA must independently lack those IAM
permissions. Operator/worker credentials must never be shared with this service.

Startup failures retain evidence and do not delete resources. Provider bodies,
keys and account rows are not included in startup error messages. Rollback of a
future deployment is to the previously reviewed **compatible** image and settings,
with the durable release paused when compatibility is uncertain; never reset the
ledger, change namespaces or resurrect old writers to make a health check pass.

## Local validation and reproduction

Use the same dependency overlay as the existing commerce tests, with
`PYTHONDONTWRITEBYTECODE=1`, `WOUNDAI_STORE=local`, and
`PYTHONPATH=tools:engineering/phase2:Backend/Flask:.`:

```sh
python -m unittest -v test_lite_native_startup test_lite_native_package
python -m unittest -v test_lite_attest_http test_lite_build_context
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

The first group has 39 tests. Nine signed-HTTP scenarios run through mounted
settings: free-2, Plus-total-10 and restore, session renewal, restart/replay,
cross-midnight recovery, settlement response loss, pause/delete intake, late
result after closure and blocked research/worker routes. They use generated
Apple/App Attest roots, synthetic KMS, model transport and temporary SQLite.
They are not live Apple Sandbox, GCS, device or clinical-accuracy evidence.

Packaging uses a new output directory outside both checkout and model source:

```sh
python tools/stage_lite_native_candidate.py --repo /path/to/checkout \
  --models /path/to/verified/models --output /path/to/new-context
cd /path/to/new-context
PYTHONDONTWRITEBYTECODE=1 python verify_native_package.py
```

The build-time verifier checks the exact file set and hashes, imports the native
factory and verifies that no legacy application module loaded. It does not start
the service or claim a deployment was accepted. Models, original images, mounted
keys and runtime databases are not committed to the repository. The tool copies
only the explicitly pinned model artifacts into the local build context.

## Remaining gates before Apple Sandbox

- A real Linux container build and staged-source/runtime validation. Resolving
  Linux wheels with hashes is useful evidence, **not** a successful container run.
- Fixed-source candidate review and separately authorized cloud resources/IAM;
  actual bucket readback, isolation, bounded load and rollback rehearsal.
- Authorized policy/release initialization and per-account KMS provisioning.
  The web service deliberately cannot create a missing account key; login will
  remain unavailable until the separate provisioning flow exists and is accepted.
- Sign in with Apple configuration, mounted key custody/recovery, real Apple
  Sandbox S0–S9, native UI and retained-record tests against that same candidate.
- Complete deletion, research-disposition linkage and retention/backup validation.
  HTTP 202 means intake accepted, **not** that the account or all data is deleted.

No patient images, research data, real tokens or keys are required for this local
validation. No deployment, IAM mutation, real data deletion or store update was
performed by these tests.

## Attached Cloud Run identity

### External beta revision and secret versions

For the external Lite personal service, save the approved settings JSON, the
operator's independent release plan, and the Cloud Run **v2 Revision** response.
Run the following offline, keeping the inputs in the operator's workspace.

```sh
python tools/check_lite_native_revision.py plan.json revision.json settings.json
```

The plan records the signed app's `team` and `build` (strings), `revision` (full resource name), `image`
(registry path at SHA-256 digest), `settings_sha256` (original file bytes), and
`secret_versions`. The latter maps `config`, `identity`, `oauth`, `api` and each
configured `root-N` to `projects/PROJECT_ID/secrets/NAME/versions/NUMBER`.
If research linking is planned, put its absolute file path and numeric Secret
version in `additional_secret_files`. Add a `research_link` object with that
same `file`, the personal service's `native_issuer` payload `sha256` from the
custody receipt's verified manifest, and a Boolean `capture_enabled`. The
checker compares these values to the running revision's literal environment
and mounted file. Linking for account withdrawal can use `capture_enabled: false`.
Revisions with linking and capture both off keep the original plan format.
Pin the plan before collecting the provider response.
The regression fixture includes a complete example of this comparison.

Exit 0 confirms the reviewed mounted inputs, app build, Sandbox product, runtime
identity and image references for the named revision. Owner enrollment and
owner-only settings fail this external-beta check. The output covers configuration.
Collect the serving traffic allocation, effective IAM, in-container mount metadata,
successful startup and a tester's actual App Attest and Sandbox receipts separately.
The comparison runs on the operator's machine and takes metadata files as input.
The signed IPA and retained service image can be used with this tool as issued.
[Revision resource](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services.revisions)
and [secret volume fields](https://docs.cloud.google.com/run/docs/reference/rest/v2/Volume).

### Verify the URL in the TestFlight build

For external beta acceptance, add `serving_origin` to the reviewed plan. Copy
the `LiteNativeSandboxOrigin` value from the verified, signed IPA for the build.
Collect a Cloud Run v2 **Service** resource alongside the **Revision** resource.
Use both snapshots from the same deployment after traffic has settled.

```sh
python tools/check_lite_native_revision.py plan.json revision.json settings.json --service service.json
```

Plans with `serving_origin` require the service snapshot. The check verifies that
the service advertises that exact default app URL with external ingress, that
the observed generation has reconciled successfully, and that the requested
and observed traffic both direct 100% of ordinary app requests to the reviewed
revision. Explicit revision routing and the Cloud Run latest-ready default are
supported. A tagged URL with zero traffic can still exist during staging.
A successful result includes the observed generation, origin and percentage.
Existing mount-only plans can continue to check a revision before traffic moves.

This is a point-in-time comparison of control-plane metadata. Also verify IAM
and a signed request from a fresh installation at the app URL, followed by
Apple Sandbox sign-in and purchase restoration before inviting external testers.
[Cloud Run v2 Service fields and traffic allocation](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services)

### 本人 TestFlight 的兩階段服務讀回

`owner-review` 讓一個已登記的安裝先提交 Apple 身分。取得本人帳號的私有核對收據、完成額度與服務啟用後，`owner-only` 才接受該安裝與帳號的訂閱操作。裝置初次登記仍用獨立的 `check_lite_owner_enrollment_revision.py`。

先依發行 IPA、受限執行身分、固定映像及實際 Secret 數字版本建立本頁的方案，包含 `serving_origin`。將 `owner` 加入私有方案，欄位由該階段決定。

| mode | 其他必填欄位 |
| --- | --- |
| `owner-review` | `installation_sha256`、`starts`、`until`。後兩者是 epoch 秒，期間最長 3600 秒。工具在此時段內讀回修訂，核對執行環境上的截止時間。 |
| `owner-only` | `installation_sha256`、`account_sha256`。帳號摘要取自已私下核對的本人收據。 |

在採集雲端讀回前保存已覆核方案的檔案 SHA-256。以服務和修訂的同次部署快照執行：

```sh
python tools/check_lite_native_revision.py owner-plan.json revision.json settings.json --service service.json --owner-plan-sha256 "$OWNER_PLAN_SHA256"
```

比對涵蓋實際 Ready 的修訂、已收斂的服務、預期的單一目標與全量流量、掛載及模式專用欄位。帶有其他修訂標籤的路由會停止驗收。輸出列出模式與時間，個人安裝及帳號摘要只留在私有輸入。外測沿用原呼叫方式，遇到本人模式仍會拒絕。實際 IAM、生效端點、本人手機登入與 Sandbox 交易另以對應的現場讀回確認。

### Runtime metadata

The native service and the separate key preparation worker check the attached service
account email, project ID, project number, and asia-east1 region through the fixed
Cloud Run metadata endpoint before constructing Storage or KMS clients. Bundle,
signature, local mounts and model checks retain their existing order before
provider access. A mismatched or unreadable field stops startup; the process does
not try an alternate account. A configured credential object can echo the account
passed to its constructor even on the wrong machine, so it cannot establish the
deployment identity. The checks are GET-only and do not acquire an access token.

Use the actual logged service identity and platform fields in a separately authorized
deployment smoke test. The loopback tests use fabricated metadata; passing them does
not attest a live revision. See [Cloud Run container runtime contract](https://docs.cloud.google.com/run/docs/container-contract#metadata-server).
