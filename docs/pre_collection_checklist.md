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

### B1. 儲存桶強化

```powershell
cd C:\dev\WoundAI_Proj\Backend\Flask
.\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001 -Audit
# 上一行是唯讀稽核；符合預期後才執行：
.\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001 -Audit -Apply
```

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

只有 smoke 驗證、目標桶讀回驗證、P0-2/P0-5、App E2E 與 IRB/DPA 全部通過後，才可以對
**明確指定的新桶**執行以下不可逆操作：

```powershell
.\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001 `
  -Audit -AuditBucket '<new-clean-audit-bucket>' -Apply -LockRetention `
  -LockAuthorisationRef '由實際授權者輸入的單行授權參照'
```

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
