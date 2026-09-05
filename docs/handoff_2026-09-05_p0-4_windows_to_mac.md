# P0-4 Windows → Mac 驗證交接（2026-09-05）

## 1. 交接目的與目前狀態

本交接只涵蓋 `WoundAI_Proj` 的 P0-4「照護同意後暫存、訓練同意後 promotion」與稽核鏈／部署閘門。它不是臨床就緒聲明，也不授權 Mac 端合併、Bucket Lock 或部署。

- Repo：`https://github.com/JackH0001/WoundAI_Proj.git`
- 分支：`codex/p0-4-staging-promotion`
- PR：[#4 — P0-4: consent-gated staging and promotion](https://github.com/JackH0001/WoundAI_Proj/pull/4)
- 基底：`main@505ff2e69f68a3b263f16adca8608a4036d5953f`
- P0-4 Phase A：`a47fc817deb13a7629309a235a10fcaf25cc58b8`
- 雲端硬化基礎：`26d93d7ee2e88a126af594e84117440f45eadadb`
- Cloud SDK 相容修正：`ad059f71c87267f511440931793ff3509333d2c2`
- audit v4／原子序列化：`346d8d7553e63075995e6b6f241ffd7ca9c0dd0e`
- least-privilege canary 部署閘門：`e0e6761a4819247fb5745b54b2905f247527f86d`
- gitleaks 資源名誤判精確處置：`d3e6e5e47e23486bdfd3f798ea15cdf804741a7d`

應以包含本文件的 PR #4 最新 head 與該 head 的 checks 為準；不得引用舊 head `ad059f7` 或 `f817e41` 的綠燈替代。

## 2. Windows 已建立的證據

### 後端／稽核鏈

Windows 隔離 runner 結果：**56/56 測試檔通過、0 失敗**，且 `source_snapshot_same_after=true`。涵蓋：

- audit chain v1–v4 相容與 v4 嚴格 schema。
- GCS 原子 slot、跨 store／跨執行緒競態、412 仲裁。
- response-lost 的 502/503/504、retry、connection/timeout 負向矩陣。
- generation 變化、cache eviction、同位元組新 generation。
- admin audit 讀取與證明來自同一份 strict snapshot。
- P0-4 staging／promotion／撤回／冪等回歸。

本機證據：`artifacts/windows-test/p0-4/audit-v4-final-r2-20260905/python-summary.json`（不進版控）。此結果不建立 iOS build、App E2E、部署映像、雲端現況或臨床就緒。

### 部署閘門

- `engineering/phase2/test_deployment_scripts_static.py`：**5/5 通過**。
- Windows PowerShell 5.1 parser／變數大小寫碰撞：三支腳本全 PASS：
  - `deploy_cloudrun.ps1`：4815 tokens／141 variables。
  - `harden_bucket.ps1`：2597 tokens／90 variables。
  - `provision_runtime_identity.ps1`：2049 tokens／73 variables。
- `git diff --check`：PASS。
- gitleaks 8.28.0：修補後實掃 4 筆 commit 為 0 finding；另以 4 個新命中作負向控制，4/4 仍被攔截。`.gitleaksignore` 只列 `e0e6761` 的兩個完整 fingerprint，不按檔案或規則做寬泛豁免。
- Docker Desktop engine 在本輪未能啟動，因此本機沒有建立部署映像；PR head `193ec93675a7ee19c429ef4291289f71b03cd896` 的 GitHub `backend-image` 已 PASS，補上容器建置證據。
- 同一 head 的 Android assemble/unit、iOS xcodebuild、endpoint-guards、audit-contract、兩組 integrity 與兩組 gitleaks 均 PASS；Android emulator `androidTest` 為 workflow SKIPPED，不列為通過。
- final code 已在 disposable GCS bucket 以 12 路併發寫入 1,011 筆：seq 1,011/1,011 唯一、fork=0、broken_link=0、real_issues=0，且跨過單頁 1,000 物件邊界。完整 pre-lock 摘要見 `docs/evidence/p0-4/PRELOCK_VERIFICATION_20260905.json`。

## 3. Mac 取得與 commit 完整性檢查

不要複製 `.git`，也不要直接修改產生的 `.xcodeproj`：

```bash
cd "$HOME/dev/WoundAI_Proj"
git status --short
git fetch origin --prune
git switch codex/p0-4-staging-promotion
git pull --ff-only origin codex/p0-4-staging-promotion
git lfs pull

git log --format='%H %s' -6
git merge-base --is-ancestor 505ff2e69f68a3b263f16adca8608a4036d5953f HEAD
git diff --check 505ff2e69f68a3b263f16adca8608a4036d5953f..HEAD
```

須看到上述六筆 P0-4 implementation／evidence commit 保持在線性分支歷史中；若 SHA 不同，先停下確認 PR 是否有經覆核的新 commit，不要自行 cherry-pick 舊 SHA。

## 4. Mac build／test

`iOS/project.yml` 是專案 SSOT：

```bash
cd "$HOME/dev/WoundAI_Proj/iOS"
xcodegen generate
xcodebuild -resolvePackageDependencies \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp
xcodebuild build \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO
xcodebuild test \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -destination 'platform=iOS Simulator,name=iPhone 15,OS=latest' \
  CODE_SIGNING_ALLOWED=NO
xcodebuild build \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundLite \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO
```

若 simulator 名稱不同，以 `xcrun simctl list devices available` 選實際存在的裝置。保存完整 stdout/stderr、Xcode／macOS 版本、destination 與最終 SHA；不要只回報「PASS」。

## 5. App E2E 必驗矩陣

E2E 應對準另行提供的 candidate URL，不得直接拿 production live URL試驗。使用合成 `WD-*` 個案與非 PHI 影像：

1. 無有效照護同意：`classify` 可分析，但不得回傳可持久化 `image_id`，不得寫 staging/images/receipt。
2. 有效照護同意：App 先呼叫 `/api/v1/consent/care/attest`，再把 `care_receipt` 帶入 `/api/v1/classify`；只可落 `staging/` 與無身分明文的 bind。
3. 訓練同意為 false 或已撤回：`/api/v1/annotation` 必須 fail-closed，不得 promotion 或加入訓練 queue。
4. 有效訓練同意：promotion receipt 必須先 durable，之後才可出現 `images/`；queue receipt 最後寫入。
5. 相同請求重送、App 中斷後重送與平行標註：不得重複 promotion、不得 fork audit chain，回應須符合冪等契約。
6. 撤回後重送／repair：每次轉場前重新驗 withdrawn；不可把 quarantine 或已撤回內容恢復到訓練資料集。
7. exact-byte restage：本機有保存實際上傳位元組時才可宣稱免重測；保存失敗必須顯示需重測。
8. 服務切換／網路中斷：App 不得把 candidate 的暫時性錯誤誤報成已提交或已取得訓練資格。

回交資料至少包含：每一案例的 HTTP status、去識別 response 摘要、App 畫面結果、對應 server audit action，以及 staging/images/receipt/queue 是否存在的核對。不得把 token、secret、簽名影像或 PHI 放進 log／commit。

## 6. 雲端與發布阻斷點

以下全部完成前，不可解除「僅範例／模擬資料」限制：

- [ ] PR #4 的新 head 上 `audit-contract` 與 `backend-image` 等 checks 全綠。
- [ ] 獨立覆核與 Mac iOS build／test、App E2E 完成。
- [ ] 新的 dedicated runtime service account 與三個精確 secret IAM 完成；不得再使用預設 Compute service account 的廣泛權限。
- [x] 以拋棄式 smoke bucket 驗證 final code 的真 GCS atomic append、1,000+ pagination／大量物件與嚴格 verifier（1,011 筆、0 real issue）。
- [x] 建立全新的正式 audit epoch bucket並驗證空桶、區域、PAP、UBLA 與 7 年 retention；目前仍為 `locked=false`、0 物件。
- [ ] 完成 dedicated runtime identity 後，驗證正式 audit epoch bucket 的精確 IAM。
- [ ] 只對新 audit epoch bucket 執行 Bucket Lock，且先保存 before/after 與授權記錄。
- [ ] PR 合併至 remote `main` 後，才可由 `deploy_cloudrun.ps1` 建立 no-traffic candidate；candidate 全探針通過才切流量，失敗須回滾。
- [ ] 列舉並移除舊預設 Compute service account 的過度權限，完成切換後覆核。
- [ ] P0-2（JWT 撤銷）與 P0-5（Android 離線事件排序／最後意願）完成；兩者目前仍未解。

舊桶 `gs://woundai-flywheel-jackh001-audit` 含歷史 audit fork／broken-link，**不得 Bucket Lock**。它只作為舊紀元保留與查證來源；正式 locked epoch 必須使用新桶。

## 7. 決策界線

- push 與更新 PR 不等於合併。
- 合併不等於 Bucket Lock 或部署。
- 授權不等於驗證，也不能取代失敗或缺少的前置證據。
- 本交接不解除臨床限制；在 P0-2、P0-5、Mac E2E、雲端 locked epoch 與 least-privilege cutover 都完成前，production 仍只可使用範例／模擬資料。
