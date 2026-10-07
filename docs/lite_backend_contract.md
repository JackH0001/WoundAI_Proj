# WoundLite 民眾版後端契約

> 2026-10-05 更新：已用隔離開發 App 完成真實 Apple attestation／receipt／兩筆 assertion 驗證。修復 sandbox receipt 名稱、精確 0xc0 flags 形式及 nonce 簽章算法；舊合成測試的「避免重雜湊」主張錯誤，不能沿用。現行回歸 179/179，正式 Lite App ID／GCS／Cloud Run 全鏈路仍待驗收。詳見 [實機結果](lite_real_apple_attest_validation_20261005.md)。下列按日期／階段保留歷史驗證邊界。

## 2026-10-04：候選封裝與模型前處理第十六階段（未部署）

封裝檢查發現 app.py 的 `_load_ssot()` 只讀 ../../engineering/phase0/preprocessing.json，而 Docker 建置只包含 Backend/Flask/，部署腳本把設定複製到 vendor/。在此容器目錄結構中會漏讀前處理設定，student 退回 RGB [0,1] 而非 ImageNet 正規化，WSM 也會漏掉 BGR 轉換。已用 HEAD 的原函式與合成 RGB 重現兩個確切數值錯誤（2 個失敗斷言、無執行環境錯誤）。此證據不代表已讀取線上映像，也不能倒推使用者先前某張照片誤差的原因。

新增 model_preprocessing.py：開發 checkout 的 SSOT 優先，只有該路徑不存在才找 vendor；缺檔、損壞、重複 JSON 欄位、錯誤模型結構／正規化參數均拒絕，不默默採另一份設定。模型載入與推論共用解析器；ONNX session 必須先通過前處理設定及實際輸入尺寸／layout 檢查，才公布為載入成功。傳統降級分支仍是既有程式行為；此修改不宣稱所有端點都已移除降級。

新增 tools/stage_lite_candidate.py，以明確 44 檔清單建立 repo 外的獨立建置上下文；不遞迴複製工作樹，不納入 .env、runtime、照片、任意權重或未知 Python 檔。三個模型核對完整 SHA-256，另與 model_registry 的既有前綴交叉檢查。symlink、缺來源、權重錯誤、來源在複製期间更動或輸出已存在均拒絕；manifest 明示 working_tree_dirty=true、release_approved=false，不能把 Git HEAD 當成這份候選內容的唯一識別。

實際從封裝資料夾啟動 app.py，載入 student／A-UNet／UNet++ 三個 ONNX，合成輸入均有有限數值輸出；執行 Lite 推論與完整 Flask signed raw 上傳、原生 Swift 回執核對、撤回清除成功。安全初始化使用合成測試身分／LocalStore，不連真 GCS／Apple；此為 macOS runtime，不是 Linux 容器映像，也不驗證分割精度。

驗證：前處理／模型契約 **10/10**，封裝 **5/5**；12 套回歸全部通過（合計 **124 項 unittest**＋Lite **62 項**＋runtime golden **4 項**），另 endpoint guards **8/8**，合計 **198 項**。verify_logic 50/50、parity 未宣告差異 0、git diff --check 通過。首版測試曾假設 SSOT 包含 aunet，實際 A∪U 走自己的前處理，因此改用明確的合成 [-1,1] 設定測一般轉換；原始日誌保留，以上另列的兩個 baseline 反例沒有依賴該錯誤假設。

本機未安裝 Docker，尚未執行 Linux Dockerfile／鎖定相依安裝。候選專用 IAM、預算、建置與部署指令、真 GCS／Apple App Attest、writer 異常終止回復及資料來源分類仍待完成；未建立雲端資源、未部署或推送。Chrome 唯讀核對仍為 NO_APP_STORE_CONNECT_TAB，沒有新的 Beta Review 狀態證據。

證據：repo 外 woundai-competitive-review-20261003/lite-release-package-20261004/validation.json、context/build-context-manifest.json、packaged-smoke.json、baseline 反例與 regression logs。來源與模型已以完整 hash 封存；後續來源異動需重新封裝。

## 2026-10-04：原始 RGB-D 簽章上傳與撤回第十五階段（本機候選，未部署）

新拍攝的 DepthCapture 資料完整且來源方向已知時，LiteMeasure 使用同一次 quality 0.92 JPEG 編碼建立 lite.rgbd/1 封包，經原有 App Attest 簽章送到 segment；不再把此路徑的原始 Float32 降成毫米 PNG。封包移至共用 Core/LiteRawDepthPacket.swift，醫療 target 的 BackendClient 也能正常編譯。缺來源方向等必要資訊的舊資料仍走既有 PNG 路徑，不產生原始深度已驗證的宣稱。本機紀錄的 quality 0.85 JPEG 仍是另一份編碼，不將其 hash 假改為上傳影像 hash。

後端 raw multipart 僅在裝置驗證與持久 writer ticket 均存在、同意有效時接受；拒絕重複 parts、混用 PNG、未知欄位、錯誤 digest／長度。模型前完成格式與資產配對驗證；LRD1 原始包採不可覆寫的單物件保存並讀回。相同影像／原始包重送回相同資產回執，不同深度或 metadata 綁同影像 ID 回 409；寫入或讀回未確認回 503。App 核對回執的 owner、capture、三份 digest、整包 digest 與長度後，才隨 LiteCloudBinding 保存「原始深度已核對」證據。

DELETE 在持久撤回且所有 writer 排空後清除該安裝的 raw namespace，包括後續步驟失敗留下的原始包，再清理研究 ledger。讀回失敗、清除失敗或仍有 writer 時不回報完成。只證明 live objects／rows；歷史版本、備份、匯出與衍生模型不在這次保證內。

驗證：新增 signed raw HTTP **9/9**；含 raw contract／store、App Attest、撤回 fencing、ledger purge、回執、修訂、service profile、quota、reader 及隔離的 **13 套、171 項 unittest 全過**。Release Simulator：**WoundLite 134/134、醫療版 72/72，皆 0 skipped**。另以真 Swift multipart → 真 loopback HTTP → 合成簽章驗證 → 暫存 LocalStore → 真 Swift 回執核對，確認 JPEG／metadata／Float32 逐位元一致；原簽章重放 401，簽章撤回 200 並清空本安裝 raw namespace，listener 已停止。這不是 Apple 真機持有證明、真 GCS 或線上模型精度驗收。

第一輪新 HTTP 測試以整個 lite_raw/ 列表要求全空，但 LocalStore 會回傳留下的空安裝目錄；已改為按實際清除契約驗本安裝 namespace，原始失敗日誌另存，修正後 9/9。verify_logic 50/50、parity 未宣告差異 0、git diff --check 通過。

限制：現行 app 與雲端服務尚未更新；沒有新的 TestFlight 上傳。未取得原生 confidence、相機 pose、曝光時間，registration 仍 not_verified、training_admission 仍 not_evaluated，不稱為完整多視角建模資料。為相容既有影像／修訂入口，LRD1 包之外仍另存 JPEG，增加儲存成本；相同包不重建，但既有 index 仍會附加請求行，並非全部請求冪等。還需處理來源 test_only 分類、AI 未手改時的最終量測 metadata、crashed writer 回復、真機記憶體與正式部署驗收。

證據：repo 外 woundai-competitive-review-20261003/lite-raw-business-20261004/validation.json、13 套測試日誌、兩份 xcresult、native-roundtrip.json 及來源快照。

## 2026-10-04：Lite 服務隔離第十四階段（本機候選）

新增明確 `WOUNDAI_SERVICE_PROFILE=lite`。既有未設定 profile 的醫療／demo 模式維持原流程；未知值拒絕啟動。Lite 模式要求 `WOUNDAI_ENABLE_LITE_API=1`、`WOUNDAI_STORE=gcs`、`WOUNDAI_GCS_PREFIX=lite-public-v1`、分開的 media／security 桶、明確絕對 runtime 目錄與至少 32 字元的獨立 `LITE_IP_SALT`。拒絕掛入 ADMIN_PASSWORD、JWT／Flask／care receipt 簽章金鑰、demo seed 或 audit 桶設定。這不代表 salt 的隨機性或 Secret Manager IAM 已受驗證；新密文尚未建立。

Lite 不註冊醫療 flywheel／users／console blueprint，不執行管理者／demo seed、醫療 SQLite 建表或 ImageJ 初始化，也不取得醫療 JWT／Flask 金鑰。共用推論程式及 app.py 中原有直接 route 定義仍在同一個程式包；第一個 before_request 閘門只放行健康檢查、五個確切 Lite POST 路由及本安裝的簽章 DELETE。其他路由（包括登入、醫療分類／訓練、主控台、Lite 管理者讀取與未來新增路由）、錯 method、query、编码路徑及非根掛載一律在後續 hook／body parsing 前回 404。通過路徑仍需既有 App Attest／owner／quota／withdrawal 閘門；不因採 Lite profile 而放寬。

啟動時現在會**實讀 media 及 security 兩桶**，核對 ASIA-EAST1、身分／專案編號與第十三階段的可撤回政策，完成後才建立安全狀態物件。Lite 安全或端點初始化失敗直接拒絕程序啟動；醫療模式維持其原本可降級及 health 揭露行為。Lite `/api/health` 只揭露本機模型載入與安全元件配置、版本／revision；模型未載入或配置缺失回 503，不宣稱資料庫／稽核可用，也不把本機健康檢查當成即時 GCS／Apple 端到端測試。

新增 **16/16**，相關 **13 套**全過（**184 項 unittest**及 admin_console 自訂驗證），另 endpoint_guards **8/8**，合計 **192 項 unittest／pytest**加自訂驗證；verify_logic、parity_check、git diff --check 全過。**9/9 變異捕獲**：放行未知路由、略過 canonical 檢查、晚註冊 perimeter、Lite 誤開帳號 bootstrap／醫療資料庫、未安裝 perimeter、略過 media 新讀回／政策檢查、安全初始化失敗仍啟動。真 app.py 啟動測試禁止取得真 GCS／醫療金鑰；合法註冊→簽章上傳→修訂→撤回在 perimeter 後通過。

首次新測試把所有 sqlite3.connect 都攔下，連測試用的 Lite budget SQLite 也被攔；已將「不建醫療 DB」的 spy 限定在啟動驗證後，再驗 unsigned 請求被拒絕。首次變異有 4 個假存活，原因是 symlink 測試 helper 的 resolve() 將 child import 指回原 app.py，未真的執行變異版本；修正為複製 Python 來源並斷言 app.py 的實際來源路徑後，控制組通過、9 個變異全部失敗。原始失敗證據保留，不計為成功。

Windows runner 會清除 profile，IP salt 延續既有清洗清單；CI 已加入新測試。未推送、未跑新的遠端 CI／Windows 原生驗證，未改線上服務／桶／IAM。Chrome 本輪唯讀結果為 `NO_APP_STORE_CONNECT_TAB`，已請 Jack 開啟並登入；沒有新的 Beta Review 或外測啟用證據。下一步仍是具體候選部署／IAM／預算與回復腳本、授權後真 GCS／Apple App Attest 驗收，以及 RGB-D 與資料撤回的剩餘閉環。

證據：repo 外 `woundai-competitive-review-20261003/lite-service-profile-20261004/validation.json`、suite logs、mutation logs、首次失敗紀錄與來源快照。

## 2026-10-04：Lite 專用儲存政策第十三階段（候選，未佈建）

唯讀取得主桶 `woundai-flywheel-jackh001` 的完整 JSON API metadata：projectNumber `421209514056`、ASIA-EAST1、metageneration `58`，物件版本保留啟用、soft delete `604800` 秒（7 天），另有 4 條 lifecycle 規則。因此現有 live-object DELETE 不能作為歷史資料全部清除的證據。本輪未修改既有桶、IAM、密文或服務流量，也沒有刪除真實資料。

新增 `lite_bucket_policy.py` 與離線 `engineering/phase2/check_lite_candidate_buckets.py`。檢查確切桶名／專案編號／區域、明確 PAP enforced 與 uniform IAM、無桶保留與預設 hold、versioning 關閉、soft delete 明確回讀為字串 `0`、無 lifecycle 規則，以及有效 metageneration。media／security 必須分桶。缺資訊、讀回失敗、格式不明一律拒絕；不把 CLI 重新命名或省略的欄位當成「關閉」。JSON 來源須用 `gcloud storage buckets describe gs://<bucket> --raw --format=json`，不可用 filtered list 輸出替代。

`build_lite_security` 新增必填 `WOUNDAI_LITE_SECURITY_PROJECT_NUMBER`，先做格式檢查，再以 SDK `bucket.reload(timeout=10, retry=None)` 實讀 security 桶政策，成功後才建立 keys／budget／privacy store。執行身分因此需要該桶的 `storage.buckets.get`。這是啟動時的政策證據；media 目前是離線 preflight，尚未接進部署腳本。兩者都不證明有效 IAM、舊物件沒有 hold、備份／歷史版本／匯出資料已刪除，也不保證之後管理者不變更政策。

驗證：政策 **13/13**，連同 HTTP／CAS／privacy／撤回／ledger purge／回執／測試隔離共 **130/130**；**9/9** 變異捕獲（桶身分、保留、版本、soft delete、lifecycle、區域、metageneration、略過新讀回與略過政策檢查）。真實主桶 metadata 通過離線判定為 blocked。verify_logic／parity_check／git diff --check 均回 0。新測試加入 CI 工作流，但未推送、未執行新的遠端 CI 或 Windows 原生測試。通過接受路徑使用合成 metadata／假 SDK，不能替代新桶的真 GCS CAS／刪除驗收。

證據：repo 外 `woundai-competitive-review-20261003/lite-candidate-storage-20261004/validation.json`、`actual-main-policy-verdict.json`、完整原始 metadata、8 套日誌、變異日誌與來源 SHA-256。Google 依據：[Object Versioning](https://docs.cloud.google.com/storage/docs/object-versioning)、[Soft delete](https://docs.cloud.google.com/storage/docs/soft-delete)、[Bucket JSON API](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets)。關閉版本保留不會刪除既有舊版本；改 soft-delete 政策也不能倒推歷史資料已消失。

### 下一階段候選資源草案（尚未執行）

| 項目 | 提案 | 必須補完的證據 |
| --- | --- | --- |
| 專案／區域 | `woundai-jackh001`／`421209514056`／`asia-east1` | 部署當次重新讀回，不能只採本文件數字 |
| 媒體桶 | `woundai-lite-media-421209514056`，STANDARD | 名稱只是提案，未確認全球可用；須是新建空桶、通過完整政策讀回，不能匯入舊臨床資料 |
| 安全狀態桶 | `woundai-lite-security-421209514056`，STANDARD | 與 media／audit 分開，禁止 lifecycle 過期；任何清理須保留撤回封鎖與防重放狀態 |
| 執行身分 | `woundai-lite-candidate@woundai-jackh001.iam.gserviceaccount.com` | 專用新身分；有效 IAM／群組與冒用路徑檢查，不授予專案 Editor；不可接觸正式或 demo 密文／資料 |
| 服務 | `woundai-lite-candidate` | 獨立服務與 URL；Lite profile 的路由／啟動隔離已於第十四階段本機驗證；雲端 revision 設定讀回及有效 IAM 仍待驗收 |
| App Attest | `LY2F24ZM68.com.woundai.lite`，audience `woundlite-research-v1`，production | build 白名單須綁定最終封版版本；真 Apple proof、冷啟動、重放、錯 owner 與撤回全鏈路驗收 |

runtime 的 media 權限需求待以實际路徑逐項核對，預期僅新桶上的物件 get／list／create／delete 與桶 metadata get；security 的 generation-CAS 覆寫也需要 get／create／delete，雖然程式不暴露單獨 delete API，IAM 的 delete 權限仍允許直接刪除，不能宣稱「身分無法刪除安全狀態」。IAM Conditions 的 prefix、繼承權限及桶 metadata 讀取須分開驗證。應使用 scoped custom role／bucket binding，並實測允許與拒絕案例；本輪沒有建立 role 或 binding。

佈建前還要把此草案接到可覆核的專用部署／讀回腳本，決定明確全服務預算與告警。單一使用者每日 5 次屬產品額度；註冊／challenge／assertion 的全服務 request budget 不等於每月費用硬上限。公開 Lite 不沿用 LocalStore，也不透過 gcsfuse 假裝持久且原子。研究收集開始前須完成終止 writer 的安全恢復、舊匿名資料處理與完整 RGB-D／最終 metadata 的端到端驗證。

新資源與服務部署不在既有 `4005f39` 示範服務部署授權內；完成指令、IAM 差異、成本與回復方案供 Jack 覆核後，再取得這一組具體操作的授權。此草案不是已部署環境，也不是公開上架通過聲明。

## 2026-10-04：研究索引與標註實體清除第十二階段（候選）

在持久撤回與 writer drain 完成後，DELETE 除可見媒體外，會清除 `lite_index.jsonl`／`lite_labels.jsonl` 中該安裝的研究內容；保留最小撤回標記。混有 payload 的 action 紀錄不能以「標記」名義逃過清除。本機 append_line／append_record_once／purge 使用同一個跨程序檔案鎖，先驗整份 JSONL，再 fsync＋原子替換並讀回；其他安裝的行保留原始 bytes。GCS 固定 generation 下載／CRC32C 驗證，刪除帶 generation precondition，重新盤點確認；未知物件名、無 generation、混合 owner 物件、損壞／重複欄位與讀回失敗均拒絕完成。只接受兩個確切 Lite ledger 鍵，稽核與臨床佇列不能進此清除入口。

只有兩份 ledger 都成功才加回 `research_ledger_cleanup=live_rows_removed`；舊的 `deletion_scope=live_media` 維持原義以相容既有客戶端。失敗回 503 `delete_incomplete`，持久封鎖不撤銷。本輪沒有承諾 GCS 歷史版本、soft delete、備份、匯出資料或已訓練模型全部刪除。

驗證：新增清除 **15/15**；真 Flask／簽章撤回 **9/9**；相關 **15 套**全部 exit 0（其中 **158 項 unittest**，另 4 套自訂儲存／稽核驗證）；**7/7** 變異捕獲。verify_logic、parity_check、git diff --check 回 0。涵蓋真實本機跨程序附加與 purge 競態、GCS 假傳輸的替換競態／假成功讀回；不是 Windows 原生或真 GCS 刪除證據。CI 工作流已加入新測試，尚未推送觸發遠端 CI。

唯讀雲端核對成功：`woundai-backend-demo` 目前 100% 流量仍指向 `woundai-backend-demo-demo-4005f392-pw2-10020537`。本輪未部署、未刪任何真實雲端資料。共享 ledger 盤點成本／規模、混合 owner 舊物件迁移、crashed writer 回復及歷史／衍生資料清除仍待處理。

證據：repo 外 `woundai-competitive-review-20261003/lite-research-purge-20261004/validation.json`、15 套日誌、7 份變異結果及來源 SHA-256。條件式刪除依據：[Google Cloud Storage request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)。

## 2026-10-04：Lite 撤回客戶端第十一階段（候選，未部署）

設定頁新增「撤回研究並要求清除雲端媒體」及二次確認。先停止研究上傳並原子保存待處理狀態，再以既有 App Attest 身分簽署空 body DELETE；關閉研究同意不會阻擋撤回，也不會為撤回建立替代身分。逾時、202、失敗或重開 App 後保留原服務與原 owner，可手動重試；狀態檔損壞時停用上傳。撤回中的本安裝不能重新開啟研究開關，亦不自動旋轉身分繞過撤回。

200 必須同時匹配 owner、`status=deleted`、`deletion_scope=live_media` 與 `writers_drained=true` 才顯示「目前可見媒體已清除」；202 只能顯示等待。紀錄頁只對相同服務／owner 改顯示撤回狀態，不把舊版匿名資料或其他服務的資料標成已刪除。研究同意說明版本更新為 `2026-10-04.1`。本機紀錄不會隨雲端撤回刪除。

驗證：Lite Release **129/129**（含新增 16 項撤回測試）、後端 HTTP **21/21**、withdrawal fence **7/7**，共 **157 項通過**；靜態 verify_logic／parity_check 均回 0；真機 Release 建置及 codesign 驗證通過。本輪 HTTP 首次因沙箱禁止 127.0.0.1 bind 失敗，取得本機測試權限後重跑全過。證據在 repo 外 `woundai-competitive-review-20261003/lite-withdrawal-client-20261004/validation.json` 及 clean.xcresult。

**未形成公開發行閉環**：這是候選版，沒有送出真實雲端 DELETE、沒有安裝覆蓋手機、沒有部署／TestFlight 上傳。歷史版本、備份、研究 JSONL 標註／衍生物與既有模型清除仍未完成；舊匿名資料身分復原、crashed writer 回復及實際 Apple App Attest＋GCS 端到端仍待處理，不能宣稱完整資料刪除或可上架。當前 Chrome 仍禁止 Apple 事件 JavaScript，因此沒有新的 ASC 外測狀態證據。

## 2026-10-04 撤回與跨請求寫入隔離（第十階段，本機候選）

新增 `lite_privacy_state.py`，每個installation以独立持久CAS狀態登記進行中的寫入。HTTP閘門先驗App Attest與owner，再取得writer ticket，才允許segment／annotation／revision進模型或寫檔；同步處理結束後才釋放。撤回以同一筆CAS永久封鎖新writer，尚有writer時回202 `{status:withdrawal_pending, anon_id, reason:writers_in_flight}` 及 Retry-After=5，不執行清理或宣稱完成。既有writer全部結束後，重試DELETE才盤點／刪除／讀回目前媒體namespace。身份金鑰不撤銷，否則無法重試已授權的DELETE；研究寫入由privacy狀態永久封鎖。

票券存於request.environ而非可跨巢狀request共用的Flask g。一般回應及例外teardown皆嘗試釋放；無法確認釋放則回503或保留待確認，不能刪除該票券充作成功。key／budget／privacy使用三個分開的GCS prefix，privacy設定缺失時不能放行Lite請求。管理端影像／preview／records另讀新privacy狀態，因此即使JSONL撤回marker寫入失敗，也不能重新展示已撤回owner的資料。

**沒有時間到自動清除ticket。** 暫停中的程序可能恢復寫入，時鐘到期不是它已終止的證據。程序被終止或登記寫入的回覆遺失時，撤回可能停在pending；必須補可稽核的終止確認／恢復作業才能將這條恢復路徑視為完成。不能把「保護資料不復活」等同「撤回永遠能自動完成」。本輪未實作管理者強制清ticket API。

成功DELETE的既有200／status=deleted新增 `writers_drained: true` 與 `deletion_scope: live_media`，明確只表示本候選閘門涵蓋的writer已結束、目前可見媒體物件已清理；研究JSONL目前以撤回標記排除，不是物理清除其中所有標註資料。舊物件版本、soft-delete／備份、已匯出研究資料與既有模型均未驗證清除。App UI不可因此寫「所有研究資料已刪除」。完整withdrawal receipt及前端狀態仍待接線。

部署前提：同一資料namespace的**所有**writer必須使用此閘門；不得讓舊服務持有該namespace寫入權限後仍宣稱已隔離。需獨立候選namespace或確實排空／撤除舊writer，並驗證security state無自動刪除／過期政策。新namespace的IAM、實際GCS CAS與操作復原尚未雲端驗證，本輪没有部署。

驗證：privacy12項（含SQLite及GCS假傳輸的同時撤回／進入寫入競態）、真實Flask與合成密碼學HTTP7項，加相關回歸231項，共250項通過。HTTP重現卡在實際put_blob前的writer：第一次撤回202、晚到寫入結束後仍拒絕新上傳、第二次DELETE200且namespace空。狀態缺失、marker失敗、release失敗、模型例外、重啟、其他owner及reader均有覆蓋。7/7變異被抓到；初輪release_reopens_owner存活，原因是測試先再呼叫withdraw意外掩蓋重開，已改成finish後立即檢查拒絕。首輪HTTP測試的JWT註冊時機及同執行緒巢狀request也已修正；保留原失敗log。verify_logic及parity_check在Mac回0，不代表Windows全套實跑。證據：repo外 `lite-withdrawal-fence-20261004/validation.json`、suite logs、mutations.json。


## 2026-10-04 App Attest 業務接線（第九階段，本機候選）

`BackendClient` 的 Lite segment／annotation／revision 已改用獨立 `LiteAuthenticatedTransport`；生產 adapter 是 `LiteSignedCloudTransport`，沒有退回一般 URLSession 或舊匿名路徑。醫療版登入與 JWT 請求仍使用原 session。新上傳先取得伺服器核發的 installation，再把同一身分寫入 multipart 與本機 cloud binding。研究同意在註冊前後、簽署前後檢查；拒絕註冊或逾時不會自動改身分或重傳媒體。

修訂只讀原已註冊身分，不替舊資料建立替代身分。無法證明原 owner 時，舊 server／anonID／imageID、pending revision 與原 payload bytes 保留，顯示尚未同步，不冒充完成遷移。原 UUID 偏好只保留為舊紀錄線索，新的 quota 使用已驗證上傳的記憶體快照。修訂重試重新簽章但沿用業務修訂及摘要，只有匹配回執才能清 pending；已確認且內容未變則不再次送出。

`Info.plist` 固定 audience `woundlite-research-v1`，候選後端 `WOUNDAI_LITE_ATTEST_AUDIENCE` 必須一致。Lite 單獨掛 `WoundLite.entitlements` 的 App Attest production；醫療 target 不掛。此設定不代表已完成真機 Apple 證明或雲端握手。Apple 說明 TestFlight／App Store 分發使用 production：[App Attest Environment](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.devicecheck.appattest-environment)。

新增9項實際 BackendClient→簽章client測試、2項保存／重啟同步測試、3項角度品質規則測試，與既有97項合計Lite111項；醫療版66項、後端HTTP21項＋revision22項通過，共220項。客戶端使用合成Apple adapter，不是Apple真機認證；後端21項包含真正本機HTTP、合成密碼學註冊／multipart及重放401。第一次後端HTTP在sandbox不能bind本機埠，提升執行權限後同源21/21；未略過失敗測試。第一輪105項後補測的檔案編輯曾因工作目錄錯誤而未執行，該次重跑仍105項；修正路徑後111/111，保留所有log。

依Jack補充，角度本身不是停止提供參考數值的條件；超過25°提醒誤差可能增加，深度品質不足或太近仍請重拍。10–25°提示正對與核對邊界，不保證誤差小。25°是操作閾值而非已驗證精度界限；43°現有資料保留為限制案例，暫不要求重做整套精度矩陣。

**後續尚缺：** 簽署DELETE／撤回UI、撤回與晚到寫入的跨實例隔離、AI未改遮罩的最終量測metadata同步、完整原始RGB-D上傳、候選後端與真機Apple握手。舊線上後端未支援註冊，這版client不能單獨發行；本輪未改線上服務或手機已安裝版本。證據在repo外 `lite-attest-business-20261004/`，以 `clean.xcresult`、`medical.xcresult`、`backend-http-verified.log`、`backend-revision.log` 及 `validation.json` 為準。


真機簽署補驗證：初次使用萬用描述檔失敗（缺App Attest），`-allowProvisioningUpdates` 成功取得 `com.woundai.lite` 專用開發描述檔。Release真機build成功、codesign verify通過；讀回App簽章的App Attest值為production，profile允許development／production，App ID為LY2F24ZM68.com.woundai.lite，audience符合固定值。這是開發描述檔的Release產物，不是App Store distribution IPA；尚未安裝手機、產生Apple真機proof或上傳TestFlight。詳見repo外 `lite-attest-business-20261004/device-signing.json`。

## 2026-10-04 iOS App Attest 客戶端（第八階段，尚未切換上傳）

新增 `LiteAttestClient.swift` 與平台adapter：DCAppAttestService產生key／attestation／assertion；Keychain保存key ID與短暫pending proof，使用WhenUnlockedThisDeviceOnly、不同步。註冊proof在HTTP前持久化；回覆遺失或App重啟只重送完全相同的proof，不重做Apple attestation。伺服器回覆key ID及installation需核對，成功後移除暫存proof。Apple serverUnavailable保留同key稍後重試；其他Apple attestation錯誤清除未完成key。已註冊key的assertion失敗不自動換身分。依據：[Apple app integrity](https://developer.apple.com/documentation/devicecheck/establishing-your-app-s-integrity)。

App容器內的隨機安裝marker排除備份，參與Keychain scope；更新可沿用，重新安裝／還原缺marker時不會誤用殘留Keychain。共用actor registry按正規化HTTPS origin及audience取同一client；FIFO鎖涵蓋所有await到HTTP回覆，最多8個等待者。完整body bytes、Content-Type、path、request UUID與server challenge一起雜湊；拒絕錯owner、跨origin、Cookie/JWT、query及百分比路徑。送出前再確認同意；不自動重試可能已提交的媒體請求。

原生URLSession採ephemeral，無Cookie／credential／cache、不跟隨redirect；串流回應限控制100KB／業務4MiB，結束時取消stream task及釋放session。Adapter已編譯，本輪網路流程用測試transport，尚非TLS／redirect真連線驗證。

Release iOS18.6模擬器 **97/97、0 skip**；新增client17/17，含並行註冊只產一次key、請求依序完成、完整body雜湊、錯owner／origin／audience／過期拒絕、同意在簽署期间關閉、Apple暫時不可用、pending proof重送、儲存失敗前不發註冊、安裝marker及原生模擬器Keychain讀寫。首次停用codesign造成6項既有加密儲存失敗；相同93項及來源改回ad-hoc簽署後全過，再補4項後97項全過。正式Release簽署政策未為測試放寬。

**尚未啟用：** BackendClient／LiteMeasure／修訂／撤回尚未使用此client；尚無App Attest entitlement與Developer Portal capability／provisioning驗證，沒有真機Apple證明、雲端候選或跨端握手。接線時須保留舊anon_id binding與待同步修訂，處理新installation及註冊拒絕，不得靜默回匿名上傳或宣稱舊資料已安全遷移。證據：repo外 `lite-attest-client-20261004/validation.json`、`final.xcresult`及source快照。

## 2026-10-04 App Attest HTTP 與部署設定（第七階段，本機候選）

`lite_attest_http.py` 已把註冊／assertion接到Flask資料路由。`POST /api/v1/lite/attest/challenge` 收嚴格JSON `{key_id, purpose}`（purpose為register或assert）；回傳challenge_id、標準Base64 challenge、expires_at、audience。`POST /api/v1/lite/attest/register` 收 `{key_id, challenge_id, attestation}`，回傳伺服器installation及key_id。控制請求分別限制4KiB／96KiB；key ID為32 bytes，所有Base64拒絕額外padding與非canonical編碼。註冊JSON不得指定owner或信任根。

業務請求使用 `X-Lite-Key-ID`、`X-Lite-Challenge-ID`、`X-Lite-Assertion`、`X-Lite-Request-ID`。伺服器在multipart／JSON解析前保留完整body供驗簽，限制32MiB、64個表單part，拒絕query／百分比路徑變體／壓縮body。POST需Content-Length；DELETE容許無Content-Length但必須空body。segment需唯一anon_id表單值；annotation/revision採拒絕重複JSON鍵的解析；值必須等於已驗證installation。DELETE路徑也綁定該installation。失敗不進模型／媒體写入；格式／證明401、owner不符403、超限413、容量或challenge數超限429、持久狀態／設定不可用503，所有Lite回應no-store。驗證成功後、業務參數才被拒絕的請求仍可能消耗counter/challenge，重試必須重新簽署。

管理介面三條GET查閱保持JWT＋audit.read，在閘門及原查閱函式均檢查。它們不要求消費者裝置金鑰；管理者JWT也不能替代App的POST／DELETE裝置證明。其他未列入契約的新Lite路由一律拒絕，需明確加入簽署契約才能開放。

新增 `RequestBudget`：獨立持久namespace中以同一次CAS預留全服務每分鐘及UTC每日請求數，包含失敗嘗試，不因新key／新安裝代碼重置。配額耗盡回429及Retry-After；未知提交結果不放行。這是全服務資源保護，不是個人五次的原子付費帳本；仍需邊界流量防護，因讀取限流器本身也有GCS成本。全域上限也可能被濫用者耗盡而影響正常使用者，需觀測與操作配套。

部署必填可信環境設定：`WOUNDAI_LITE_ATTEST_APP_ID`、`WOUNDAI_LITE_ATTEST_VERSIONS`（逗號分隔明確build白名單）、`WOUNDAI_LITE_ATTEST_AUDIENCE`、`WOUNDAI_LITE_SECURITY_PROJECT`、`WOUNDAI_LITE_SECURITY_PROJECT_NUMBER`（第十三階段新增）、`WOUNDAI_LITE_SECURITY_BUCKET`、`WOUNDAI_LITE_BUDGET_MINUTE`、`WOUNDAI_LITE_BUDGET_DAY`。全部通過格式／一致性檢查才建立GCS client；security bucket不得等於已設定的主桶或稽核桶，keys/budget再分namespace。公開伺服器僅production類別2/4，沒有請求指定development降級。本輪未佈建bucket或IAM，bucket可寫與適用保留設定仍需部署前實查。

app.py於 `WOUNDAI_ENABLE_LITE_API=1` 時先安裝閘門再掛業務blueprint。缺配時Lite端點503並記錄lite_attest配置失敗；health的lite_attest_configured只表示設定物件建立，不宣稱GCS或Apple實測可用。已用真正app.py啟動驗證4條路由拒絕及health揭露。測試runner剝除新的設定前綴與enable flag，factory沿用測試程序禁止GCS的保護。**此候選不能單獨部署到舊App仍在使用的服務**；iOS端尚未帶assertion，部署後會被拒絕，需使用獨立候選及協同切換。

驗證：新增HTTP／budget／config21/21，含真127.0.0.1 socket的註冊→signed multipart上傳200→同請求重放401，伺服器已關閉。App Attest共143/143；隔離22、端點pytest8、撤回查阅6、修訂22、segment62皆通過，總計263項。未改動控制組先通過，再確認12/12變異捕獲。首輪變異runner少測試環境標記，會造成無關失敗；修正後重跑控制組及全部變異。既有endpoint_guards使用pytest重跑8項，不能把直接執行.py的exit0當作測試。沒有遠端CI、Windows原生或線上部署結果。

待完成：iOS DCAppAttestService／Keychain／請求序列化、真機與實際GCS/IAM、舊匿名資料安全遷移、跨key配額治理、穩定request ID的業務重試去重（簽章防重放不等於媒體exactly-once）、撤回fencing及raw RGB-D接線。證據：repo外 `lite-attest-http-20261004/validation.json`、`main-startup.log`、`reproduce.py`及來源快照。

## 2026-10-04 App Attest 原子註冊整合（第六階段，本機候選）

新增 `lite_attest_enrollment.py`，註冊與assertion共用同一key狀態列。伺服器產生32-byte challenge與安裝代碼，120秒內重取回同一challenge，不延長期限；過期才輪替。客戶端不能指定安裝所有者，也不能用舊anon_id取得其他資料。完成註冊時同時驗Apple attestation及receipt，使用同一個從持久狀態讀出的nonce；提交前再次檢查期限，單筆CAS把pending整列換成active金鑰、公鑰、owner、receipt digest及counter=0。沒有先消耗challenge、後寫金鑰的跨物件空窗。

只對相同challenge ID和相同完整attestation SHA-256提供完成回執重送，並保留現有counter、owner與assertion challenges；回執不授予任何業務API權限。不同證明重註冊、已撤銷key、舊challenge、驗證中過期與儲存結果未知都不回新成功。若寫入已成功但回覆遺失，相同請求重試可讀回既有結果。尚未完成的challenge若過期，客戶端可能需要新建key再做Apple attestation；不可讓同一Apple key無限制重新attest。

本機 **21/21**：動態生成兩套獨立測試CA，完整驗證attestation鏈／nonce、公鑰與CMS receipt，完成註冊後再驗第一筆真ECDSA assertion；不是以假回傳值跳過密碼學。8個SQLite工作者及8個GCS假傳輸服務同送，皆只有一次狀態轉換與一個owner；不同有效證明競態只接受其中一份。涵蓋重開資料庫、重送保留counter、撤銷、challenge輪替競態、存儲失敗與失去回覆。公開驗證器仍拒絕合成根。10/10變異被捕獲；相關receipt19、憑證25、assertion21、state29、request7皆通過，本輪共122項。

**未完成的上架條件：** 尚無公開HTTP路由或iOS DCAppAttestService整合；未驗真GCS、實機Apple證明、Windows原生或遠端CI。公開challenge入口必須先有容量／速率／成本防護，否則任意key ID會製造pending物件。新key取得新owner，不自動遷移舊匿名紀錄，跨key恢復及配額防繞過仍需明確設計。媒體業務去重、跨實例撤回fencing、raw RGB-D路由整合未因本階段完成而解決。未部署。

證據：repo外 `lite-attest-enrollment-20261004/validation.json`、`reproduce.py`與來源快照；根與驗證時間僅在測試中注入私有驗證函式，正式RegistrationService沒有root/time驗證覆寫參數（clock只供狀態期限）。

## 2026-10-04 App Attest receipt 簽章與註冊綁定（第五階段，本機候選）

新增 `lite_attest_receipt.py`，初始註冊 receipt 驗證 CMS 簽章、憑證用途與鏈、App ID、client hash、公鑰／key ID、ATTEST 類型、建立時間不早於300秒及尚未過期。公開入口固定32-byte server challenge並取SHA-256，不接受請求指定信任根或時間。Receipt 信任 Apple Root CA G3（DER SHA-256 `63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179`），不同於attestation憑證的Apple App Attestation Root CA。支援Apple實際BER constructed indefinite封裝，但先限制大小、巢狀與項數；拒絕重複欄位、尾隨資料、錯誤簽章及不符的signed attributes。

新增固定版asn1crypto 1.5.1解析CMS；後台鎖檔相對本階段起點僅新增此套件，既有版本不變。執行期不呼叫openssl。依據：[Apple Assessing fraud risk](https://developer.apple.com/documentation/devicecheck/assessing-fraud-risk)。官方公開receipt樣本以歷史有效時間通過簽章、信任鏈及欄位驗證，以目前日期拒絕。該樣本client hash為範例的24-byte字串，只在私有測試入口作歷史fixture；不改本專案32-byte challenge雜湊協定來遷就範例。

本機receipt **19/19**、12/12變異捕獲；註冊25/25、assertion21/21、持久化29/29、請求7/7回歸皆通過，共101項。CI工作流已加入測試，但尚無本階段遠端CI／Windows原生結果。證據：repo外 `lite-attest-receipt-20261004/validation.json`、`reproduce.py`與來源快照。

**尚未接線：** receipt驗證器沒有建立註冊列、公開HTTP路由或iOS呼叫；仍需將憑證與receipt驗證結果綁入持久化註冊challenge的一次消耗與所有權交易，再接入assertion授權。未做Apple風險receipt更新、真機App Attest、真GCS驗證或部署；不能宣稱公開API已受保護。下方各段是歷史階段快照，其尚缺項以本段及後续整合狀態為準。

## 2026-10-04 App Attest challenge／counter 持久化（候選，未接公開路由）

`lite_attest_state.py` 已把assertion驗證與狀態更新組合：從可信註冊列取得安裝所有者、公鑰、App ID、環境及audience；伺服器簽發32-byte隨機challenge，最多同時4筆、有效120秒。用實際請求重建clientData並驗簽，成功後在**同一筆條件式更新**保存新counter並消耗challenge，確認更新成功才回傳Admission。驗簽前與提交前都检查期限；遠端提交仍可能有網路延遲，這不是精確到提交瞬間的雲端時鐘保證。未知key不建立資料，狀態缺欄、損毀、錯誤環境／所有者、公鑰不符都停止，不補預設值或回退匿名模式。

SQLite以真實本機檔案及交易跨執行緒／程序比較revision，只供本機／測試，不能用Cloud Run暫存檔或GCS FUSE代替雲端持久化。GCS以每key一個JSON物件保存aggregate；讀取metadata後固定generation下載並驗CRC32C，寫入強制 `if_generation_match`，412才視為競態並重讀。IAM拒絕、讀取中的404、checksum／網路錯誤及「已提交但回覆遺失」均為unavailable，不當作不存在或成功。不提供列舉／刪除安全狀態的API；撤銷以保留原key與counter的tombstone禁止重新啟用。需配置獨立可更新的security namespace，不能寫進鎖定的稽核紀元桶。依據：[GCS request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)。

驗證 **29/29**：本機SQLite重啟後不重放；8個執行緒、4個真實獨立程序同送一份assertion皆恰好1個成功；8個GCS服務物件共用假傳輸也只有1個成功。不同challenge使用相同counter、消耗後用更高counter重簽同challenge、驗證中過期、撤銷競態、未知結果／寫入失敗、損毀狀態皆涵蓋。12種破壞全部捕獲；其中首輪「GCS回舊generation」只測新建，因另一個零值檢查擋住而漏抓，補既有物件覆寫反例後捕獲。註冊25/25、assertion21/21、請求綁定7/7、撤回17/17、測試隔離22/22亦通過。

**驗證邊界：** 測試預置的是合成「已完成註冊」列，receipt digest欄位只是可信註冊結果的索引，不能替代receipt驗證。本模組尚無註冊建立API、iOS呼叫或公開路由接線，GCS只測注入的傳輸模型，未佈建／寫入線上安全狀態。Counter消耗不等於業務操作exactly-once：逾時需取得新challenge／assertion，媒體及修訂本身仍須使用穩定request ID與回執去重。Admission先成功、之後才撤回時，已在執行的媒體寫入還需要fencing；本模組的key撤銷不能單獨證明研究資料完整刪除。註冊receipt、安裝配額、防濫用、跨key所有權與真機端到端仍屬上架阻擋。

證據：repo外 `lite-attest-state-20261004/validation.json`、`reproduce.py`，包含本機並行與GCS假傳輸結果，未宣稱遠端CI或Windows全套通過。

## 2026-10-04 App Attest 註冊憑證驗證（本機候選，尚無註冊API）

`lite_attest_registration.py` 的公開函式只信任隨程式封裝的Apple App Attestation Root CA，DER SHA-256固定為 `1cb9823ba28ba6ad2d33a006941de2ae4f513ef1d4e831b9f7e0fa7b6242c932`。來源為[Apple Private PKI](https://www.apple.com/certificateauthority/private/)；沒有從請求指定root／驗證時間、使用作業系統CA清單或執行期連網抓憑證的路徑。`.gitignore`只對這張公開root及Apple公開範例的兩張公開憑證開精確例外，其他PEM私鑰仍排除。

cryptography驗證leaf→intermediate→固定root鏈、有效期、CA限制、key usage及App Attest專用EKU。後續核對P-256公鑰與key ID、App ID、初始counter=0、AAGUID開發／正式環境、credential ID、COSE演算法／座標與憑證公鑰相同、category／bundle version，以及憑證nonce與 `SHA256(authData + SHA256(server challenge))` 相同。production政策僅允許TestFlight／App Store類別；development須配合development AAGUID與類別，不能混用。註冊challenge固定32 bytes，必須由後續伺服器狀態機簽發／核銷。

**修正前一階段的編碼假設：** Apple現行[validation guide](https://developer.apple.com/documentation/devicecheck/attestation-object-validation-guide)的公開authData在ED bit未設定時仍帶extensions，category以4-byte little-endian UInt32 bytes表示。現在註冊與assertion都驗證任何存在的完整尾端extension map，不因ED未設定就略過；接受這種UInt32表示與文字規格描述的CBOR整數，錯長度／相反byte order／不允許類別仍拒絕。這些欄位仍在nonce／簽章保護範圍內，未放寬版本或類別白名單；舊系統無extensions仍需可信伺服器明確政策。尚須真機確認各支援系統的實際封裝。

測試：註冊 **25/25**、assertion **21/21**、請求綁定 **7/7**；註冊16種破壞及assertion原12種破壞全部捕獲。Apple公開範例的憑證鏈在2026-04-21可通過，2026-10-04因leaf過期而拒絕。這只驗其憑證鏈，不把範例整份attestation、receipt或今日真機註冊當成通過；完整正例使用每次新生成的合成測試CA，公開函式會拒絕這個測試CA。測試用root／time注入只在私有測試函式，未暴露HTTP。

尚缺獨立receipt驗證、持久化challenge／所有權／counter原子狀態、裝置端DCAppAttestService與真機端到端，且未接公開路由、未部署。回傳receipt仍是未驗證的opaque bytes，不能直接拿來完成註冊。CI已加入測試與fixture路徑觸發；遠端CI及Windows原生執行尚未驗證。證據：repo外 `lite-attest-registration-20261004/validation.json`、`assertion-regression/validation.json` 及來源快照。

## 2026-10-04 App Attest assertion 驗證（本機候選，尚未接入授權）

`lite_attest_assertion.py` 使用 cryptography 的 P-256／ECDSA 驗證。輸入為已註冊公鑰、其 key ID、可信 App ID／版本政策、先前 counter，以及由伺服器實際請求重建的 clientData。核對公鑰雜湊、RP ID、嚴格遞增 counter、簽章與 extension 中的 validation category／bundle version。**2026-10-05 更正：** 本段原先主張以 Prehashed(nonce) 避免再雜湊，已被兩筆真實 Apple assertion 推翻。現以 ECDSA/SHA-256 驗證 nonce；獨立低階正例是 Prehashed(SHA256(nonce))。舊合成 fixtures 與驗證器共用錯誤假設，不能作真機相容性證據，詳見 [實機驗證](lite_real_apple_attest_validation_20261005.md)。

CBOR 外層最多4096 bytes，authenticatorData最多1024 bytes；先限制字串／unsigned integer／map、巢狀深度與欄位數，拒絕tag（含共享循環參照）、indefinite encoding、尾隨資料，再由固定版cbor2檢查UTF-8和重複鍵。欄位順序與合法的非最短整數編碼不影響驗證。沒有把所有authenticatorData寫死成37 bytes；有extensions時必須驗其簽章內的值，未知欄位或flags目前拒絕，待真機證据再決定相容性。預設要求extensions；舊系統例外只能來自可信伺服器政策，不能由客戶端自稱版本決定，有extension但值不符也不能降級放行。

驗證：合成P-256 assertion **20/20**，請求編碼 **7/7**，雲端測試隔離 **22/22**，部署腳本靜態 **5/5**；12種破壞（跳過簽章／RP ID／key ID／counter／category／version、接受相同counter／重複CBOR鍵／尾隨資料、當時誤判的重雜湊、取消extension要求／flags檢查）全被當時合成測試捕獲；其中雜湊方向已於 2026-10-05 真機驗證更正，不代表舊測試能驗過 Apple 簽章。依賴鎖檔只新增cbor2 6.1.5，既有套件版本無變動；Linux CPython3.11及Windows CPython3.13 wheel均通過鎖檔SHA-256下載核對。此項不是Windows原生執行或完整容器建置結果。

**尚未完成：** Apple憑證鏈／attestation註冊、真機DCAppAttestService、receipt驗證、key與安裝所有權持久化、challenge過期及一次消耗、跨實例counter原子更新與撤回整合。函式回傳只代表對所提供可信狀態的簽章檢查，沒有更新任何狀態；若兩個請求讀到同一舊counter，兩者仍可能同時通過，因此不能直接據此執行上傳／刪除。未接線HTTP、未部署、未完成App Attest上架閘門。測試公鑰為動態生成，並非Apple認證裝置證明。

可重現證據：repo外 `woundai-competitive-review-20261003/lite-attest-assertion-20261004/validation.json` 及 `reproduce.py`。規格依據：[Apple server validation](https://developer.apple.com/documentation/devicecheck/validating-apps-that-connect-to-your-server)、[cbor2解析選項](https://cbor2.readthedocs.io/en/latest/api.html)。

## 2026-10-04 App Attest 請求綁定編碼（準備實作，尚未接入授權）

新增 Python `lite_attest_request.py` 與 Swift `LiteAttestRequest.swift`，產生同一份 clientData。版本前綴為 `woundlite.appattest.request/1` 加NUL，其後每欄皆為4-byte大端長度＋原位元組，依序為：audience、installation、32-byte key ID、32-byte challenge、request UUID、HTTP method、path、Content-Type、SHA-256(實際body)。Content-Type和multipart boundary不重新排序／正規化；JSON空白也屬body位元組。App使用SHA-256(clientData)作assertion的clientDataHash，不把body的hash直接冒充整個clientDataHash。

支援既有segment、annotation、revision POST與綁定同一installation的DELETE；拒絕query、百分比變體、非規範路徑、控制字元、錯長度key／nonce，以及DELETE非空body。上限32MiB只是encoder的記憶體界限；不是HTTP入口完整大小限制。匿名代碼或request UUID仍不提供身分證明。

後端整合必須從可信部署配置、已註冊金鑰和已簽發challenge讀取audience／installation／key／nonce，並以真正收到的method、完整路徑（拒絕query）、Content-Type和body重建；不可直接信任客戶端傳來的digest或另一份clientData。multipart解析前需保留原body，App以最終一次編碼的bytes同時產生雜湊與傳送。路由內的資料所有者必須由已驗證金鑰決定，不能讓body裡的anon_id換成另一人。

已完成Python7/7與原生Swift3組跨語言JSON／binary multipart／DELETE逐位元組及hash一致性。Release模擬器整合80/80、0 skip，測試期間Swift來源不變；初次命令缺ENABLE_TESTABILITY而建置失敗，補測試專用旗標後通過，正式Release設定未放寬。尚無DCAppAttestService呼叫、Apple憑證鏈驗證、assertion CBOR驗證、challenge/counter原子持久化、key撤銷或公開端點接線；目前函式不發出HTTP，也不改匿名降級行為。不可將這個編碼器或測試當作App Attest完成。

後续須按Apple最新規格驗RP ID、金鑰／nonce／環境／counter，並處理新系統的validation category與bundle version extensions；相容政策需以實際iOS證明驗證，不能照抄舊式固定長度authData判定。來源：[Apple server validation](https://developer.apple.com/documentation/devicecheck/validating-apps-that-connect-to-your-server)。

## 2026-10-04 撤回物件清單修補（本機候選，未部署）

重現：媒體物件寫入成功、lite_index append 失敗時，舊 DELETE 僅依索引刪除，會留下沒有索引的照片卻回200。刪除函式靜默未刪除時也缺讀回確認。新增6個反例在修補前失敗，證明既有測試未覆蓋這些路徑。

候選先持久化 withdrawal_requested，接著讀取新鮮帳本並列出 `lite/<anon_id>/` 實際物件。先驗證整份清單皆為該精確前綴、16位小寫hex影像ID與既有四種副檔名，才開始刪除；未知物件或越界清單回503，不猜測刪除。逐件核對不存在，最後重列清單；任何列舉、刪除、讀回或完成紀錄失敗都回503。原撤回標記繼續阻止後續順序式上傳，重試可以完成剩餘清理。完成後才寫deleted紀錄。

本機驗證：撤回17/17（含真正segment索引append失敗留下物件再清理、靜默刪除失敗、清單失敗、跨裝置／未知物件、清理中新增物件、完成紀錄失敗、快取索引）；查閱端6/6；segment既有62項、revision22/22、storage receipt9/9、quota12/12、raw store13/13均通過。真127.0.0.1 HTTP生命週期3/3，伺服器已停止；未寫入／刪除線上資料。

仍未完成：裝置持有證明／App Attest；跨實例寫入與撤回fencing；GCS版本／soft delete／備份清理；尚未公開的lite_raw資料包整合。最後清單為空只證明當次快照，不能保證最後讀取後沒有舊請求寫入。此修補不可單獨作為全球公開研究收集或「完整撤回」的放行證據。

證據：repo外 `woundai-competitive-review-20261003/lite-withdrawal-inventory-20261004/`。

## 2026-10-04 候選修訂端點（未部署）

`POST /api/v1/lite/annotation/revision`：JSON envelope 為 `anon_id`、`image_id`、正整數 `revision`、`research_consent: true`、`payload_json`（原樣 UTF-8 JSON 字串）。請求上限 256 KiB、payload 128 KiB。只接受已有影像且座標尺寸與 metadata 一致；不接受照片補傳。

Payload 必填 `polygons, image_w, image_h, surface_cm2, projected_cm2, source="manual", consent_version="2026-10-03.1"`；可帶 `volume_ml, max_depth_mm, wound_id, wound_side, wound_site`。數值需有限且在範圍內，輪廓點不超出影像；側別部位是固定代碼，不接受人名／自由文字。資料仍屬 `label_grade=lay`，不進醫療 retrain queue。

伺服器用 SHA-256(identity + image + revision) 前 16 hex 作 append-once slot，再以**完整 payload SHA-256**讀回核對；不同內容重用 slot 回 409。200 回覆必須含 `status=stored, image_id, revision, payload_sha256`，App 全部核對才更新已確認狀態。重試需使用完全相同的 payload 字串。不同 revision 可亂序抵達，查閱者取同安裝／影像的最大 revision，不按物件順序。

回應：400 格式／同意錯誤；404 原影像不存在或服務未支援；409 修訂內容或尺寸衝突；410 已撤回；413 過大；429 新修訂限流；503 寫入／讀回未確認。超時或 503 不能推論「沒有寫入」，以同一修訂重試。舊 `/annotation` 不具這項保證，候選 App 不回退呼叫。

本端點沒有解決匿名身分、防惡意攻擊、全量 RGB-D、正式持久儲存與撤回治理；詳見研究管線最新階段。後端新測試 15／15 離線通過（真 Flask/LocalStore 與 GCS 假傳輸），尚待候選部署與合成資料端到端驗收。


狀態：**端點已實作（2026-08-19，Windows/Backend）**，尚未部署。
本文件由 Mac/iOS 端起草（2026-08-18），Windows 端於 2026-08-19 回寫定稿欄位。

實作與提案的差異、以及**上架前的必要條件**見下方〈2026-08-19 實作回寫〉。

---

## ⚠ 上架前必須先解決：`anon_id` 擋不住任何有意的濫用

契約寫「以 App 附帶的裝置匿名代碼限流」。但 `anon_id` 是**客戶端自己產生的字串**，
改一個就換一個身分。所以限流：

- 擋得住：誤觸、失控的重試迴圈、單一裝置的異常用量
- **擋不住**：免費雲端檔案空間、每次呼叫都跑一次分割直接燒 Cloud Run 的錢、
  把影像灌進 GCS

實作已加上**來源 IP 的日配額**當第二道（IP 不是身分，CGNAT 之下整棟樓共用一個，
但它至少不是呼叫端說了算）。這仍然只是提高門檻，不是控制措施。

**正式對外開放流量前必須換成真正的裝置證明**（Play Integrity / App Attest）。
那件事後端單方面做不到，需要 App 端配合。在那之前這個端點不該公開。

把這段話放在文件最前面，是因為「限流有做」很容易被讀成「濫用有擋」，
而那不是同一件事。

## 產品前提（已定案 2026-08-18）

- 民眾版輪廓來源依**研究同意**分流：
  - 同意 → 雲端自動辨識；去識別影像＋LiDAR 深度幾何上傳供精度研究與訓練。
  - 不同意 → 完全離線手動圈選，資料不離機。
- 單一中心傷口（App 端取畫面中央輪廓，其餘丟棄）。
- 民眾版主數字＝表面積（tilt-invariant；phantom 驗證見 2026-08-18 誤差數據）。

## 端點提案

### POST /api/v1/lite/segment

匿名（無醫療帳號）、以 App 附帶的裝置匿名代碼限流。

Multipart 欄位：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `image` | file (jpeg) | 去識別傷口影像（≤2048 長邊，方向已烘進像素） |
| `client` | str | `"woundlite-ios"` |
| `anon_id` | str | 裝置匿名 UUID（首啟隨機生成，不連結任何身分；限流與撤回鍵） |
| `research_consent` | "true"/"false" | true 才可持久化保存；false 時**辨識完即棄**（不落地） |
| `depth_map_png` | str (base64), optional | 16-bit 灰階 PNG，值=mm，0=無效——**與 `docs/depth_capture_contract.md` 完全同格式** |
| `depth_conf_png` | str (base64), optional | 同上契約 |
| `camera_intrinsics` | json str, optional | 深度圖像素空間 fx/fy/cx/cy，同上契約 |
| `depth_scale` | str | `"0.001"` |
| `depth_format` | str | `"png16_mm"` |
| `measured` | json str, optional | App 端算得的 {surface_cm2, projected_cm2, tilt_deg, coverage, median_distance_m}，供後端比對研究 |

回應（200）：

```json
{
  "wound_polygons": [[[x, y], ...], ...],
  "image_w": 2048,
  "image_h": 1536,
  "confidence": 0.93
}
```

錯誤：`429` 限流（App 顯示稍後再試並退手動）；`5xx` App 一律退手動圈選。

### 設計原則

1. **consent=false 不落地**：這是同意分流的可信度基礎。民眾版的同意文案
   寫明「不同意＝資料不離機」，唯一例外是自動辨識本身需要影像過境——
   所以未同意者 App 端根本不呼叫此端點（直接手動），後端的 false 分支
   只是縱深防禦。
2. **與 depth_capture_contract.md 同 wire format**：深度欄位驗證邏輯
   （IHDR bytes 24/25 = 16/0 等）後端已有，直接重用。
3. 不動 `/api/v1/annotation`：那條是醫療版病患同意鏈，兩者不可混。
4. 撤回：`DELETE /api/v1/lite/data/{anon_id}`（後續版本；v1 先保留 anon_id
   欄位讓資料可撤）。

## 個人額度與商業模式（2026-10-03 更新，取代 2026-08-19 提案）

Jack 同意先採個人每日免費 5 次、邊實作邊驗證。機構／多人個案管理屬
WoundAI 機構方案範圍，不以超過 5 次直接判定為機構。

### 本輪已實作，尚未部署

- `LITE_LIMIT_ANON` 預設從 30 改為 5；仍可由部署環境覆寫，不能假定線上已是 5。
- 成功且有輪廓的辨識消耗一次；缺模型、500、無效影像、無輪廓不扣成功次數。
- `LITE_LIMIT_ATTEMPT_ANON` 預設 30：失敗、空結果與人臉退件仍計嘗試額度，
  避免以免費失敗請求反覆消耗 CPU。嘗試 IP 額度沿用 `LITE_LIMIT_IP`。
- 修正輪廓改用獨立 ledger，`LITE_LIMIT_ANNOTATION_ANON` 預設 30；不扣辨識額度，
  第五次辨識後仍可送修正。同一帳本的 IP 防濫用上限仍適用。
- 200 與配額 429 加入 `quota`：`limit`、`used`、`remaining`、`resets_at`（UTC ISO8601）、
  `scope=installation`、`operation=segment`。快照失敗可省略 quota，App 不猜測數值。
- App 顯示最近服務回覆的剩餘次數及轉為當地時間的重置時刻。舊服務、缺欄位、
  格式錯誤、App 重啟、切換服務或超過重置時刻都顯示尚待確認，不自行補成 5/5。
- 保留舊辨識 ledger 檔名，避免升版把當天用量清零；舊 ledger 無法分辨的修正紀錄
  保守保留到 UTC 午夜重置。既有用量大於新上限時 remaining 為 0。
- 本機拍攝、手動圈選、紀錄及資料撤回不以辨識額度收費。

### 明確尚未完成的上架／收費前條件

目前仍是匿名安裝代碼與先讀後寫的 ledger；不是跨實例原子預留，也沒有
request-id 去重／結果重放。並行請求可能超額、成功回應遺失後重送可能再扣一次。
本輪不能宣稱「重試不重扣」已驗證，也不得啟用付費點數或全球公開入口。
LocalStore 也不能作 Cloud Run 的耐久付費帳本。

下一階段需一併完成：App Attest、持久化且原子的 reserve/complete/release、
request-id 與 payload 綁定、跨日重試、逾時／未知結果的查詢恢復、全域成本上限、
實際服務回讀及併發／冷啟動故障注入。裝置證明不等於人身分。

### 收費建議，尚未建立商品或對外承諾

1. 先提供消耗型次數包，供偶爾超過每日免費額度的個人使用者；付費點數不設到期日。
2. 持續高用量者可再提供有明確每期額度的訂閱，不承諾無限雲端。
3. 優先扣每日免費，再扣訂閱當期額度，最後才扣次數包；若會使用付費點數，送出前提示。
   訂閱到期／取消不刪除另購未用點數或本機紀錄；不以付款解鎖研究撤回。
4. 價格、包數、訂閱額度先留為營運試算，實測成本與使用量後再定案。
   不放無法購買的按鈕、不將待審功能宣稱已可用。
5. StoreKit 2 與後端驗證 Apple 簽章、bundle/product/environment、退款／撤銷與通知重放；
   交易唯一鍵原子入帳，App 傳 `entitlement=paid` 不得增加額度。
   originalTransactionId 是交易關聯，不是裝置證明，不能代替 App Attest。
6. 已使用的 consumable 不靠 StoreKit 自動恢復餘額；後端需有可跨裝置恢復、
   防冒領的購買歸屬與餘額機制。必須先決定匿名使用與帳號恢復的產品契約。
7. 付費服務處理與研究保留／訓練同意須分開設計；本輪未改動現行研究同意流程。

本輪 Mac 實作依 Jack 已授權的跨端檢查與續作範圍進行；`Backend/`、`engineering/`
仍屬 Windows owner。owner_guard 規則未放寬，跨端變更須明確列入覆核，不能將其失敗報成通過。

### 本輪本機驗證（2026-10-03）

- 既有 Lite 契約測試：62 項通過；新增 Lite 配額測試：12/12。
- WoundLite Release 模擬器編譯成功；共用 Core 隨醫療版 Release 測試組建成功。
- LiteCloudQuotaTests：5/5，0 skipped（Release、ENABLE_TESTABILITY=YES）。
- CI gate 靜態測試：12/12；新後端測試已加入 p0-4-audit 工作流程，但尚未推送執行遠端 CI。
- parity 回傳 1：只有 Android versionCode 22／iOS build 25；與乾淨 HEAD d7e3617 的輸出完全相同。
- owner_guard Mac 回傳 1：api_lite.py 與 test_lite_quota.py 為 Windows 所有權；不宣稱通過。
- 未部署、未更新 TestFlight、未實機驗收；不將以上結果外推為重送去重、並行計數或支付驗證已完成。

參照（2026-10-03）：[Apple IAP 類型](https://developer.apple.com/help/app-store-connect/reference/in-app-purchases-and-subscriptions/in-app-purchase-types/)、
[Apple Review Guidelines 3.1.1 / 3.1.2 / 5.1.1](https://developer.apple.com/app-store/review/guidelines/)。

## PARITY 影響

無。此檔為文件提案；後端欄位落地時再依慣例更新 `docs/PARITY.md`
（預期為 backend-only 端點，Android 端無對應功能，需加宣告）。

## 過渡期：WoundLite 專用後台帳號（2026-08-18 決議）

匿名端點上線前，內測不共用 `dr01`，請後端管理者另建**最小權限服務帳號**
（建議名 `lite01`）：

- 權限：僅 `/api/v1/classify`（辨識）。**不給** annotation／病患／個案／同意書 API
  ——Lite 沒有病歷概念，帳號就不該拿得到那些資料面。
- 隔離：Lite 流量掛在獨立帳號下，與臨床樣本統計（n=20 追蹤）天然分開，
  之後清點研究資料也以帳號歸屬切分。
- 撤銷面：帳密若外洩只需停用 `lite01`，臨床端不受影響。
- 帳號建立與密碼由管理者（Windows 端）操作；iOS 端只在 Lite 設定頁
  「進階」由使用者自行輸入，App 與 repo 不儲存明文。
- 正式上架仍走上方匿名 `lite/segment` 端點（anon_id 限流）；帳號只是內測過渡。

### 帳號權限機制（回答「需不需要特別權限或類別」）

- **正式民眾流量：不需要任何帳號**——`lite/segment` 設計為匿名端點，身分即
  `anon_id`，控制手段是限流與 consent 分流，不是登入。
- **過渡帳號 lite01 需要權限分級**。現行後端帳號應該是單一類使用者（登入即
  全 API 可用），直接建普通帳號會讓 Lite 憑證拿得到病患/同意書 API——
  憑證裝在民眾側 App 上，這不可接受。建議 Windows 端擇一：
  - **方案 A（建議）**：`users` 表加 `role` 欄（`clinician` 預設／`lite`）、
    JWT claim 帶 role；`annotation`／`patients`／`consent`／`restore` 等端點加
    `role == "clinician"` 檢查，`classify` 與 `health` 不限。改動小
    （一個欄位＋一個裝飾器），並為未來多角色鋪路。
  - 方案 B（最小）：普通帳號＋約定 Lite 只呼叫 classify。無強制力，
    僅可短期內測，不可帶到 TestFlight 之外。

### 2026-08-19 後端實況核對（Mac 端讀碼確認）

- `/api/v1/classify`＝`@jwt_required()` **無角色檢查** → 任何已登入帳號可辨識、
  影像以 sha1[:16] 內容雜湊存 `images/`（去識別檔名）。
- `auth_users.ROLES` **沒有 lite 角色**（physician/nurse/assistant/engineer/admin）。
  ⚠ 目前的 `lite01` 必掛其中之一——assistant 有 `clinical.view`、engineer 有
  `audit.read`/`gcp.console`，都超過民眾版所需。**最小改法**：`ROLES` 加
  `"lite": "民眾版"`、不加入任何 `PERMS` 集合（敏感端點全查 perm 會自動 403，
  classify/health 只驗 jwt 照常可用），再把 lite01 改指 lite 角色。
- `/api/v1/depth` 要求 `annotation.submit`（僅醫師）＋既有標註綁定 →
  **設計上就不是給 Lite 用的**；Lite 深度／量測數值上傳仍以 `lite/segment` 為準。

## Windows 端交辦清單（推送後接手）

1. `lite01` 帳號＋上述 role 權限機制（方案 A）。
2. `POST /api/v1/lite/segment` 依本檔欄位表實作（深度欄位驗證邏輯與
   `/api/v1/annotation` 現有程式共用）。
3. `research_consent=false` 不落地（辨識完即棄）；true 落地時記 `anon_id`。
4. `anon_id` 限流（個人免費辨識預設 5 次/日/安裝）與 429 語意。
5. 完成後：回寫本檔定稿欄位、`docs/PARITY.md` 宣告 backend-only 端點、
   通知 iOS 端把 Lite 雲端路徑從 classify 切換到 lite/segment 並移除
   設定頁「進階（開發測試）」區塊。
6. 順帶確認：Mac 工作樹出現三個非 iOS 端的變更
   （`Android/bugreport-*.zip`、兩份 `wsm_stub.onnx` 131B→262KB）——
   若是 Windows 端的真實產出請在該端 commit，否則查一下同步來源。
7. **（2026-08-19 新增）** `ROLES` 補 `lite` 角色（見上方最小改法），並確認
   現有 `lite01` 帳號改指 lite；順帶告知它目前掛的角色以評估暴露面。
8. **（2026-08-19 新增）** `lite/segment` 落地時建議加**人臉偵測**（偵測到
   臉部即拒收並回可讀訊息）：協定層去識別擋不住畫面內容，App 端已加
   「只拍傷口」指引，後端這道是縱深防禦。
9. **（2026-08-19 新增）** 2026-08-19 下午以 lite01 上傳的兩張印刷範例影像
   為內測件（無 anon_id、無 consent 標記）——正式統計時請按 actor=lite01
   時段清點或標記排除。

## 2026-08-19 實作回寫（Windows/Backend）

程式碼：`Backend/Flask/api_lite.py`（**獨立模組**——它是整個服務唯一不需要登入的
資料端點，用一個檔案名就回答得出「哪些程式碼是公開暴露面」）。
契約測試：`engineering/phase2/test_lite_segment.py`（32 項）。

### 與提案一致的部分

欄位表、回應格式、429 語意、`research_consent=false` 不落地、
深度沿用 `/api/v1/annotation` 的 16-bit PNG 判準——皆照提案實作。

### 提案沒寫、實作補上的

| 項目 | 為什麼 |
|---|---|
| 回應多 `stored` / `image_id` | 讓 App 能對使用者**據實**說明這張照片有沒有被保存。同意分流講給人聽才有意義；契約原本的回應看不出來 |
| 來源 IP 日配額（預設 200） | `anon_id` 可偽造，換一個就換身分。IP 不是身分，但不是呼叫端說了算 |
| **IP 一律雜湊加鹽後才落盤** | 原始 IP 是個資，而這是民眾健康 App。為了限流而長期保存真實 IP，本身就是一個要交代的資料蒐集 |
| 先查配額再記錄 | 反過來的話，**被擋下的請求也計入配額**，那使用者被擋一次之後永遠解不開 |
| `DELETE /api/v1/lite/data/<anon_id>` | 提案列為「後續版本」，但沒有它，落地的資料就撤不回來。既然要收就要能刪 |
| 人臉偵測（OpenCV Haar，可用 `LITE_FACE_REJECT=0` 關閉） | 交辦第 8 點。**見下方限制** |

### 落地路徑與撤回

`lite/<anon_id>/<image_id>.{jpg,json,depth.png,conf.png}`，索引 `lite_index.jsonl`。

以 `anon_id` 分前綴是刻意的：撤回需要一個可執行的鍵。代價是同一裝置的影像被歸在一起
——這是「可被遺忘」與「不可連結」之間的取捨，契約選了前者。

撤回端點**同樣是匿名的**：任何知道某個 anon_id 的人都刪得掉它。要求證明「你就是那個裝置」
就需要一個身分，而那與匿名互斥。風險方向是安全的（惡意刪除損失的是資料量，不是隱私），
反過來（無法刪除）才不可接受。

### ⚠ 人臉偵測的實際能力

用 OpenCV 內建的 Haar 正面臉分類器，參數刻意保守（`minNeighbors=8`、臉需占畫面 ≥12%），
因為**誤判的代價是把一張合法的傷口照片退掉**，而民眾版使用者只會覺得壞了。

它抓不到：側臉、部分遮擋的臉、以及所有**非人臉的可識別物**
（證件、名牌、刺青、病房門牌、背景中的人）。

**這是縱深防禦，不是保證。** 真正擋得住的是取景指引與「只拍傷口」的流程約束；
後端這一層是補網。**不可以拿它當作放寬前面那一層的理由。**

契約測試刻意**不驗**它的召回率——用測試去背書一個抓不全的偵測器，
會給人錯誤的安全感。

### 交辦清單狀態

| # | 項目 | 狀態 |
|---|---|---|
| 1 | `lite01` 帳號＋role 權限機制 | ✅ `lite` 角色已加（權限全空）；主控台補上「改角色」。**部署後**把 lite01 由 physician 改過去 |
| 2 | `POST /api/v1/lite/segment` | ✅ |
| 3 | `research_consent=false` 不落地 | ✅ 契約測試直接數儲存目錄的檔案數驗證，不只看回應 |
| 4 | `anon_id` 限流與 429 語意 | ✅ 雙軌（anon_id ＋ IP）。⚠ 見文件最上方的上架前條件 |
| 5 | 回寫定稿欄位／PARITY 宣告／通知 iOS 切換 | ⏳ 本節即定稿。**PARITY 尚未宣告**——iOS Lite 目前仍走 classify，還不是落差；等 iOS 切過來、Android Lite 未跟上時才宣告（過期的宣告比沒有宣告更糟） |
| 6 | 三個非 iOS 端變更的來源 | ✅ 已查明：`.gitattributes` 把 `*.onnx`/`*.zip` 掛了 LFS，Mac 的 clone 沒有 git-lfs 所以拿到 131B 指標。請跑 `git lfs install && git lfs pull`。bugreport 已移出版控 |
| 7 | `ROLES` 補 `lite`＋告知 lite01 目前角色 | ✅ 見 `docs/handoff_2026-08-19_windows_to_mac.md` 第四節（掛 physician，暴露面已列出；稽核查證：只有 8 筆 login，無 annotation） |
| 8 | 人臉偵測 | ✅ 已實作，能力界限見上 |
| 9 | 兩筆內測影像標記排除 | ⏳ **目前做不到**：`classify` 全程不寫稽核，稽核裡沒有 classify 事件，只能靠 `images/` 檔案時間推而推不出操作者。Windows 端會補 `image_stored` 稽核 |

## 地端模型保留槽（iOS 端已鑄好）

`iOS/WoundLite/Models/MODEL_SPEC.md`＋`LiteLocalSeg.swift`：模型成熟後把
`WoundSegLite.mlmodel` 放入該目錄、補推論一段即自動啟用
「地端 → 雲端 → 手動」優先序，離線可自動圈選。訓練資料即本契約收集的
去識別影像＋醫療版醫師 GT。

## iOS 端現況（已完成）

- `iOS/WoundLite/`：target、同意分流、手動圈選、深度量測、本地紀錄。
- 雲端路徑暫以醫療 `/api/v1/classify`＋開發帳號驗證（Lite 設定「進階」），
  正式端點上線後改指 `/api/v1/lite/segment` 並移除開發區塊。


## 2026-10-04 候選：逐資產儲存回執（未部署）

`POST /api/v1/lite/segment` 保留 `stored` 的既有語意：照片已保存，不代表完整 RGB-D。
完成一般辨識流程後新增 `storage_receipt`，schema_version=1，含 image、metadata、depth、validity_mask；值為 stored、not_requested、not_provided、rejected。`rgbd_validation` 固定 not_performed，不能以 stored 推導校準、配準、真值或研究資格。

深度／遮罩成對先驗證後寫入：有界 Base64、PNG header／位元深度／灰階、尺寸上限 2048×2048、完整 chunk 驗證與像素解碼、遮罩尺寸相同。深度必須 16-bit，遮罩 8-bit；無效遮罩不再默默略過。儲存失敗向上回 500，不回成功回執；可能留下未確認的部分物件，尚未實作跨物件原子交易或自動清理。

推論可以成功而 depth=rejected；照片保存與深度完整性分開告知。舊版 App 忽略新欄位仍只知道照片是否保存；iOS 32 尚未解析此回執，仍顯示完整深度保存未確認。mask=None 的既有提前返回路徑為 stored=false，不提供此回執，客戶端應按 unknown 處理。不要用缺欄位推測成功。

這份回執未驗證內參、格式與尺度欄位的一致性、鏡頭畸變或來源身分；不等同「可訓練」。新測試 test_lite_storage_receipt.py 已加入 p0-4-audit workflow，但遠端 CI 尚未執行。


### App 候選 33 的解析（2026-10-04，待實機解鎖驗收）

LiteStorageReceipt 只接受 schema_version=1 與完整已知狀態；布林版本、未知版本／狀態、缺欄位不當作有效證據。回覆與 LiteCloudBinding 一起保存，紀錄頁可見，舊 binding 不會自行產生回執。影像／深度／遮罩保存仍不宣稱配準或精度通過。辨識空輪廓亦顯示保存狀態。

已用候選 Flask 真實回覆餵入相同 Swift 型別與文案函式：saved、missing、rejected、no_consent 4/4 通過。Lite 一般 Release build 成功；iPhone XCTest 已編譯但卡在「Unlock J-iP16PM to Continue」，不可算實機通過。尚未安裝普通 Release、未部署後台；現行服務缺回執時仍顯示深度保存未確認。


### 2026-10-04 撤回後重新上傳：候選驗證

新增 `test_lite_withdrawal_admission.py`，實際 Flask route＋暫存 LocalStore，8/8 通過。撤回即使没有標註仍先寫入拒收標記；分割前及推論後保存前重新讀取，舊標註入口也拒收。讀取失敗／格式錯誤回 503，已撤回回 410。相關回歸：保存回執 9/9、修正同步 16/16、額度 12/12、分割 62 項通過。

限制：這不是跨物件交易，保存過程與撤回的競態尚待 fencing；舊標註列尚未物理移除，anon_id 尚缺持有證明。同一安裝碼撤回後不會因重開研究選項而重新接納，重新同意協議尚未實作。修補僅在本機候選，未部署。

Build 33 實機 XCTest 本輪在建立測試連線前終止（runner exit 74 / xcodebuild 65），不是測試通過；Release 編譯及 Flask→Swift 4 組互通結果仍成立。解鎖後需重新執行獨立測試，不能沿用已結束程序。


### 修正同步與撤回的交叉驗證（2026-10-04）

先新增反例，原程式 21 項測試出現 8 個失敗斷言：只有 index 撤回、刪除未完成、撤回中、index 無法解析、寫入中出現 index 撤回時，revision 仍可能回成功。已改成與 segment/legacy annotation 共用 fresh gate，寫入後也查驗；保留最終 labels 讀回的撤回檢查，標註選取排除三種撤回狀態。

最終 revision **22/22** 通過（多一項最後讀回才出現撤回的案例）；相鄰回歸 withdrawal 8/8、receipt 9/9、quota 12/12、segment 62 項通過。這是本機候選程式與隔離儲存測試，不是線上部署驗收。仍無跨物件原子 fencing，晚到撤回可能讓已落地標註需另行清除，不能宣稱並行撤回完整解決。


### 後台查閱端撤回一致性（2026-10-04）

新測試先重現 7 個失敗斷言：撤回但照片尚未實體清除時，工程師仍可讀照片；只查單一 ledger 導致列表、輪廓／統計未一致排除。候選修補讓 image/preview 在認證及參數檢查後查驗撤回，records 以 fresh index + labels 聯集排除三種撤回狀態，失敗回 503，不再容忍損壞索引輸出不完整統計。SVG 回覆增加 private, no-store。

新 reader suite 6/6（含未撤回的另一安裝代碼仍可讀、匿名者仍 401、索引損壞拒絕）；相鄰 withdrawal 8/8、revision 22/22、receipt 9/9、quota 12/12、segment 62 項通過。均為隔離 LocalStore/合成圖；尚未部署。不代表已完成跨物件原子撤回或歷史標註實體刪除。


### 候選整合驗證（2026-10-04）

Mac 執行既有 `tools/windows/run_python_tests.py` 全量發現 78 支 engineering 測試檔，78/78 通過，原始碼 snapshot 前後一致、暫存 runtime 已清除。另跑 `run_backend_http_test.py`，20 個真實 loopback HTTP 合成資料檢查通過，原始碼未變、runtime 已清除。此 HTTP 套件驗證醫療 classify/receipt/annotation/撤回，不能冒稱已測 Lite 真實網路 E2E；Lite 本輪由 Flask test client 與暫存 Store 驗證。

parity 未宣告差異 0，static mobile logic 50/50；tool-only RGB-D preflight 與 mobile release contract 測試另跑（engineering runner 不發現 tools/ 測試）。不是 Windows 全套、不是遠端 CI、沒有部署。現在線上 revision API 的 404 尚未解除，手機待同步回執不能標為完成。


### Lite 真實 HTTP 生命周期（2026-10-04）

新增 `engineering/phase2/test_lite_http_lifecycle.py`，啟動只監聽動態 loopback port 的 Werkzeug + 正式 Lite blueprint，以每次隨機 response marker 確認伺服器身份，HTTP 客戶端拒絕 proxy、redirect 與非 loopback URL。暫存 LocalStore、合成 JPEG／深度／遮罩；固定合成分割器不驗證 AI 精度。

3/3 流程通過：multipart RGB-D 保存回執→手動畫圈修訂→原樣重送僅一筆→撤回四種附件→舊修訂與上傳回 410；同修訂不同內容回 409 且原資料不變；損壞深度回 rejected 且無深度檔案。cleanup 確認 server thread 已結束。

標準隔離 runner 已發現此檔，重跑 **79/79** 測試檔通過，原始碼 snapshot 未變、runtime 已移除；候選 CI 亦已掛入，尚未遠端執行。此結果覆蓋真實 HTTP serialization，仍不是完整部署映像／GCS／多實例／裝置持有证明驗收。


## 2026-10-04：撤回額度與 challenge purpose

- POST attest/challenge 新增 `purpose: withdraw`，只對已註冊身分發出；獨立兩個待用名額。新 challenge 儲存 purpose，舊未帶 purpose 的資料視為 assert；讀回拒絕未知 purpose／超出各 lane 數量。
- withdraw challenge 只允許同安裝簽章 DELETE `/api/v1/lite/data/{installation}`，不能上傳或提交 revision。一般 assert challenge 仍接受既有 DELETE，以保留舊請求相容性。
- DELETE 與撤回 challenge 共用獨立持久 budget key；一般 POST／register／assert 不消耗它。每 lane 各自使用 WOUNDAI_LITE_BUDGET_MINUTE／DAY，總 admitted work 上限可達兩倍。產品每日五次辨識額度不變。
- 撤回 lane 並非無限、也不是可用性保證；自身耗盡仍回 429 + Retry-After，狀態不確定回 503，不降級放行。尚未解決 writer 異常終止恢復、跨歷史副本／備份的刪除。
- iOS DELETE 使用 withdraw purpose。新狀態含 purpose，舊後端不能讀取；公開前需完成單一候選前後端驗收，不混用新舊後端 revision 共寫安全狀態。舊 client 仍請求 assert，無法保證一般額度耗盡時可啟動撤回。
- 本機證據：後端 177、Lite Release Simulator 135/135、五個變異反例。未部署，未宣稱真 GCS／Apple 已驗收。


## 2026-10-04：AI 最終量測修訂

`annotation/revision` 的 payload.source 接受 manual 或 ai；前者標 lay，後者標 ai_unverified。兩者都是 Lite 研究量測，均不可宣稱醫師確認／GT。AI source 不增加 label 統計、不進人工疊圖；records API 額外提供 measurement_source、measurement_revision、measurement，位置由最新量測讀取。人工／AI 切換必須使用下一個 revision，既有序號換內容回 409，完全重送只回原回執。研究撤回同樣清除此 ledger 的該安裝 live rows。

iOS 只在明確 manuallyConfirmed=false 且 source=cloud、有原雲端影像綁定時傳 ai。舊紀錄未知旗標／來源不猜測。編輯器完全沒改遮罩或已全部復原，保留原輪廓、面積與來源；不單憑按下「完成」產生人工標註。未知來源、多視角配準、實際精度、人工品質或 test-only 資料分類，均不由這個欄位推論。

本機測試 198 項後端與 138 項 Lite Release Simulator 通過；真 GCS／Apple／實機送出尚未驗收。


## 2026-10-04：當前同意版本的真實跨端核對

revision payload 允許精確 2026-10-03.1（既有 pending 相容）及 2026-10-04.1（当前 iOS）。未知版本仍拒絕；沒有讓 App 降回舊同意文案。測試讀取當前 iOS consentVersion 以抓升版落差，另保存真正 Swift 編碼 JSON → Flask 合成簽章路徑的回執、去重與撤回證據。此相容清單不表示自動授權新版研究用途，也不取代每次送出時的明示同意。


### 2026-10-04：影像隱私檢查不可用

公開 Lite 服務的影像檢查必須啟用。`/segment` 在已解碼影像後、模型推論與研究影像／RGB-D保存前檢查人臉：確定偵測到回400 `face_detected`；分類器缺檔、SHA不符、空分類器或執行失敗回503 `privacy_check_unavailable`，不得宣告stored或扣成功辨識額度。安全記帳（簽章counter、attempt）仍可更新，請勿把「未保存影像」解讀成完全沒有請求記錄。客戶端可稍後重試，或保留離線紀錄。

分類器為隨伺服器封裝的固定OpenCV資產，來源與授權見 Backend/Flask/privacy/README.md；不作runtime網路下載。端點通過只表示這個有限的正面人臉檢查未命中，不保證沒有識別資訊。文字、證件、刺青、背景、小臉／側臉的完整防護與影像模糊化尚未驗收。新測試 test_lite_face_privacy.py 用合成資料與簽章驗證控制流程，不提供偵測準確率證明。


### 2026-10-04：候選寫入封鎖協定（第二十二階段歷史紀錄；後續串接見下節）

`lite_fenced_objects` 與 `lite_fenced_manifest` 是為解除程序異常中止後永久pending的儲存層基礎；目前HTTP仍走原writer drain。它們不是身分驗證，也不能由使用者直接提供PinnedWrite當成授權。

完整切換必須遵守以下順序：

1. 驗證簽章、原安裝及研究同意；建立票證。每次寫入均透過 write_registered，在owner CAS內持久登記目標與原generation後才寫GCS；失去確認必須保留計畫。
2. RGB-D、JPEG、metadata、深度／confidence及研究index／label都使用owner範圍的新namespace。禁止混用原無generation的put_blob或shared ledger append；原臨床與稽核路徑維持獨立。
3. 撤回先永久關閉計畫登記，再seal_pending，之後對該owner已完成物件做全量inventory與逐一seal。不能僅以pending=0宣告刪除。沒有新寫入可登記後，才能核對所有現有payload均已清空。
4. 封鎖標記不得刪除，也不得由lifecycle自動移除；桶的versioning／soft delete／retention／lifecycle仍須依已有啟動檢查拒絕不符設定。回執需明確說明僅保留最少撤回標記，不能宣稱物件名稱全部消失。
5. 客戶端完成回執必須辨別新版寫入已封鎖與舊版writers_drained，不可以直接偽填writers_drained=true。讀取端須排除封鎖物件，不能用歷史generation、快取或空物件當作可用資料。
6. 舊namespace／仍跑舊程式的服務不可混合寫入新協定。正式切換前需明確決定既有資料遷移／撤回範圍，核對IAM、桶政策、真GCS generation行為及完整signed HTTP＋Swift回執。

本輪測試涵蓋本機CAS、真子程序終止及注入transport競態；不包含真GCS、Cloud Run程序終止、HTTP切換或Swift新版回執，以上六項仍是串接驗收要求。


### 2026-10-04：公開候選 HTTP 已串接寫入封鎖（第二十三階段）

公開 Lite profile 使用 `FencedPrivacy`，媒體與研究列位於專用媒體桶的 `lite_fenced/v1/<owner>/<kind>/<id>`；每次寫入均先登記原 generation，未知結果不得丟棄計畫。臨床／demo 不切換。安全額度記帳使用既有獨立儲存路徑，不作研究媒體。

GET 讀取不接受歷史 generation 或 sealed 內容。revision 相同回執重送不追加標註，衝突仍409；未修正的AI輪廓仍為ai_unverified，不混入醫師GT或人工修正統計。公開啟動若發現舊Lite研究資料，拒絕啟動等待明確遷移，不宣稱新版涵蓋舊資料。

有效 signed DELETE 先永久關閉 owner，封鎖全部未完成計畫，再清空完整 owner inventory。200 完成回執需包含：status=deleted、anon_id、deletion_scope=live_media、writers_drained=false、write_fence=gcs-generation-v1、live_payloads_remaining=0、非負整數 retained_markers、research_ledger_cleanup=live_rows_removed。這是阻止舊寫入的證據，不是宣告程序全已停止。物件名稱、owner與最少狀態仍保留；不宣稱歷史備份或研究衍生結果全部消失。任何讀取／清空／列舉失敗都不能回完成；503可重試。Swift同時支援既有排空回執，但新欄位不完整或版本未知必須拒絕。

上傳CRC校驗失敗不得讓SDK自動刪除永久標記；專用Blob覆寫delete為拒絕，保留計畫待撤回處理。封鎖標記不得有lifecycle、soft-delete、版本保留或外部寫者移除；仍需真桶政策與IAM驗收。

本機證據：HTTP14/14、後台231項、Lite141/141、5個破壞變異被抓。合成attestation＋SQLite＋注入GCS transport並非Apple/GCS服務證據。Linux容器、真雲端故障恢復及部署仍待完成；第二十二階段的「未接HTTP」狀態已由本節更新。
