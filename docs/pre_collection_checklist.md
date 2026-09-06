# 收案前檢核 —— 在雲端放入第一張真實病人影像之前

> 2026-08-03。後端已在 GCP Cloud Run 彰化區運行，但**目前只可放範例圖、模擬圖、
> 自己的傷口照**。這份清單是跨過那條線之前要完成的事。
>
> 分成三類：**已做完（可驗證）**、**你要執行的指令**、**程式解決不了的**。
> 第三類最重要——把它們混在技術清單裡，會讓人以為跑完腳本就合規了。

---

## A. 已完成（附驗證方式）

| 項目 | 做了什麼 | 怎麼驗 |
|---|---|---|
| **PII 不上雲** | 姓名／病歷號以 Keystore AES-GCM 加密存在手機，離開裝置的只有 `WD-` 代碼 | 後端全域搜尋無姓名欄位；`/console` 明示不顯示個資 |
| **新鎖定紀元的稽核鏈實作** | v4 稽核物件以 immutable numeric slot 序列化，記錄帶 nonce、版本化雜湊與前一筆連結 | 先在拋棄式 smoke bucket 跑 `smoke_audit_chain_gcs.py`，再對**全新空白**正式稽核桶驗證 |
| **撤回同意即排除** | code ∪ image_id 雙鍵排除、影像移入隔離區、可重新簽署恢復 | `python engineering/phase2/test_resubmit_from_timeline.py` |
| **孤兒 GT 阻擋** | 缺 `image_id`／尺寸的標註一律拒收（曾有 8/8 筆不可訓練） | `python engineering/phase2/test_flywheel_datachain.py` |
| **範例圖誤標偵測** | classify 回報 `image_reused`，臨床模式警示 | 同上測試第 4 項 |
| **座標空間一致性** | 影像一律以當次畫布重存；review 畫面比對尺寸不符即停用修邊 | `docs/v3_review_findings.md` M1 |
| **病歷 DB 不滅失** | 移除 destructive migration；v1→v2→v3 遷移測試 | `.\gradlew :app:connectedDebugAndroidTest` |
| **影像保存期限** | 結案逾 90 天刪影像、**保留面積與趨勢**（病歷不可因逾期而毀） | `CaseRepository.purgeExpiredImages` |
| **憑證不隨程式散佈** | 後端密碼只從環境變數／Secret Manager 讀，程式碼無預設值；App 端 Keystore 加密 | 部署腳本會用舊密碼試登入並在成功時警告 |
| **病人影像不進容器映像** | `.gcloudignore` / `.dockerignore` 排除 `flywheel/`、`*.db`、`logs/` | 部署腳本出發前檢查，缺檔即中止 |
| **降級模式會現形** | 模型載不進來時 `/api/health` 回 `degraded` 並說明影響 | `curl <網址>/api/health` |
| **SBOM 與授權清冊** | 後端 11 項、Android 38 項、模型 3 個的來源與授權 | `python engineering/phase2/generate_sbom.py` → `docs/SBOM.md` |

---

## B. 你要執行的指令

### B1. 主桶強化（不帶 `-Audit`）

```powershell
cd C:\dev\WoundAI_Proj\Backend\Flask
.\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001
# 上一行是唯讀稽核；符合預期（PASS）後才執行：
.\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001 -Apply
```

⚠ 這一段**不要**加 `-Audit`。`-Audit` 不帶 `-AuditBucket` 時會退回預設的 legacy 桶
`woundai-flywheel-jackh001-audit`，而該桶的 675 個時間戳命名物件會讓 `Assert-AuditObjectsExpected`
必然 DIE——那是正確行為（legacy 桶只讀不寫），不是主桶有問題。稽核桶一律在 B2 以**顯式**
`-AuditBucket` 指定。任何一步 DIE，就停在那一步；不要接著跑 `-Apply`。

做四件事：封鎖公開存取（`publicAccessPrevention=enforced`）、統一存取控管、物件版本控制、
以及生命週期規則。

**關於生命週期規則要特別說清楚**：它**沒有**對 `images/` 設「N 天後刪除」。
IRB 承諾的是「結案逾 90 天銷毀」，而「結案」是臨床事件，雲端不知道。
用單純的年齡規則會刪掉仍在追蹤中的傷口影像——那是**病歷滅失**，比留著更嚴重。
P0-4 Phase A 腳本會精確設定四條規則，並逐條讀回；多一條、少一條都中止：

- `quarantine/` 逾 30 天銷毀 —— 已撤回同意的影像，保留只為短期稽核，不刪就違背「撤回即下架」
- `staging/` 逾 30 天刪除 —— 尚未取得訓練資格的短期處理中影像
- `staging_meta/` 逾 37 天刪除 —— bind 證據比影像多保留 7 天供失敗追查
- 非最新版本逾 30 天清除 —— 否則版本控制會讓「已刪除」的物件實際上永遠留著

另強制驗證 7 天 soft delete。上述時間是 lifecycle 配置目標，不是硬性 SLA；hold 與
GCS 執行延遲可能延後實體不可回復時間。

### B2. 稽核桶（WORM）

**不要鎖既有的 `woundai-flywheel-jackh001-audit`。** 它是開發期 legacy 桶；已保留
舊公式與 fork/broken-link 的發現證據，必須維持唯讀供追溯，不能被當成新的鎖定紀元。

先建立一個經專案負責人命名、明確核准的**全新空桶**，並先在另一個名稱含 `smoke` 的拋棄式
桶完成真 GCS 條件寫入測試。煙霧桶必須是未鎖、無 retention policy 且完全空白；測完要由
操作者確認目標、位置與物件清單後才刪除。正式桶也必須在第一次寫入前為空。

只有 smoke 驗證與目標桶讀回驗證通過後，才可以對**明確指定的新桶**執行以下不可逆操作。
專案負責人於 2026-09-06 決定：空桶鎖定屬 §D 所述之「技術步驟」，不涉及任何資料流入；
7 年為醫療紀錄保存下限、且鎖後只能延長不能縮短；**資料流入仍受 P0-2/P0-5、隔離／本機 App 協議
E2E 與 IRB/DPA 閘門管制（§D）**，鎖定不代表任何限制解除。

```powershell
.\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001 `
  -Audit -AuditBucket '<new-clean-audit-bucket>' -Apply -LockRetention `
  -LockAuthorisationRef '由實際授權者輸入的單行授權參照' `
  -LockRecordPath 'C:\dev\WoundAI_Proj\docs\evidence\p0-4\BUCKET_LOCK_YYYYMMDD.json'
```

鎖定成功後、授予 runtime identity 之前，以正式程式碼做一次零寫入閘門核對：

```powershell
# Python GCS client 使用 ADC；這與 gcloud CLI 的互動登入是兩套憑證。
gcloud.cmd auth application-default login
$env:WOUNDAI_STORE = 'gcs'
$env:WOUNDAI_GCS_BUCKET = 'woundai-flywheel-jackh001'
$env:WOUNDAI_GCS_PREFIX = 'flywheel'
$env:WOUNDAI_AUDIT_BUCKET = '<new-clean-audit-bucket>'
python C:\dev\WoundAI_Proj\engineering\phase2\check_locked_epoch_gate.py `
  --audit-bucket '<new-clean-audit-bucket>' `
  --project-number '421209514056' `
  --location 'asia-east1'
```

此檢查會比對明確 bucket、project number、region、prefix、7 年 locked retention，並列舉
**整個**新稽核桶確認仍為空；它不會寫入物件。

稽核桶必須獨立。GCS 的保留政策是桶層級、
不能只套在前綴上。套在主桶會連影像一起鎖住——而影像必須刪得掉（撤回同意、保存期限）。
稽核軌跡則相反，必須刪不掉。兩種需求相反，所以分桶。

⚠ `-LockRetention` 會**鎖定**保留政策，這個動作**不可逆**：保留期內任何人都刪不掉物件
（包含專案擁有者與 Google），桶本身也刪不掉，保留期只能延長不能縮短。
鎖定前腳本會拒絕預設 legacy 桶、確認新桶為空、驗 project/location/IAM，再讀回
`retentionPolicy.isLocked=true`。這項操作不可逆，必須有**針對該新桶名稱**的明確授權。

### B3. 佇列歸零

若測試期間送過標註，**不得用 `gcloud storage rm -r` 直接清空**。先停服務、list-only 盤點
staging，再以 `archive_flywheel_queue.py` 的 `--dry-run` 檢視範圍；正式歸檔需提供 operator
與授權參照，工具會先寫版本化 audit intent、完成後寫 outcome。它拒絕未清空 staging、
unsafe label 與無法寫 audit 的情況。

**不要刪 `audit.jsonl/`** —— 稽核鏈要連續，斷在中間比雜訊更糟。

### B4. 驗證

```powershell
python engineering\phase2\verify_audit_chain.py
python engineering\phase2\test_audit_chain.py
python engineering\phase2\test_flywheel_datachain.py
python engineering\phase2\test_resubmit_from_timeline.py
python engineering\phase2\test_store_abstraction.py
python engineering\phase2\test_audit_chain_concurrency.py
python engineering\phase2\smoke_audit_chain_gcs.py  # 僅限明確標記的空 smoke bucket
```

把 `verify_audit_chain.py` 印出的**鏈頭雜湊抄到程式碰不到的地方**（紙本值班紀錄、
或另一個帳號）。這是唯一能揭露「整條鏈被重算」的手段——雜湊鏈本身偵測得了單點竄改，
但擋不住有寫入權限的人把整條重算成一致的樣子。

### B5. 合併閘門 —— CI 必須先綠，而且必須是**這一次推送**的 CI

2026-09-06 的實際教訓（兩個 fail-open，同一天各出現一次）：

1. `gh pr checks <n> --watch` 印出 `no checks reported`，被當成「沒有紅燈＝可以合併」。
   它真正的意思是**查詢當下 check run 還沒被建立**。該次 check run 的建立時間是
   `07:05:51Z`，晚於指令執行的時刻。`--watch` 不會等待 check 被「建立」，筆數為 0 就
   直接結束；PowerShell 預設不會因為非零 exit code 停下，於是 `gh pr ready` 與
   `gh pr merge` 在**完全沒有任何 CI 結果**的狀態下跑完了。
2. 補救時改用 `gh run list --commit $sha` ＋ `gh run watch --exit-status`，回報
   `already completed with 'success'`——但那個 run 屬於**更早一次推送**，而當下要驗的
   那次 `git push` 其實失敗了（指令裡留著 `<branch>` 佔位符）。只憑 SHA 查 run，
   分不出「這次推上去的」與「上次推上去的」。

因此**不要**用 `gh pr checks --watch` 當閘門。改用：

```powershell
cd C:\dev\WoundAI_Proj
$notBefore = (Get-Date).ToUniversalTime()      # 一定要在 push 之前抓
$sha = (git rev-parse HEAD)
git push origin HEAD:refs/heads/<你要推的分支名，親手打，不要留角括號>
if ($LASTEXITCODE -ne 0) { throw "push failed -- STOP" }

.\engineering\phase2\Assert-CIGreen.ps1 `
  -Repo JackH0001/WoundAI_Proj `
  -Ref  heads/<同一個分支名> `
  -Sha  $sha `
  -Workflow p0-4-audit.yml, gitleaks.yml, phase0-check.yml, endpoint-guards.yml `
  -NotBefore $notBefore
if ($LASTEXITCODE -ne 0) { throw "CI gate failed -- STOP, do not merge" }
```

`Assert-CIGreen.ps1` 只觀察、不動作（不 push、不 ready、不 merge），並且機械性地拒絕：

- 帶佔位符形狀的參數（`<>`、`輸入`、`填入`、`placeholder`、`範本`、`TODO`）
- 縮寫的 commit id（只收完整 40 碼，避免前綴比對配到別的 run）
- 遠端 ref 不等於要驗的 commit（＝推送沒真的落地）
- 建立時間早於 `-NotBefore` 的 run（＝上一次推送留下的綠燈）
- 沒有 run、run 還沒跑完、逾時（一律不算綠）
- `success` 以外的任何結論，`skipped` 與 `neutral` 也算失敗——那代表閘門根本沒跑

這些性質由 `engineering/phase2/test_ci_gate_static.py` 靜態鎖住，並已用突變測試證明
每一條斷言都真的會抓（拿掉任何一項保護，對應的測試就變紅）。該測試本身也在
`.github/workflows/p0-4-audit.yml` 的 CI 步驟與 path filter 內——**測試沒被 CI 跑，
就只是文件，不是閘門**。

#### 兩個順序陷阱（2026-09-06 第一次實跑閘門時撞到）

**一、只在 `pull_request` 觸發的 workflow，必須先開 PR 才跑得出 run。**
`p0-4-audit.yml` 與 `endpoint-guards.yml` 的 `push` 觸發都限定 `branches: [main]`，
推到功能分支不會產生 run；要等 PR 存在、`pull_request` 事件觸發之後才有。所以順序是
**push → `gh pr create --draft` → 跑閘門**，不是 push 之後直接跑閘門。用 draft 開 PR
即可，`gh pr ready` 仍留在閘門通過之後。

**二、必填清單必須是對「這次變更」真正適用的 workflow。**
帶 `paths:` 的 workflow 只在改到那些路徑時才跑。把一個結構上不會被觸發的 workflow 列進
必填，閘門會正確地永遠不通過——那不是誤判，是清單開錯。2026-09-06 這次變更沒有碰
`Backend/Flask/**`，所以 `endpoint-guards.yml` 不適用，必填清單只列
`p0-4-audit.yml`、`gitleaks.yml`、`phase0-check.yml`。

**三、同一個 commit 可能有同一個 workflow 的多個 run。**
push 事件與 pull_request 事件各自產生獨立的 run id。2026-09-06 實測 `gitleaks` 與
`integrity-gate` 在同一個 commit 上各有兩個 run。閘門判定的是**所有**符合條件的 run，
不是只取最新的一個——否則紅的可以躲在綠的後面。（GitHub 的 re-run 是在同一個 run id 上
增加 attempt，不會多出一筆，所以這條嚴格化不會擋住重跑。）

決定清單的方法：看每個 workflow 的 `on:` 區塊。`on: [push, pull_request]`（`gitleaks`、
`phase0-check`）一定會跑；有 `paths:` 的，逐條對照 `git diff --name-only origin/main...HEAD`
的結果，有交集才列入。**寧可列少而準，也不要列一個跑不出來的**——但真正適用卻沒綠的，
一個都不能省。

合併仍為獨立閘門：CI 全綠只是必要條件，`gh pr ready` 與 `gh pr merge` 需要專案負責人
在看過閘門輸出後另行決定。

---

## C. 程式解決不了的（這一節才是真正的關卡）

| 項目 | 為什麼程式做不到 | 誰要處理 |
|---|---|---|
| **IRB 核准** | 沒有 IRB 核准，收進來的資料日後一筆都不能用。這是**前提**不是步驟 | 你＋院方 IRB |
| **與院方簽 DPA** | 委外契約須涵蓋受託範圍、權義等 10 項；使用非自行開發系統須說明來源與授權（`docs/SBOM.md` 是為此準備的） | 你＋院方法務 |
| **Cloud Armor IP 白名單** | 需要知道合作院所的出口 IP，而且要先架負載平衡器。現在沒有那些 IP | 待院所確定後 |
| **訓練資料集授權** | uwm wound-segmentation 與 FUSC 的條款須逐項核對，特別是「可否商業使用」與「衍生模型的授權要求」 | 需要人讀條款 |
| **CMEK（客戶管理金鑰）** | 目前用 Google 管理金鑰。要不要升級到 CMEK 是成本與合規要求的權衡，不是技術問題 | 依院方要求 |
| **紙本同意書歸檔** | 電子同意已留存，但雙軌保險的紙本編號要真的填進個案 notes | 收案時的操作紀律 |
| **範例圖被當臨床樣本** | 程式判斷不了照片裡是不是真人。`image_reused` 只抓得到「同一批位元組再次出現」 | 寫進 SOP＋教育訓練 |
| **值班與事故回應** | 臨床系統壞掉不能等上班 | 人的安排 |
| **WoundLite 匿名研究端點** | 其「被遺忘」語意不能直接套 clinical WORM；需單獨的 IRB、保留與防濫用決策 | 預設部署不註冊；另案核准才可啟用 |

---

## D. 明確的界線

**在 A、B 全部完成、P0-2/P0-5 關閉、Mac App E2E 通過，且 C 的前三項到位之前，
雲端只能放：**

- 範例圖（`source=sample`）
- 印刷模擬圖（`source=phantom`）
- 你自己的傷口照

**不可以放**：任何其他人的傷口影像，即使已去識別、即使對方口頭同意。

理由不是技術上擋不住，而是**沒有 IRB 核准的資料日後一筆都不能用**——
提早收只會得到一批不能寫進論文、不能送審、也不能拿來訓練上線模型的照片，
還多背了一份個資責任。

專案負責人的操作授權只能允許執行技術步驟，不能替代 P0 修補、IRB、DPA、院方網路
控制或 App 驗證證據；缺一項都不得把限制標成「已解除」。尤其不可把本輪的 Cloud
hardening 或正式稽核桶鎖定誤寫成臨床限制已解除。

---

## E. 收案當天的檢核（每一位受試者）

- [ ] IRB 核准文件在手，版本與同意書一致
- [ ] 病患已建檔，①照護同意已簽（未簽則量測入口是停用的）
- [ ] 要進訓練集者，②訓練同意已勾選
- [ ] 紙本同意書編號已記入個案 notes
- [ ] 每個傷口有獨立個案與 WD-code（不同部位不可共用）
- [ ] 拍照時 ArUco 標記完整入鏡且未變形
- [ ] 量測後確認來源是 `clinical`，且**沒有** `image_reused` 警示
- [ ] 存入時間軸後，主控台的「臨床」計數 +1
