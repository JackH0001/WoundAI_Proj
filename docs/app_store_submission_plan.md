# 上架計畫：醫療版 TestFlight ＋ 民眾版 App Store（2026-08-20 起草）

## 2026-10-05：Lite 專用私有候選部署方案待確認

GCP 登入／帳務／必要 API 可用，新候選資源名稱未使用；既有 build 身分保持停用，正式與 demo 未改。新的 49 檔包含真 Apple 發現的解析修復，source 834ef602…；方案 9bdec747…，指定獨立 runtime、媒體／安全桶、salt、映像庫，一次建置推送後以 digest 私有部署，min=0。來源／方案／部署測試 24/24、macOS 三模型與 raw／簽章探針通過，未執行新 Linux 工作或部署。先前授權限建置、不含推送／部署，新增資源與私有候選範圍待 Jack 確認；[可覆核方案與月預算](lite_candidate_deployment_review_20261005.md)。10 位測試者每日 5 次之試算約 US$3.26–8.78/月，不是硬上限或實際帳單。


## 2026-10-05：Lite 真實 Apple 裝置證明驗證與三項相容性修復

隔離開發診斷 App 已在 iPhone 16 Pro Max 取得真實 Apple 回覆，發現並修復 receipt sandbox 命名、assertion flags 0xc0 與簽章 nonce 雜湊三項合成測試盲點。真實 attestation／receipt／兩筆遞增 assertion 通過，請求竄改／counter 重播／開發證明套正式策略皆拒絕；本機 9 檔 179/179，6 個變異全捕獲。診斷 App 已移除。正式環境策略保持 production；未部署、未提交，不代表 Lite 正式 App ID／GCS 全鏈路完成。詳見 [實機驗證](lite_real_apple_attest_validation_20261005.md)。


## 2026-10-05：Lite 本機備份排除候選與實機驗證

Lite 量測 JSON 及共用影像／深度目錄加入備份排除，先設定保護與排除再原子替換；保留原路徑與舊紀錄。Lite 全套 149 通過、1 模擬器不支援項改由實機驗證、0 失敗；醫療 79/79；iPhone 16 Pro Max 隔離合成測試 7/7。診斷 App 已移除，未存取使用者既有 App 資料。設定補換機／刪 App 恢復限制；尚未发布或納入 Build 27。醫療資料庫／WAL 與完整備份還原仍待驗證，詳見 [紀錄](lite_backup_protection_validation_20261005.md)。


## 2026-10-05：醫療版 Build 27 已啟用內外部測試

包含本日拍攝導引／重新拍照文案與修邊多指／ROI 修復。固定來源 Release XCTest 79/79、archive／Distribution IPA／簽章／隱私 manifest 核對通過；實際上傳 exit 0。Apple 回讀 Build 27 VALID、BETA_APPROVED，WoundAI Internal QA 與 WoundAI 外部測試皆 IN_BETA_TESTING，測試說明逐字核對一致。project.yml 與 PARITY 已對齊 27，尚未確認手機安裝及實機手勢驗收。詳見 [發布驗證](medical_release27_validation_20261005.md)。


## 2026-10-05：醫療拍攝導引與修邊手勢候選修復

新增明確「臨床拍攝」箭頭入口、已有影像時顯示「重新拍照」；共用修邊器修正 ROI 擴張後雙指不能撤銷誤畫圓點、縮放平移錨點偏移與動態標頭引起的畫布尺寸變化。醫療 Release 78/78、Lite Release 141/141 通過；另加畫面測試後專項 7/7 通過並核對合成圖截圖。尚未實機重演或上傳，新修復不在既有 Build 26。詳見 [驗證紀錄](medical_ui_gestures_validation_20261005.md)。


## 2026-10-05：醫療深度內參漏洞候選修復

已補嚴格維度／參考尺寸、有限內參及正焦距、主點範圍、明確格式與伺服器統計重算。新增反例在修復前失敗，修復後深度端點 132 項通過，既有深度鏈 34、組織資料集 41、組織匯出 22 項通過，端點守門 pytest 8/8，10 個驗證器變異全捕獲。無效請求不改已存深度與索引；保留正常 iOS 傳送契約。已輸出兩檔獨立 patch 與 SHA-256 證據，詳見 [驗證紀錄](medical_depth_calibration_validation_20261005.md)。尚未提交、Windows 驗證或部署；不等於整體研究收案條件已完成。

## 2026-10-05：研究與公開發布關卡重核

Jack 確認 **IRB 尚未送件**。醫療 TestFlight 模擬測試可繼續，但不得把測試版核准當臨床研究或 Lite 民眾研究招募許可。已完成 [整體研究準備度覆核](ecosystem_research_readiness_20261005.md)：後端 13 個目標測試檔通過，WoundAI3D 研究匯入／分組 52/52 通過；新增反例仍重現醫療內參驗證不足。醫療深度是人工補傳，Lite raw 候選尚未獨立部署，兩來源至 WoundAI3D 的轉接／新鮮授權與 raw 封存仍未閉合。公開收案前須補技術契約、備份／撤回範圍、PI／IRB 計畫、TFDA 屬性與 Apple 提交實體核對。更正舊深度契約及 IRB 草稿中過時或過度保證的文字；未修改已發布政策或部署。

## 2026-10-05 10:29：醫療版 Build 26 上傳成功

Comet 已確認 ASC 登入有效，既有1.0(25)正在內外部測試，上傳前清單沒有26。重新核對既有archive執行檔與已匯出IPA的SHA-256後，以固定版號、destination=upload的Xcode exportArchive上傳，exit 0、Upload succeeded、EXPORT SUCCEEDED。沿用既有72/72 Release測試通過的archive，未以當前髒工作樹重新建置。

ASC讀回顯示1.0(26)於2026-10-05 10:29上傳，Apple處理完成。已儲存633字繁中測試說明，包括時間軸入口、結果／紀錄雙指縮放、修邊標註工具、nurse權限及示範LocalStore限制；加入WoundAI Internal QA與WoundAI外部測試既有群組（各1位），提交Beta Review並保留自動通知。隨後主清單與外部群組的builds頁均讀回1.0(26)「正在測試」，90天後到期；外測可取得更新。沒有將UI結果外推為正式App Store上架，也尚未確認Jack手機已安裝26。證據：repo外`medical-upload26-20261005/validation.json`、`external-group-readback.json`、`upload.log`與`test-notes-zh-Hant.txt`。


## 2026-10-05：研究增量封存與清理排程開始運作

依 Jack 授權新增 `tools/lite_research_archive.py` 與 14/14 通過的測試。舊 Lite 桶首批 16 筆、50 個內容去重檔案（15.58 MiB）已存入 repo 外 FileVault Mac 專用資料夾；SHA-256 全數讀回通過，重跑新增資產下載 0、64 引用沿用，撤回核對成功。15 筆不符同意／撤回篩選被排除。16 筆深度與信心 PNG 可解碼，但完整 raw RGB-D 確認數仍 0；全部維持隔離、不得直接加入研究訓練。

已建立每天台北時間03:00的對話 heartbeat `woundlite`（ACTIVE）：增量下載與撤回核對，每週完整性檢查、每月5日容量與保留覆核。依赖Mac與有效登入；首個排程尚待實跑。專用建置桶的兩份來源／日誌已核對本機備份並設定30天、固定名稱與建立日上界的lifecycle，讀回通過，2026-11-04起符合清理條件。研究桶沒有啟用自動刪除：需補第二份備份與capture retirement，避免破壞線上修訂與撤回。

詳見 [研究保存與成本計畫](lite_research_retention_plan.md)。大規模下載的外網費也已列入：1,000 DAU、每日1次、5MiB，單次匯出新增約US$21.97/月規劃費，不能只算移除雲端儲存的省額。

Comet JavaScript 已成功讀取外測群組，顯示 Jack 安裝1.0(25)；切到build清單時Apple重新導向login且authResult=FAILED，已提示重新登入。不是JavaScript權限仍關閉；尚未核對最新build清單或上傳26。


## 2026-10-05：發行 IPA 匯出與單次 Linux 建置成功

Jack 已授權先前固定 Lite 單次建置方案。plan SHA-256 94a562b6d67686457224fa5e2001b4d69b82c121809ae889a4b8b7e660980410、source manifest 16abf4b8a7da325105eb918be5cc4c625c2fd94dbda0cfc7b4d335d40dc706a0 不變。已建立 woundai-lite-build 專用服務帳號、woundai-lite-build-421209514056 來源／日誌桶、自訂 woundaiLiteBuildObjects 角色並只在該桶綁定。角色首次綁定受傳播延遲阻擋，讀回後同一綁定重試成功；未新增專案級角色。7 把現有密文、10 個既有桶、2 個執行身分及專案 IAM，共 61 項有效權限回覆均 CANNOT_ACCESS，逐一核對 accessTuple 的 principal/resource/permission。429 配額錯誤降速重查，未當作拒絕證據。

本機 gcloud 534 不支援 E2_STANDARD_2，在解析設定時即拒絕、未建立工作；改用官方 REST API 提交相同配置與來源（54 個上傳檔案，含 49 個 context 檔、manifest 及驗證／設定檔）。唯一建置 ID bf8aef7b-2992-4932-9026-d833b4928155，asia-east1，2 CPU／8 GiB、20 分鐘 timeout。沒有映像推送、Cloud Run 部署或公開存取變更。最終 SUCCESS，三步全部成功，實際起迄 84.579 秒，計算運算費約 US$0.00846（非帳單）。canonical golden、三個真實 ONNX 推論有限值、人臉分類器空白煙霧測試、raw RGB-D 回執／清除、安全驗證未配置時拒絕上傳均通過。探針在 --network=none、唯讀容器、暫存 LocalStore 執行；不是 Apple/GCS 公開服務驗收。既有模型有 tf_half_pixel_for_nn 棄用警告，未據此改模型或宣稱精度已驗證。

Xcode 登入恢復後，既有醫療版 1.0 (26) archive 已成功匯出 App Store IPA；codesign strict 通過，profile 無 ProvisionedDevices、get-task-allow=false、bundle com.woundai.app。IPA SHA-256 c364056c62c36e112a23bf10bc32dd261573dbd06ab73adbc6e7ea58758b820c。單次建置完成後已移除專用桶的建置角色綁定，停用 woundai-lite-build 並讀回 disabled=true；保留來源／日誌證據。前後 Cloud Run spec 比對無異動。尚未上傳 TestFlight。Comet 支援 AppleScript，但實際 execute JavaScript 仍回權限關閉，已提示「檢視 → 開發人員 → 允許 Apple 事件的 JavaScript」。此處是瀏覽器權限，不是網站登入失效。

新增 repo 外 lite-cost-budget-20261005 HTML／PDF／JSON 預算報告。1,000 DAU、每人每天 1 次，基準新增成本第 1 月 US$21.76、第 12 月 US$53.99；壓力情境 US$72.10／175.22，不扣免費額度且不含既有醫療/demo與3D訓練。均屬可重算假設，尚非真實 Cloud Run benchmark 或帳單。既有 demo minScale=1、2 CPU／4 GiB，純待命 730 小時毛額約 US$39.42。未代設帳務警示、未修改現有 minScale。


## 2026-10-04 22:58：外部發布阻擋已重查

醫療26與Lite34的Release archive已保留，當前目標未達成。再次實際exportArchive仍exit70：No Accounts、LY2F24ZM68缺Distribution憑證／私鑰；Chrome仍回Apple Events JavaScript關閉。Lite獨立建置授權未回覆，plan94a562b6與source16abf4b8保持原值，沒有执行雲端變更。這些相同的外部阻擋已跨連續三個目標回合；前兩輪完成了可獨立做的封存、驗證與政策草稿，現在不以重跑綠燈測試替代發布進度。

恢復順序：Xcode Settings→Accounts確認團隊與登入後重試既有archive匯出；Chrome開啟允許Apple事件JavaScript後核對ASC實際build／審查；新Lite Cloud Build先取得確切方案授權再作IAM與資源前檢。正式Lite政策、保存期限與研究責任／倫理適用性仍需負責方確認。本輪沒有新IPA、實機安裝、TestFlight上傳、公開政策發布或部署。完整阻擋稽核保存在repo外release-blocked-audit-20261004.json。

## 2026-10-04：Lite Release 34 與公開政策核對（未送審）

WoundLite 本機 Release archive 成功；com.woundai.lite、1.0 (34)、iOS17。build34僅命令列覆寫，project.yml預設仍33。codesign strict驗證通過，實際production App Attest entitlement存在且描述檔允許；get-task-allow=true，仍需Distribution匯出，不能直接當上架IPA。PrivacyInfo.xcprivacy與來源一致，照片／健康／裝置代碼均宣告linked，成品無XCTest。沒有上傳、安裝或雲端變更。資料見repo外lite-release34-audit-20261004/validation.json。

發布缺口已核對：AppSettings的WOUND_LITE預設仍是舊後台，獨立Lite服務及真實Apple/GCS端到端仍未驗收；Lite同意／設定頁沒有可開啟的隱私政策連結。不能把App Attest封裝成功當成公開服務已可用。

唯讀抓取公開 https://jackh0001.github.io/WoundAI_Proj/woundlite.html 得HTTP200，內容仍為2026-08-21。舊頁對完全匿名、原始IP不保存、全部紀錄AES加密與全量刪除的承諾不應沿用。site/privacy/woundlite.html已更新為中英候選草稿，明示未發布；補App Attest、原始深度與最終修訂、假名關聯、人臉檢查限制、Google/Apple處理、保留撤回標記及範圍外資料。未自動发布、未把它接成已生效政策。

依2026-10-04讀取的[Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/#privacy)，5.1.1(i)要求App內與ASC隱私連結；5.1.3(iii)/(iv)對健康人體研究要求明示研究內容／期間、程序／風險／利益、資料處理、聯絡與撤回，以及獨立倫理審查。是否涵蓋此研究須由負責方確認，不能只靠「個人參考／非診斷」免除。研究責任機構、期間、保存期限、成年人／未成年資格及核准／適用性判定目前未見權威證據；未虛構或替使用者簽核。現階段仍以合成／模擬測試推進。

## 2026-10-04：醫療版 Release 26 封存（簽章匯出仍待登入）

醫療版目前完整來源重新執行 Release XCTest **72/72，0 skipped**，`xcodebuild archive` 成功。實際封存的 bundle `com.woundai.app`、版本 `1.0`／build `26`、最低 iOS17；commands以 `CURRENT_PROJECT_VERSION=26` 覆寫，project.yml預設25尚未更動。`codesign --verify --deep --strict` 通過，內含的 PrivacyInfo.xcprivacy 與來源逐位元組相同。archive目前仍是開發簽章（get-task-allow=true），需成功Distribution匯出才可作上傳包。

實際 `-exportArchive -allowProvisioningUpdates` 回70：Keychain missing Xcode-Username、No Accounts，以及未找到 LY2F24ZM68 的 iOS Distribution 憑證／私鑰。這是本轮实际錯誤，不從網站登入狀態推測。已提示Jack在Xcode Settings→Accounts重新確認帳號／團隊；不索取帳密。Chrome ASC讀取也仍明確回覆 Apple Events JavaScript關閉，已提示操作位置，未取得新審查狀態。

唯讀核對 GitHub：PR #16 仍OPEN／Draft、head d7e3617、7個check SUCCESS；main仍4005f39。這些CI只涵蓋已提交的head，不涵蓋本機未提交的醫療UI／Lite候選。

本輪未產生IPA、未上傳、未安裝或修改手機資料，也未改雲端資源。待恢復Xcode登入後可重用這份archive匯出；上傳前仍須確認build26未使用與審查metadata／測試群組。來源SHA清單與證據保存在repo外 `medical-release26-final-20261004/`。Lite最新49檔建置包的獨立雲端資源及一次Cloud Build授權問題仍待回覆；舊demo部署授權不替代新資源授權。

## 2026-10-04：持久寫入封鎖第二十三階段（已接候選 HTTP，未部署）

公開 Lite profile 現在使用 FencedPrivacy／FencedMedia；醫療與 demo profile 仍維持既有協定。JPEG、metadata、raw RGB-D、舊式深度 PNG／有效像素遮罩、研究 index／label 均走新 owner namespace 與持久寫入計畫。revision 重試仍只保留一筆相同回執，不重新上傳影像。公開服務啟動會拒絕含舊研究 namespace 的資料桶，不默默把既有資料排除在新版撤回範圍外。

撤回會先關閉新計畫，再清理未完成計畫，最後完整列出已完成物件、逐一清空內容並讀回。回執區分 `writers_drained=false` 與 `write_fence=gcs-generation-v1`；永久零位元組標記保留以擋遲到寫入，不宣稱所有物件名稱消失。Swift 要求新版回執的範圍、剩餘內容數、標記數與研究列清除結果均齊全，未知／缺漏不得降級當成完成。

另外攔住 GCS SDK 大型分段上傳 CRC 失敗時的自動 delete：該 SDK 路徑沒有 generation 前置條件，可能刪掉同時建立的撤回標記。專用 Blob 類別拒絕這種刪除，保留未確認計畫供撤回清理；CRC32C 驗證維持開啟。測試直接走安裝 SDK 的錯誤處理方法，注入 checksum 失敗並確認沒有呼叫遠端 delete。

驗證：後台 13 套，169 項 unittest＋62 項既有 Lite 檢查，合計 **231 項通過**；其中新 signed HTTP 整合 **14/14**，涵蓋所有媒體類型、重送、跨 owner 隔離、回應遺失、重新建立 coordinator、inventory 中斷及實際暫停中的 HTTP 上傳。撤回完成後恢復舊上傳，無法恢復任何 payload。五個關鍵變異皆由失敗斷言捕獲；首次未登記計畫變異揭露測試只跑 immutable raw 路徑，已補普通 JPEG 路徑，重跑捕獲。Lite Release Simulator **141/141，0 skipped**。static logic50/50、parity未宣告差異0、diff check通過。

醫療版五個 UI 檔與先前72/72驗證版本雜湊一致：醒目時間軸按鈕、修邊標註標題、統一重做圖示及結果／紀錄圖片縮放保留。本輪未重新安裝實機、未上傳 TestFlight。

新候選 context **49 檔**，manifest `16abf4b8a7da325105eb918be5cc4c625c2fd94dbda0cfc7b4d335d40dc706a0`；獨立建置方案 `94a562b6d67686457224fa5e2001b4d69b82c121809ae889a4b8b7e660980410`。三模型與人臉分類器在 macOS 原生探針通過；探針使用 LocalStore，與注入 GCS transport 的 HTTP 測試分開記錄。repo 外 `lite-fenced-http-20261004/validation.json`、`mutations.json`、`lite-summary.json`、`build-review/plan.json` 提供完整證據。

**尚未完成**：Linux 容器、真 Apple／GCS／Cloud Run 中斷恢復及外部測試送審。這是新來源包，原第十七階段待授權包保持不變，不能用舊雜湊授權替換本包。無雲端資源、IAM、正式／demo部署變更。未知寫入仍保留票證；尚無「不撤回即可恢復所有卡住上傳」的維運流程。大型owner完整撤回可能超時，需重試並以讀回確認，不以時間推定成功。

## 2026-10-04：撤回後禁止舊寫入恢復第二十二階段（候選基礎模組，未接入 HTTP）

重新核對現況：PrivacyState 僅有 writer token、沒有寫入目標；服務意外中止後即使重啟也不能證明舊GCS請求不會晚到，因此不能用TTL清票。另Chrome的App Store Connect分頁仍因 Apple Events JavaScript關閉而無法讀取；沒有取得新的審查狀態，沒有擅自開啟該設定。

新增 lite_fenced_objects.py：固定owner／物件種類／識別碼、新 lite_fenced/v1 namespace，寫入綁定原generation，衝突不換新generation重試；封鎖將內容改成零位元組永久標記，連尚不存在的物件也建標記。這是必要的：把標記刪掉會重新允許遲到的 generation=0 create。讀回以目前live物件加generation前置條件，不選歷史generation來冒充成功；錯誤／權限不足／讀回失敗都拒絕。這套邏輯依據 [GCS request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)，本輪只用注入的transport驗證，非Google服務實測。

新增 lite_fenced_manifest.py：原generation先登記進owner CAS，再實際寫入，再確認，最後釋放票證。write_registered 封装此顺序；未確認的寫入／回應遺失不得丟掉計畫。撤回先關閉新登記，再逐一封鎖剩餘計畫的物件，讀回成功後才清票。每owner最多8個writer，每writer10個計畫；最大配置仍在現有16KiB安全狀態限制內。已完成writer的物件仍需後續inventory，seal_pending=0不是整個owner刪除完成。

驗證 **77項通過**，含新增物件16項＋計畫14項；7個破壞變異都有失敗斷言，無環境錯誤。實際子程序寫入SQLite計畫後被kill並等待終止，新實例重新讀回計畫、建立封鎖標記，遲到的原generation寫入被拒絕。另有雙thread暫停／恢復、先寫入贏競態、回應遺失、metadata與body間被撤回、owner／bucket錯置及容量檢查。static logic50/50、parity未宣告差異0、diff check通過。證據：repo外 lite-storage-fence-20261004/validation.json、mutations.json、各測試日誌。

**尚未解除上架阻擋**：兩個候選模組尚未接入api_lite、候選封裝或公開服務。既有PrivacyState未鬆綁、不因本輪測試通過就刪掉舊票證。未部署、未上傳TestFlight、未改GCP資源。下一步須完整串接所有媒體與研究索引／標註寫入、撤回inventory與讀取端、Swift完成回執，再做真GCS故障／恢復驗收。不能只改JPEG存檔而漏掉RGB-D、metadata或晚到的研究標註。

## 2026-10-04：Lite 人臉檢查失敗阻斷第二十一階段（本機候選，未部署）

發現並重現 fail-open：`_has_face()` 缺 cascade、載入失敗或例外時回傳 False，端點將其誤認為無人臉而繼續推論與研究保存。既有業務測試關閉 FACE_REJECT，因此未覆蓋此路徑。實際 macOS venv 的 opencv-contrib-python-headless 5.0.0.93 的 cv2/data 只有 __init__.py，與 [OpenCV 官方 issue #1244](https://github.com/opencv/opencv-python/issues/1244) 所列 wheel 缺檔一致；本輪未據此斷言已部署服務的檔案狀態。

改為三態：檢查成功且未偵測到才繼續，偵測到人臉回 400，無法確認回 503 privacy_check_unavailable；均在推論與影像／深度／研究 index 保存前阻擋。額度 attempt、簽章 counter 等安全記帳仍可前進。錯誤訊息不包含 exception/path，writer ticket 仍釋放，後續撤回可完成。公開 Lite profile 只容許預設啟用或 LITE_FACE_REJECT=1；medical profile 既有相容設定保留。

新增固定來源分類器（OpenCV 4.13.0 commit fe38fc608f6acb8b68953438a62305d8318f4fcd），原 Intel 授權保留在 XML，大小 930127 bytes、SHA-256 0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0。執行時讀取 repo 隨附 privacy/ 檔案並核對 SHA，不依赖 wheel 的 cv2/data，也不臨時下載。固定建置清單增加 XML 與來源说明；新 context 共46檔，manifest dc2c28a9ecb03b9384e771e1f3c1669859eb9b19f436881f26c6ef8ff5642a33。原待授權包不變，不能以其授權替換成此包。

驗證：新隱私測試13/13；8套後台測試91項 unittest＋62項 Lite 檢查，共153項，static logic50/50、parity未宣告差異0。舊程式反例得到200 != 503；4種變異（未知當乾淨、跳過端點阻擋、忽略檔案雜湊、允許公開服務關閉檢查）各有失敗斷言、無測試環境錯誤。新 context 的原生 macOS probe 通過真實 cascade blank smoke、三個 ONNX、raw回執與刪除；不是 Linux容器測試。證據位於 repo 外 lite-face-privacy-20261004/validation.json、regressions.json、native-probe.log、各測試日誌。

限制：分類器漏偵測與誤判率未驗證，不能保證去識別化；未實作側臉／小臉完整涵蓋、文字／證件辨識或模糊化。人臉檢查通過不代表研究資料已通過完整隱私驗收。未部署、未新增GCP資源、未上傳TestFlight。醫療版前一階段五個UI檔案雜湊仍與72/72通過版本一致；未為此次純後台變更重跑iOS測試。公開上架仍需處理writer異常中止恢復及真Apple/GCS/實機驗收。

## 2026-10-04：裝置入口與同意版本跨端驗收第二十階段（本機候選，未部署）

修正無 LiDAR 時的全 App 阻擋：硬體限制只作用於新拍攝量測入口，紀錄、設定、政策與撤回仍可進入；量測頁顯示限制與兩個導向按鈕。未加入無尺度照片估算面積或冒用前鏡頭深度。模擬器的無 LiDAR 路徑實際渲染驗證三個 tab 存在、沒有首啟研究同意頁蓋住入口，並匯出畫面做目視核對。

另外抓到前一階段獨立測試未覆蓋的真實契約落差：LitePrefs.consentVersion 已為 2026-10-04.1，但 api_lite revision 只接受 2026-10-03.1。前一階段的後端 fixture 使用舊值，Swift 測試只檢查本機編碼／假 HTTP 回應，所以各自綠燈仍可能真實 400。本輪後端加入精確當前版本，保留原已支援版本供持久 pending 重送；未知、空值、型別錯誤及尾隨空白仍拒絕，未採任意版本放行。

新增防退步測試直接讀取 iOS 原始碼目前的 consentVersion，檢查 manual 與 ai 都能被修訂入口接受。另從真正編譯執行的 Swift revisionJSON 匯出原始 JSON 附件（不是重寫 Python fixture），以其原始 UTF-8 bytes 經合成 App Attest 簽章送入 Flask：上傳 200、修訂 200、相同內容重送 200 且只有一列、SHA 回執一致、撤回 200 並移除該量測。相同原始 JSON 在修改前後端回 400 invalid_payload，已保存反例。原始 native payload SHA-256 為 68f0418612b6a16fe4a0e9f1baf8a52a837eeec51fb06dbda4c27400db8bbf23。

驗證：Lite Release Simulator **139/139，0 skipped**；本輪五套後端 **74 項 unittest＋62 項 Lite 檢查，共136項**；verify_logic 50/50、parity 未宣告差異 0、diff check 通過。附件 export 的 no matching attachments 是指沒有附件，並非測試 skipped。證據：repo 外 lite-device-consent-20261004/validation.json、lite-summary.json、cross-contract.json、baseline-cross-contract.json、attachments/manifest.json 與畫面。

範圍限制：Flask test client、合成證明、LocalStore；沒有真 Apple／GCS／使用者手機上傳驗收。未上傳 TestFlight、未部署、未新增 GCP 資源。當前舊建置包仍不含本輪修改；新發布候選需重封裝。writer 異常中止恢復、資料 test-only 分類、實機壓力、商店資料／雙語及真實後端驗收仍未完成。

醫療版 TestFlight：已重新開啟 Chrome 的 App Store Connect，但讀取被 Chrome「透過 AppleScript 執行 JavaScript 已關閉」阻擋。這是瀏覽器讀取設定，不是已證明 Apple／GCP 憑證失效；登入與 Beta Review 狀態尚未取得新證據。需要使用者在 Chrome「檢視 → 開發人員 → 允許 Apple 事件的 JavaScript」開啟讀取，並於 Apple 要求時自行登入；沒有代為改瀏覽器安全設定。

## 2026-10-04：AI 最終量測同步與未改遮罩來源第十九階段（本機候選，未部署）

確認資料收集缺口：原 LiteRecord.revisionJSON 只接受 manuallyConfirmed=true，所以直接接受 AI 圈選並存檔時，最終面積、傷口分組與位置不會送至雲端；原後端也只接受 source=manual。新增 payload source=ai 路徑，仍綁定已存影像與原安裝、同意、尺寸及固定位置碼，沿用持久修訂、SHA-256 回執及去重，不重傳照片或重跑模型。

後端把 AI 量測存為 label_grade=ai_unverified；不得當成人工標註。人工修正統計／人工輪廓疊圖明確排除 AI row；管理者紀錄 API 新增 measurement_source／measurement_revision／measurement，位置取最新量測；既有 lay 欄位保留人工來源。AI 後續手改用下一個 revision，不能覆寫原回執。Lite label ledger 一直與醫師 GT 佇列隔離；本輪未建立訓練匯出，未聲稱模型訓練資料已驗收。

同時修正僅進出編輯器就變成「手動圈選」：Lite 量測與紀錄編輯回呼使用實際 raster IoU；只有完全相同（1.0）才不更新，不以近似門檻略過微小修正。保留原多邊形與面積，避免重描近似造成漂移；繪製後復原回完全相同遮罩亦保留 AI 來源。真畫布 finish／undo → Lite VM 的合成測試驗證此行為。舊紀錄不回溯猜測；manuallyConfirmed 缺值或來源不明不自動宣告 AI。

驗證：12 套後端測試，136 項 unittest＋62 項既有 Lite 檢查，共 **198 項**；Lite Release Simulator **138/138、0 skipped**；static logic 50/50、parity 未宣告差異 0、diff check 通過。含真 Flask／合成簽章上傳、AI metadata 修訂重送去重、reader 的 lay 排除、撤回清除該量測資料。baseline 舊後端對 AI payload 回 400，新測試得到一個預期 400 != 200 斷言失敗，無基礎設施錯誤。移除 AI 排除及錯標 lay 的兩個變異均被斷言抓到；首輪誤用系統 Python 缺 Flask，未算作有效捕獲，已在既有 venv 重跑並分別保留日誌。

證據：repo 外 lite-ai-measurement-sync-20261004/validation.json、lite-final-summary.json、各測試日誌、baseline-repro.log、mutations-verified.json。未部署、未安裝新版至實機、未上傳 TestFlight。此為合成 attestation／LocalStore；不是今日真 Apple／GCS 驗收，也未核對使用者線上傷口資料。

撤回異常中止仍是公開上架阻擋：PrivacyState 的未釋放 writer ticket 無租約超時，服務程序消失可能永久 pending；這是避免舊 writer 晚到重建資料的安全取捨，不能只以 TTL 刪票證解決。需要 storage 寫入 fencing 或能證明所有相關 writer 已終止的受控維運恢復流程後，再做終止／恢復驗收。未將此列為完成。

第十七階段待授權包 b2a1e7e… 不包含第十八／十九階段修改，維持原快照不變。新的可部署候選須重新封裝並核對來源；不得把舊包建置成功外推為本輪驗證完成。

## 2026-10-04：撤回專用額度第十八階段（本機候選，未部署）

重現既有問題：一般服务額度耗盡後，即使 DELETE 簽章有效，也在驗證前被共用 budget 擋成 429。baseline-repro.log 使用修改前的三個後端模組，只有一個預期失敗斷言（429 != 200），沒有執行錯誤。

RequestBudget 新增獨立持久 `withdraw` lane；一般請求無法消耗這個計數。撤回挑戰使用 purpose=withdraw，最多兩個待用名額，與原四個一般挑戰分開；新挑戰只能用於該安裝的 DELETE，不能改拿去上傳／修改。簽章、安裝所有權、counter／challenge 防重放及 writer 排空仍需通過。iOS DELETE 主動請求撤回挑戰；舊 assert 挑戰仍可做簽章 DELETE，但舊客戶端在一般額度耗盡時仍可能無法取得新挑戰，因此需前後端一起驗收。

兩個 lane 各自使用設定的 minute/day 上限，總 admitted request 上限可達原設定的兩倍；未變更每匿名身份每日五次的辨識產品額度。這不是免費無限撤回或全天可用保證，撤回 lane 自身耗盡、網路／狀態儲存故障仍會拒絕並回傳錯誤／Retry-After。最多 4 KiB 的 challenge JSON 先解析以選 lane；格式錯誤仍计入一般額度，大型媒體先保留額度才讀取。

驗證：新撤回額度 **9/9**；12 套後端回歸合計 **177 項**通過；5 種關鍵破壞（共用額度、DELETE 回一般 lane、挑戰回一般 lane、撤回挑戰可上傳、共用待用挑戰名額）各以指定測試的失敗斷言抓到，無環境錯誤。Lite Release Simulator **135/135，0 skipped**。static logic **50/50**，parity 未宣告差異 0。初次測試意外收集了匯入的 HTTPTests 並在沙箱內無法開 loopback；已改用 module fixture，完整 HTTP 回歸在允許本機 socket 的執行環境通過。變異整套曾同時出現預期拒絕例外與斷言失敗，另保留每個指定反例的純斷言證據。

證據：repo 外 lite-withdrawal-budget-20261004/validation.json、baseline-repro.log、mutations-targeted.json、lite-summary.json 與原始日誌。未安裝新版至 iPhone、未上傳 TestFlight、未部署、未新增 GCP 資源。

本輪三個後端來源已異動；第十七階段待授權的 build manifest b2a1e7e… 仍是原封不動的封裝診斷快照，**不含此修正，不得當成最新發布候選**。若要建置此版必須重新封裝、核對及明示新雜湊，不能沿用舊 SHA 的授權偷偷換包。Chrome 唯讀核對仍無 App Store Connect 分頁，未取得新的 Beta Review 狀態。

## 2026-10-04：隔離 Linux 建置第十七階段（待授權）

完成離線方案、來源包雜湊驗證與實際 gcloud 待上傳清單核對：49/49 檔。建置方案 9/9、來源包 5/5、前處理 10/10；最終包原生 macOS 三模型／合成 raw 回執與清除探針通過。Linux Cloud Build 尚未執行。具體身分、桶、IAM、一次建置、成本與停止方式見 [建置審閱方案](lite_candidate_build_review.md)。

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

## 2026-10-04：醫療版介面對齊 Lite，開發驗收版 26

傷口個案改為獨立、全寬「查看傷口時間軸」按鈕（最低 48pt）；撤回照護同意後仍可查看既有時間軸。修邊標題改為「修邊標註」，共用工具列的重做採 SF Symbol `arrow.uturn.forward`。紀錄頁標題改為「紀錄檢視／重修補送標註」。醫療結果圖與紀錄頁使用與 Lite 相同的雙指縮放／移動／重設預覽；傷口輪廓與黃色校正框共用照片座標和縮放。紀錄照片尺寸缺漏或不符時只顯示原圖並說明，不畫可能錯位的輪廓。

驗證：Release 模擬器醫療版 **72/72**、Lite **113/113**，0 failure／0 skip；包含兩端共同執行的 6 項預覽驗證（圖層對齊、縮放、重設、更新、開關與異常座標）。verify_logic、parity_check、git diff --check 均回 0。首輪醫療測試因 canImport 選到舊 Lite 編譯產物而編譯失敗，已改用測試 target 明確編譯旗標並重跑成功。

真機 Release 建置及 codesign 驗證通過；以 build setting `CURRENT_PROJECT_VERSION=26` 產生 **WoundAI 1.0（26）開發驗收版**，已覆蓋更新到 J-iP16PM 並讀回版本。project.yml 的醫療預設仍為 25，正式下一次封版時需一併更新版本／PARITY 登記。這不是新的 TestFlight build；手機手勢與外觀仍需實際驗收，不能以單元測試當成觸控驗收。

本輪證據：repo 外 `woundai-competitive-review-20261003/medical-ui-alignment-20261004/validation.json`、兩份 xcresult、device build／install／launch 紀錄和來源 SHA-256。後台部署、撤回完整刪除與公開上架閘門仍維持前節狀態。

## 2026-10-04：撤回與晚到寫入隔離第十階段

新增持久CAS writer登記：簽章驗證後、寫入前登記；撤回永久封鎖新writer，既有writer未結束回202處理中，全部結束才清理可見媒體。管理端也讀privacy狀態，即使舊ledger寫入失敗仍不展示已撤回資料。250項後端測試及7種變異驗證通過，Mac verify_logic／parity_check回0。詳細契約與限制見 `lite_backend_contract.md`。

**仍未可送審：** 尚需簽署撤回UI、程序異常終止後pending票券的安全恢復、研究JSONL／歷史版本／備份與匯出資料的完整清理，以及實際雲端所有writer與IAM驗證。不能讓舊服務共享可寫namespace後仍宣稱沒有晚到寫入。尚未部署，沒有新的Apple提交紀錄。證據：repo外 `lite-withdrawal-fence-20261004/validation.json`。


## 2026-10-04：App Attest 業務接線第九階段

Lite segment／annotation／revision 現在經裝置簽章；新上傳綁伺服器installation，舊匿名紀錄不能證明owner時保留本機及pending，不改掛新身分、不自動重傳。研究同意前後檢查、相符回執及修訂去重均有整合覆蓋。Lite111、醫療66、後端HTTP21與revision22全部通過（合計220項）；沒有真機Apple認證或雲端候選握手證據。新增Lite production entitlement及固定audience `woundlite-research-v1`，須與部署端相同；簽署描述檔驗證另見本輪結果。

傾角超過25°提醒誤差可能增加，深度品質足夠仍保留參考值。建議角度內的精度範圍仍依實測證據，不把25°當精度保證，也不因43°單筆偏差要求使用者重做整套。簽署撤回UI、晚到寫入隔離、完整RGB-D與最終metadata仍未閉環。醫療ASC本輪仍回覆Chrome Apple事件JavaScript未開啟，尚無新的提交／外測啟用證據。詳見 `lite_backend_contract.md`；repo外 `lite-attest-business-20261004/validation.json`。


真機簽署補驗證：初次使用萬用描述檔失敗（缺App Attest），`-allowProvisioningUpdates` 成功取得 `com.woundai.lite` 專用開發描述檔。Release真機build成功、codesign verify通過；讀回App簽章的App Attest值為production，profile允許development／production，App ID為LY2F24ZM68.com.woundai.lite，audience符合固定值。這是開發描述檔的Release產物，不是App Store distribution IPA；尚未安裝手機、產生Apple真機proof或上傳TestFlight。詳見repo外 `lite-attest-business-20261004/device-signing.json`。

## 2026-10-04：iOS App Attest 客戶端第八階段

完成DCAppAttestService／Keychain／HTTPS adapter與持久化註冊狀態、完整HTTP週期序列化、安裝marker及共用client registry。Release iOS18.6模擬器97/97通過（新增17項、既有80項），含原生模擬器Keychain讀寫；未取得Apple真機證明。首次停用codesign造成6項既有加密儲存失敗，同來源恢復ad-hoc簽署後93/93，再補4項後97/97；錯誤及成功logs均保留。

尚未接到原有上傳／修訂／撤回，尚未設定App Attest capability及驗provisioning，沒有改線上服務或安裝手機新build。下一步為業務接線及舊匿名binding處理，再候選部署與真機握手。醫療版ASC仍待Chrome Apple事件JavaScript設定，沒有新的審查或提交證據。詳見 `lite_backend_contract.md`；repo外證據 `lite-attest-client-20261004/final.xcresult`、`validation.json`。

## 2026-10-04：App Attest HTTP 整合第七階段

Lite註冊、簽章授權、owner核對與全服務持久請求上限已接到Flask候選；管理介面查阅保留JWT＋audit.read。部署設定須明列App ID／build白名單／audience、獨立security bucket及分鐘／每日上限，缺配時503且health揭露，沒有匿名降級。真正app.py啟動驗證此拒絕行為；目前線上服務未變。

HTTP／budget／config21/21、App Attest合計143/143；相關隔離22、端點pytest8、撤回查閱6、修訂22、segment62全部通過，共263項。含真正127.0.0.1註冊與簽章上傳成功、重放401。未修改控制組通過後，12/12變異被捕獲；初輪runner環境標記與pytest啟動方式均已更正重跑。證據：repo外 `lite-attest-http-20261004/`。

**未部署：** 舊App沒有assertion，不能單獨上線這版後端。下一階段接iOS裝置金鑰／註冊／簽章與重試，再使用候選環境驗真機、GCS/IAM及既有資料遷移；媒體業務去重、撤回fencing、完整RGB-D仍未閉環。Chrome本輪再次明確回覆Apple事件JavaScript未開啟；無新Beta Review／外測群組證據，未提交或啟用。

## 2026-10-04：App Attest 原子註冊第六階段

憑證與receipt驗證已接到單筆pending→active註冊交易；owner由伺服器產生，challenge與金鑰同時更新，重送不重設counter或重新啟用撤銷key。21/21新增驗證、10/10變異捕獲；相關101項回歸通過，共122項。涵蓋完整合成密碼學註冊後第一筆assertion、SQLite及GCS假傳輸8工作者並行、寫入回覆遺失後重試。未接公開HTTP／iOS真機，未部署；仍需入口防濫用、舊匿名資料遷移、業務去重與撤回fencing。

醫療版：唯讀Chrome網址已確認進入App Store Connect TestFlight建置頁（不再是login authResult=FAILED）。但Chrome拒絕AppleScript執行JavaScript，故尚未讀到當前審查／外部群組狀態。已請Jack開啟「檢視→開發人員→允許Apple事件的JavaScript」；本輪未提交／啟用外部測試。證據：repo外 `lite-attest-enrollment-20261004/`。

## 2026-10-04：App Attest receipt 第五階段

離線receipt驗證已加入固定Apple Root CA G3、CMS簽章／憑證鏈、App ID、challenge hash、attested公鑰、ATTEST類型與有效時間檢查。真實Apple公開BER範例在歷史日期通過、目前過期拒絕；不把它當今日真機註冊。Receipt19/19、12/12變異捕獲，相關4套回歸82/82；本階段合計101項通過。鎖檔只新增asn1crypto1.5.1，未改既有套件版本。

尚未串入註冊challenge／所有權交易、HTTP授權與iOS真機；未部署、未提交新build。醫療版Apple登入仍待使用者完成，尚無新的外測狀態證據。來源、測試與限制見 `lite_backend_contract.md` 及repo外 `lite-attest-receipt-20261004/validation.json`。

## 2026-10-04：App Attest 持久化防重放第四階段

新增counter＋challenge同筆CAS更新與可信key撤銷；SQLite採本機交易，GCS採固定generation讀取、CRC32C及條件式更新。29/29通過，含8執行緒與4個真實程序的單一勝出、重啟後重放拒絕、GCS假傳輸並行、寫入結果未知與撤銷競態；12/12變異捕獲。相關註冊25/25、assertion21/21、請求綁定7/7、撤回17/17、隔離22/22通過。尚未接公開HTTP，合成註冊列不等於Apple receipt驗證；仍需註冊／iOS端到端、業務重試去重、撤回期間媒體寫入fencing、真GCS及Windows驗證。證據：repo外 `lite-attest-state-20261004/`。

醫療版進度：Chrome已恢復可回應，重新開啟 `apps/6814448065/testflight` 後實際導向 `/login?...&authResult=FAILED`。已請Jack在Apple頁面重新登入／2FA；未讀取密碼欄位，尚未取得Beta Review或外部群組新狀態，未提交／啟用。此登入限制不阻止本機後台工作。

## 2026-10-04：App Attest 註冊憑證第三階段

新增固定Apple根憑證的註冊驗證模組，檢查憑證鏈／有效期／用途、公鑰與COSE一致、nonce、App ID、AAGUID環境、初始counter及版本／類別。根憑證從Apple官方取得並固定DER fingerprint，不接受請求自帶信任根或驗證時間。依Apple公開範例補上UInt32 byte string與ED未設定但存在signed extensions的解析，原拒絕條件仍保留。

本機註冊25/25、assertion21/21、請求綁定7/7通過；註冊16/16及assertion12/12變異捕獲。Apple官方範例憑證鏈在歷史有效日通過、目前日期過期拒絕；沒有把此結果當作今日真機註冊通過。後續仍須receipt驗證、持久化一次challenge與counter更新、key所有權／撤回整合、iOS呼叫及實機驗收。未部署、未提交新build；醫療版ASC外部測試狀態本階段未取得新證據。證據：repo外 `lite-attest-registration-20261004/`。

## 2026-10-04：App Attest assertion 伺服器檢查第二階段

新增離線assertion驗證器，核對P-256簽章、公鑰／key ID、App ID、counter與版本extensions；CBOR拒絕重複鍵、循環tag、超限結構及尾隨資料。合成assertion20/20、請求綁定7/7、隔離22/22、部署靜態5/5通過；12/12變異捕獲。CI工作流與Windows依賴清單已納入，尚未執行遠端CI或Windows全套。新增cbor2固定版本的Linux3.11／Windows3.13 wheel通過鎖檔hash核對，既有後台依賴版本未變。

本階段沒有修改iOS程式或部署線上服務。尚缺Apple憑證註冊、真機呼叫、receipt、持久化所有權與challenge／counter原子消耗，不能宣稱App Attest已啟用或Lite可公開收集研究資料。醫療版外部TestFlight狀態本階段未取得新證據，仍需回到App Store Connect查核。證據：repo外 `lite-attest-assertion-20261004/validation.json`，規格與限制見 `lite_backend_contract.md` 最新段落。

## 2026-10-04：App Attest 請求綁定第一階段

Python／Swift已實作同版clientData編碼，綁定服務環境、安裝代碼、key、challenge、request UUID、HTTP method/path/Content-Type與實際body digest。3組JSON／binary multipart／DELETE跨語言bytes與hash一致；Python7/7，Release模擬器80/80、0 skip，Swift來源前後一致。workflow已納入Python測試，本輪沒有遠端CI run。

這是授權實作的第一階段，尚未產生真機key、驗Apple憑證／assertion、持久化challenge/counter或保護公開端點。後續必須完成所有權、重放／跨實例原子性、撤回及真機驗證後才能啟用。未改公開請求路徑、未部署或送審。

醫療版ASC查詢本輪以Chrome唯讀AppleEvent嘗試，回-1712逾時；無法確認當前Beta Review／外部群組狀態，不沿用歷史狀態冒充最新結果。證據在repo外 `lite-attest-request-20261004/validation.json`。


## 2026-10-04：後台撤回不再只依索引刪除

本機候選修正「媒體寫入成功、索引失敗」的孤兒物件漏刪，以及刪除後未核對實存卻回成功的問題。先撤銷後續接收，再驗證該安裝代碼實際物件清單、逐件刪除讀回與最後重列；失敗維持撤回狀態並回503供重試，越界或未知物件不猜刪。6個反例在修補前失敗；修補後撤回17/17、查閱6/6、segment62項、revision22/22、回執9/9、配額12/12、raw store13/13通過，另真loopback HTTP3/3通過。

只驗本機候選與既有假傳輸，未部署、未刪除任何雲端資料；匿名所有權、跨實例晚到寫入fencing及保留版本清理尚未完成，不能聲稱完整撤回或公開發布已通過。詳見 `lite_backend_contract.md` 與repo外 `lite-withdrawal-inventory-20261004/results.json`。


## 2026-10-04：沿用傷口4資料的原生幾何重播

使用現有兩張實體標準靶，不新增拍攝。以完整 `DepthAreaEstimator.swift`、`MaskTrace.swift` 在 macOS 原生編譯；只用 DTO 提供雲端毫米 PNG 解碼深度與實際內參、輪廓。共10個診斷輸入（最終輪廓、原AI輪廓、擬合平面、未平滑、固定HSV邊界）。同輸入與 HEAD d7e3617 估算器10/10結果完全一致；目前估算器差異僅新增輸入檢查。這不代表已重播8月原始實機版本。

| 輸入 | 正面 cm² | 約43° cm² |
|---|---:|---:|
| 手機保存值 | 10.2273 | 11.2231 |
| 雲端PNG＋最終輪廓重播 | 10.2516 | 11.1994 |
| 雲端PNG＋原AI主輪廓 | 10.2516 | 11.2482 |
| 雲端PNG＋固定HSV邊界 | 10.4064 | 10.9548 |

- PNG重播與手機值分別相差+0.237%／−0.211%；僅這兩筆，不是PNG量化的一般誤差上限。未取得手機原始Float32作逐樣本比較。
- 斜拍改用舊工具未改動的HSV門檻仍比10.08參照高8.68%；將深度擬合為平面後仍為11.2717。這排除了「只因進修正頁」或「只因局部深度起伏」就足以說明偏差的結論，但尚未定位尺度／校正／配準或物理参照的根因。
- 全圖最大紅色元件在正面照選到無關背景，已拒絕；兩張均限制在既有靶輪廓外擴64px ROI，再用同一套門檻。HSV輪廓屬診斷參照，不是獨立認證真值，未依10.08調參。
- iPhone 已重新確認 connected；lockState 回 passcodeRequired=true，整合實機測試等待使用者解鎖。未因本輪分析改動手機紀錄、面積公式、雲端或上架狀態。

可重現證據：repo外 `woundai-competitive-review-20261003/wound4-geometry-replay/summary.json`、`reproduce.py`、10組輸入及來源快照。這是排錯證據，不是新增的實拍精度通過結果。

## 2026-10-04：傷口4實體面積靶驗證與「其他」部位

Jack 提供實體印刷面積靶，參照面積 10.08 cm²；同框另有立體物品。兩張雲端 JPEG 目視符合此場景，非前輪的螢幕翻拍。尚未獨立量測列印比例／面積靶不確定度，以下誤差以 Jack 提供值為參照。

| 保存時間（台北） | App估計傾角 | 表面積 cm² | 相對 10.08 誤差 | 投影面積 cm² | 深度ROI中位數 |
|---|---:|---:|---:|---:|---:|
| 14:37:16 | 5.81° | 10.2273 | +0.1473 cm² / +1.46% | 10.0840 | 314 mm |
| 14:37:57 | 42.87° | 11.2231 | +1.1431 cm² / +11.34% | 8.3867 | 310 mm |

- 每筆 4 檔、共 8/8 檔案可讀；兩筆 RGB 1536×2048、depth/validity 576×768，ID／尺寸與手機相符。兩筆方向比例檢查通過；不等於像素級配準或精確度全數通過。
- **採用 Jack 更正：斜拍有進修正頁但沒有修改遮罩。** `source=manual` 是確認路徑的標記，不能解讀成人為改動。原雲端主輪廓與最後輪廓以獨立 OpenCV 整數柵格比較，IoU 98.9879%、最後像素面積少 0.5255%；輪廓重新描邊會改變點數，不應據此宣稱使用者畫錯，也不能解釋 +11.34% 的偏高。此比較不是原生 Swift 次像素量測計算。
- 正面與斜拍結果相差約 9.74%；一張一角度，不能形成可承諾的精確度區間。不可用這兩筆硬調一個全域比例係數，也不把其中正面 +1.46% 外推到所有拍攝情境。
- 保存遮罩對應紅色平面圖形，**不是旁邊立體物品的完整外形**。容積／最深值仍是該圈選區的參考估算；物品没有已知高度／完整遮罩，尚未驗證其三維尺寸或建立完整模型。
- 斜拍那筆修訂 POST 日誌仍為 404、pending revision 1；正面 AI 紀錄沒有待傳修訂。兩筆 metadata `measured=null`；已傳照片／深度不可混稱最終量測已同步。

### 「其他」位置選項

Lite 詳細部位清單最後新增「其他」，保存固定代碼 `other`，允許左／中線或不分側／右，仍要求明確選擇側別。不增加自由文字欄，避免把姓名等資訊寫進研究位置欄。候選後台的部位名單同步接受 other，仍拒絕任意文字；不改寫傷口4目前的原位置。

新增實際 store 儲存／重載／revision JSON 測試，驗證三種側別的 other 代碼持續一致；後台測試驗證接受固定代碼、拒絕「other patient name」且不新增資料。首次 iPhone XCTest 63/63。後續協作回執模組同時進入工作樹，初次一般 Release 因新檔未列入專案而失敗（未安裝成功）；重新 xcodegen 後一般 Release 成功。整合實機測試因手機鎖定無法啟動；改用模擬器時未簽章執行有 6 項加密儲存測試失敗，正常本機簽章重跑 **68/68、0 skip** 通過。候選後台 revision 16/16、storage receipt 9/9，共 25/25 通過。整合測試期間的 Swift 來源雜湊未變。以上分開列示，不將模擬器通過當作實機整合測試通過。

一般 Release 33 已編譯並通過簽章驗證，但最終安裝前 iPhone 連線中斷（CoreDevice peer no longer reachable），一般 Release 更新未完成；需重接 USB 並解鎖後續作。證據：repo 外 `woundlite-other33-validation-20261004.json`。

### 後續精度驗證

2026-10-04 依 Jack 提醒重新查得歷史實拍證據，修正先前過於概括的「未做實拍驗證」說法：未完成的是目前版本／目前顯示指標的回歸驗收，不是專案從未實測。`EVIDENCE_LEDGER.md` 2026-07-20 記載 ArUco v2 比例法 n=15、0/30/60°，平均絕對誤差 1.9%、最大 4.3%；`phantom_validation_2026-08.md` 記載同台 iPhone 16 Pro Max 的 LiDAR 實拍，斜拍 38–55° 所列「投影÷cos(傾角)」誤差在 ±3% 內。後者不是現行三角化表面積的分角度精度結果，不能混用。歷史表中 16.61/16.02 的比值實為約 1.037，與表列 1.00 不符，需原始資料核對，保留原紀錄而不猜改。

不要求重新完成整套舊驗證。先追溯可取得的原始 RGB、深度、內參、輪廓及版本，固定同一輸入比較舊／新計算；優先分析傷口4已有的正面與約43°兩筆，分離輪廓、深度、校正與顯示指標的影響。使用者已確認斜拍未改遮罩，不以 manual 標記歸因人工描邊。

若舊資料足以重播，只補現行相機方向／校正路徑無法由重播證明的實拍；若不足或 +11.34% 尚未解釋，再沿用同一靶及已實量比例尺，先做正面／約43°各3次獨立取景的定位回歸，而非立即重做全角度矩陣。這6次是局部排錯方案，不足以建立對外精度範圍；直／橫拍方向問題另針對性補驗。只有結果顯示方向／距離依賴或需要擴大精度聲明時，才擴充 `lite_physical_rgbd_validation.md` 的24筆矩陣。若驗證立體物品，另需高度／尺寸參照與對應遮罩。以上均屬工程驗證，未產生新的精度通過結果。

證據：repo 外 `woundai-competitive-review-20261003/woundlite-wound4-audit-20261004.json`。本輪雲端僅唯讀，後台候選程式沒有部署。


## 2026-10-04：後台儲存回執與 WoundAI3D 契約續核

本機候選新增逐資產 storage_receipt；完整 PNG／CRC／像素解碼與深度遮罩尺寸檢查通過才保存。PNG 驗證前不寫深度或遮罩；物件儲存失敗不回成功回執，但仍可能留下未確認的部分檔案，不宣稱跨物件原子性。

驗證：storage_receipt 9/9、既有 segment 62/62、revision 15/15、quota 12/12；使用前輪唯讀下載之 6 組真實測試深度／遮罩，全部通過完整解碼。測試均隔離本機，不寫雲端。CI workflow 已加入新測試，但尚未有遠端 run；候選未提交／部署，App 仍未解析新回執。

已對照独立 WoundAI3D 的 Frame／VolumeMeasurer／TrainingDataFormat，接軌規劃寫入 lite_research_pipeline.md：有效值遮罩不冒充原生信心、缺姿態不補 identity、received_at 不當曝光時間、內參 reference 明示深度空間、人工自報部位不當臨床真值。這些是具體接軌規格與缺口，尚非 importer 或訓練流水線完成。

repo 外證據：woundai-competitive-review-20261003/lite-storage-receipt-validation-20261004.json。目標仍進行中；不得將這輪測試當成完整 WoundAI3D 建模／公開上架驗收。


## 2026-10-04：傷口三兩張範例的實際核對

唯讀取得 iPhone 當前 1.0(32) 的紀錄索引，僅核對「傷口 3／右側／足背」兩筆及對應雲端 8 個物件；沒有改寫、刪除、重送或重新分類雲端資料。

| 保存時間（台北） | 手機面積 cm² | cloud image_id | RGB | 深度 PNG | 結論 |
|---|---:|---|---|---|---|
| 14:13:07 | 1.4420 | ebc729cba274ce8d | 1536×2048 | 768×576 | 修正前橫直不符，不能列為已配準樣本 |
| 14:20:04 | 5.0152 | 23592196e2f830dc | 1536×2048 | 576×768 | 新資料方向尺寸一致，仍非完整精度驗收 |

- 8/8 JPG、JSON、16-bit 深度 PNG 與 8-bit 有效值 PNG 可讀；2/2 雲端 JPEG 的 SHA-1 ID、尺寸與手機 binding 相符。兩筆同屬一 wound_id，側別／部位完整，pending payload 與手機面積、歸组、位置相符，輪廓座標在影像內。
- 兩筆 metadata `depth=stored`，內參在各自深度網格內合理；conf 圖均為 255，是有效值遮罩，非原生置信度。metadata `measured=null`，最後修訂沒有伺服器回執。
- Cloud Run 對應保存時間的修訂 POST 皆為 404。針對兩筆實際 payload 的隔離 LocalStore 重播通過（200／重試不增生、409 衝突、新 revision 2 不被舊重試覆蓋），不代表線上已更新。
- 目視確認兩張都是拍摄電腦顯示的不同傷口範例，且 RGB 都是直式；不是同一物件的直／橫拍對照。不能用 1.44→5.02 判斷傷口進展、重複性或實拍精度，也不能用螢幕深度當原傷口 3D 真值。
- 下一項尚待完成：以同一個已知尺寸、不對稱的實物（L 形紙片＋小階梯皆可），固定距離直／橫拍，手動圈選同一範圍。此後才做方向、配準與幾何誤差驗收。原有兩筆保留作傳輸管線測試，不猜旋轉覆寫。

證據：repo 外 `woundai-competitive-review-20261003/woundlite-wound3-audit-20261004.json`、`woundlite-wound3-replay-20261004.json`。本輪未改 App，因此沒有重複執行上一輪 62 項 XCTest，也沒有新增建置或部署。


## 2026-10-04：Jack 的研究方案決策與 Lite 32 方向修正

### 產品決策（尚非商店／倫理審查批准）

Jack 明確指定：現階段免費雲端 AI 辨識採研究參與方案，須明示同意影像／深度等資料留存供模型優化與後續獨立 WoundAI3D 研究；不是預設勾選，也不將研究留存包裝成僅一次性傳输。保留離線手動圈選、本機量測與紀錄路徑。暫不實作雲端辨識與研究留存的分離方案；成熟商業版再重新評估。每日額度用於成本與濫用控制，不能當作研究同意、權限或完整性驗證的替代品。

此決策取代本文件早先將「立即拆分同意」當成已採用產品方案的建議，但不代表上架可直接放行。Apple 5.1.1(ii) 明確限制付費功能依賴資料授權，(iii)/(iv) 仍要求資料最小化及不強迫非必要資料存取；免費不自動排除這些規則。5.1.3(iii)/(iv) 涉及健康人體研究的完整告知與獨立倫理審查。需將研究目的、期間、利益／風險、第三方、退出及保留政策寫實；不能以去識別、個人參考或保留離線功能直接宣称豁免。若開始銷售訂閱／次數，須先另設不附加研究留存義務的付費契約與資料流程。來源：[Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)。目前未增加付費功能、未發布研究同意新版本。

### 工程實作

- 共用 CameraCaptureView 改用 DepthCapture.fromPhoto，將 UIImage 的方向以名稱映射至 EXIF（兩種 enum 的數值不同），再呼叫 AVDepthData.applyingExifOrientation。Apple API 同時轉換深度像素與校正參數，避免只轉像素卻保留舊內參。
- 不根據長寬猜方向。旋轉資訊來自當次照片；新的本機深度 sidecar 保留 sourceExifOrientation。既有 sidecar 可讀但沒有此欄位，不能宣稱已對齊。
- 估算器檢查照片／深度長寬比及深度樣本長度；比例不符的舊紀錄可保留檢視，修正圈選重算會說明原因且不覆寫。這個比例檢查只能抓明顯錯配，不能證明 180 度、鏡像、裁切、鏡頭畸變或拍攝同步均正確。
- 本輪未宣稱完成所有幾何配準。實機八方向合成測試與真實 LiDAR 拍摄驗收不同；仍要用不對稱的平面／階梯目標做直橫拍與已知尺寸對照。既有螢幕翻拍紀錄不自動回填旋轉或作為原傷口真值。
- 新欄位先保存在本機；線上 legacy segment metadata 尚未保存這份方向來源。完整雲端研究契約仍需逐資產回執、方向／尺度／時間／版本與測試資料標記，不能只靠本機修正聲稱完整。

依據：[Apple applyingExifOrientation](https://developer.apple.com/documentation/avfoundation/avdepthdata/applyingexiforientation(_:))。

### 階段驗證

- Lite Release XCTest：iPhone 16 Pro Max / iOS 27.0.1 **62/62**。新增三項涵蓋八種 EXIF 的獨立非正方形像素排列、RGB 正規化後長寬一致性、舊方向不符／截斷資料拒絕估算。既有 codec 測試另驗證方向來源往返。
- 使用實際手機三筆 pending payload 與對應檔案，在隔離 temporary LocalStore／Flask endpoint 重播（無外連、無雲端寫入）：三筆首次與重試均 200；同版本不同內容 409；新版寫入後重播舊版，最新仍 revision 2；最終面積／傷口 ID／側別部位保留。這不代表 GCS 跨實例並行或正式部署已驗收。
- Lite 32 一般 Release 編譯與簽章檢查成功，已安裝並啟動；更新前 5 筆內容全部保留，期間使用者新增 1 筆，故索引位元組有變化但無既有紀錄被覆寫。WoundAI Release 共用程式編譯成功（未簽章、未安裝、未上傳）。
- 14:19:34 新拍測試上傳唯讀核對：RGB 1536×2048、深度 576×768，內參主點約 (287.67, 383.75)，方向尺寸已一致；這證明新拍流程不再出現上一輪的明顯橫直錯配，尚不是完整配準／精確度驗收。
- repo 外證據：`woundai-competitive-review-20261003/woundlite-alignment32-validation-20261004.json`、`woundlite-revision-replay32-20261004.json`、`woundlite-build32-live-dimensions-20261004.json`。
- 雲端仍是上一輪查得的舊服務，修訂端點 404 尚未由本輪改變。舊紀錄沒有自動補傳成功；不把離線測試當成線上驗證。

### 下一階段的具體驗收順序

1. 新拍非對稱測試物件（不是電腦上的傷口圖）直、橫拍，驗證新 RGB／深度方向與內參；保持真正實拍精度尚未驗證的聲明。
2. 固定研究上傳 schema：原始／衍生格式、方向、時間、深度有效性、相機參數、演算法版、同意版、逐資產 hash 與明確回執，加入 test_only 排除訓練。
3. 保護上傳／更新／撤回權限並修復既有撤回及深度回執缺口，再建立可覆核後台部署候選。部署後才核對手機三筆補傳、latest revision、重試去重與跨實例持久性。
4. 研究參與內容與倫理／商店審查條件確認；再完成公開發行驗收，不能因技術測試通過而跳過。


## 2026-10-04 14:10：Lite 31 趨勢圖與手機／雲端實際資料核對

本輪讀取 Jack 已授權的 iPhone WoundLite 紀錄索引，以及其中 4 個影像 ID 對應的 GCS 檔案；沒有修改雲端資料、密碼、服務或部署。螢幕翻拍資料只可用於管線測試，不是原始傷口 3D 真值，也不能以圖表變化推論癒合。

### 實際核對結果

| 手機時間（台北 10/4） | 手機表面積 cm² | 雲端 image_id | JPG／JSON／深度／遮罩 | 最後修訂 |
|---|---:|---|---|---|
| 13:04:18 | 27.1651 | 1a7e7289f8e821d7 | 4 檔完整可解碼 | 原 AI 輪廓；沒有待傳修訂 |
| 13:08:35 | 38.2637 | ea620d64f05039a6 | 4 檔完整可解碼 | revision 1 尚未確認，HTTP 404 |
| 13:23:05 | 28.8751 | c38eddd51bb7f625 | 4 檔完整可解碼 | revision 1 尚未確認，HTTP 404 |
| 13:48:16 | 27.5303 | 551002d9965a63b3 | 4 檔完整可解碼 | revision 1 尚未確認，HTTP 404 |

16/16 指定物件存在並可讀；4/4 JPG 的 SHA-1 前 16 位與手機 binding image_id 相符，尺寸均為 1536×2048。雲端 metadata 記錄研究同意版本 2026-10-03.1、AI 原始輪廓、內參、png16_mm、scale=0.001、depth=stored。此為雲端檔案與手機識別碼比對；未解密手機 JPEG 做逐位元組比較。

**P1：最新修訂未到後台。** Cloud Run `woundai-backend-00039-xdk` 在三個儲存時間回覆 `POST /api/v1/lite/annotation/revision` 404，與手機的 pending revision 1／acknowledged revision 0 對應。不能把照片成功上傳當作修正遮罩／面積／分組已同步。三筆待傳 payload 的面積與 wound_id 均與手機相符；目前沒有伺服器回執。本輪未重送寫入。

**P1：RGB-D 方向／配準未驗證。** 4 張 RGB 為直式 1536×2048，深度為橫式 768×576；metadata 無旋轉／配準變換。`CameraCaptureView` 直接轉出 AVDepthData，Lite normalize 另外烘正 RGB 方向；估算器只對 x/y 分別縮放，沒有驗證兩圖方向一致。這不是單純「深度解析度較小」。在修正與非對稱目標實拍驗證前，不可將這批影像遮罩與深度當成已對齊的 3D 訓練樣本，也不能由本輪推導實拍面積精度。

**P2：資料契約尚不完整。** 4 筆雲端 `measured` 均為 null；segment metadata 無 wound_id、側別與部位。13:04 的 AI 原始紀錄沒有待傳修訂，單純部署 revision API 不會自動補齊它。四張 conf.png 全部為 255，代表有效值遮罩，非原生感測器置信度；深度是毫米量化 PNG，非本機 Float32。還缺拍攝方向／變換、濾波與精度屬性、真正採集時間與來源版本等研究 provenance。

本輪只核對指定 4 個 ID；沒有掃描全庫重複資料，沒有宣稱全域去重或撤回已驗證。稽核 manifest 將它們標為 test_only，但尚未將排除條件寫入雲端訓練管線。

### Lite 31 介面

- 紀錄頁初次進入預選最近有紀錄的傷口；保留全部紀錄、未分組及其他傷口選單。
- 選單與量測列表之間獨立顯示「傷口表面積變化趨勢（cm²）」；實際時間為 X 軸、面積為 Y 軸、從零開始。單筆也显示點；全部／未分組不畫混合趨勢。
- 無效時間、非有限值與負面積不繪圖；不同傷口不混線。相同時間依 ID 穩定排序，不推測先後。
- 精確度頁改為實拍尚無承諾範圍、顯示位數不等於精度、拍攝／比較限制、螢幕翻拍限制、容積與最深值限制；移除三角化與測試容許差等方法細節。保留既有估算功能。

### 驗證與後續順序

iPhone 16 Pro Max／iOS 27.0.1，Release XCTest **59/59**，0 skip／0 failure；新增 3 支趨勢資料測試（分組與時間順序、無效資料、單點／同時間）。一般 Release build 成功、簽章檢查成功、PrivacyInfo 存在且無 XCTest bundle。 已原地安裝並啟動 1.0(31)，更新前後 lite_records.json 的 SHA-256 與完整位元組相同，4 筆紀錄保留。這些不等同真人完成全部手勢／畫面驗收，也不等同後台已部署。

優先順序：先修正 RGB-D 方向／配準並用非對稱已知尺寸物件重測；接著補齊保存／修訂與逐資產回執契約，覆核後部署相符後台；以相同 image_id 補傳最新修訂並驗證不重複；明確排除螢幕翻拍／模擬資料進入正式 3D 訓練。公開研究功能仍受既有身分、同意／撤回及資料持久化閘門約束。

實際證據（repo 外保留）：`woundai-competitive-review-20261003/woundlite-upload-audit-20261004.json`、`woundlite-trend31-validation-20261004.json`、`woundlite-trend31-summary-20261004.json`。本輪沒有新 PR、遠端 CI 或 TestFlight 上傳；local changes 不能視為 PR #16 已驗證的 head。


## 2026-10-04 全面發布覆核：WoundLite／WoundAI 外测（架構評估，非發布批准）

### 結論與證據邊界

WoundLite 30 是可繼續實機驗收的候選；尚不具備公開研究蒐集型 App 的完整發布條件。
WoundAI 外測以隔離 demo、合成／模擬資料與受控測試者為範圍；不能等同正式臨床服務。
同 repo、兩個 bundle、共用幾何／相機／編輯器、獨立使用情境是合理方向；需要補強的是能力分級、資料契約、同意／撤回與驗收交付。
此次為程式／資料流／商店文件覆核，沒有新增量測精度實驗、沒有修改 App、沒有部署，也沒有查得當前 ASC Beta Review 狀態。
依據當日已有證據：Lite 30 iPhone XCTest 56/56、後台 8 組 62/15/12/30/34/32/8/29；另有 3 項可重現後台缺口，不能把綠燈測試當成全功能上架批准。

### 無 LiDAR 機型：建議能力分級

現碼 `LiteRootView` 把整個 TabView 放在 lidarOK 分支，無硬體時連紀錄、設定都看不到；Info.plist 僅要求 arm64。
Apple 已公開 capability 清單未列 LiDAR，ARKit／晶片效能門檻也不能當作 LiDAR 保證；不可使用虛構 capability 或假裝其他硬體需求來篩機型。
來源：[UIRequiredDeviceCapabilities](https://developer.apple.com/documentation/bundleresources/information-property-list/uirequireddevicecapabilities)。

| 裝置／狀態 | 建議可用功能 | 不應顯示／操作 | 尚需實作 |
|---|---|---|---|
| LiDAR 且深度／拍攝品質合格 | 拍照、輪廓、表面積估算、位置分組、紀錄、趨勢 | 不把數值稱為已驗證臨床精度 | 裝置實拍對照、資料完整性檢查 |
| 無 LiDAR | 照片日記、位置分組、前後照片對照、讀取／匯出／刪除既有資料、設定與政策 | 無尺度時不填 cm²、mL、mm，也不以零代替缺值；不要用像素面積變化冒充傷口變化 | 照片紀錄路徑、可空量測 schema、列表／趨勢兼容 |
| 有 LiDAR 但拒絕相機權限／深度暫缺／品質失敗 | 提示恢復方法，仍可讀舊紀錄與保存照片紀錄 | 不假裝硬體不支援，不用上一張深度補當前照片 | 能力狀態分開與明確錯誤畫面 |
| 相簿匯入照片 | 如後續開放，只能照片紀錄；已配對且可驗證 RGB-D 匯入另設格式 | 不能從普通 JPEG 猜出實際尺寸／深度 | 尚未實作，不列目前功能 |
| 後續增加標準尺／貼紙校正 | 經驗證可給 2D 投影面積 | 不能稱為曲面表面積；不同量測方法不可直接接同一條趨勢線 | 暫不納首版，避免增加流程與驗證成本 |

最小發布候選應至少讓無 LiDAR 裝置讀取紀錄、設定、政策與功能說明。若尚未完成照片日記，須據實描述僅量測支援機型；全頁阻擋的商店體驗不宜當作長期方案。
不任意承諾所有「Pro」裝置：以實際 `.builtInLiDARDepthCamera`、depth delivery 支援、權限與當次深度分開判定；iPad 也需測，不以 iPhone 顯示文案概括。
目前 TARGETED_DEVICE_FAMILY=1,2，最低 iOS17，並未只發 iPhone。是否先縮小預定支持範圍是產品決策，且不能把 iPhone-only 當完全阻止 iPad 相容執行。
Apple AVFoundation 支援範圍：[Capturing depth using the LiDAR camera](https://developer.apple.com/documentation/avfoundation/capturing-depth-using-the-lidar-camera)。

### 模組與情境對照

| 層／模組 | WoundLite 30 實作 | WoundAI 外測實作／界線 | 發布補件 |
|---|---|---|---|
| 啟動與能力 | 整體 LiDAR gate；無帳號／安裝代碼 | 病患個案、快速範例量測；登入後台角色 | Lite 改能力分級；醫療版 demo 身分與角色畫面一致 |
| 採集 | 相機 RGB＋AVDepthData；不開相簿 | 相機優先 LiDAR、退廣角；相簿／檔案入口 | 相機拒權限、暫停／重啟、支援／不支援裝置、方向一致性 |
| 分割 | 雲端 lite/segment 或手圈；本地 Core ML 只有保留槽，segment 回 nil | classify／校正／模型路由、組織比例 | 雲端失敗／空輪廓／多傷口確認；不能把健康端點模型檔存在當推論品質證明 |
| 幾何 | DepthAreaEstimator：RGB 輪廓映射深度、三角面積、平滑、品質檢查 | 校正貼紙尺度為主要量測依據，LiDAR 另作研究對照 | 真實尺度對照、曲面、濕亮、傾斜、小傷口與不同操作者；有尺度不等於已知誤差 |
| 編輯與保存 | 共享 WoundEditView，Lite boundaryOnly；分組／側別／位置／確認；表面積趨勢 | 組織／滲液／醫師修邊、SQLite 個案時間軸 | 修改必須帶版本；醫師確認綁定內容與操作者，不能把民眾修正當臨床 GT |
| 儲存 | LiteStore JSON 索引；照片／深度 AES-GCM；Float32 本機 sidecar | CaseRepository／加密媒體／DepthStore sidecar | 索引敏感資料保護、備份排除或加密策略、換機／金鑰遺失、容量不足、清理回執 |
| 上傳 | anon_id、PNG16 毫米深度、內參；修訂回執與持久待送為本機候選 | JWT／角色／照護與研究同意、image_id／receipt、補送佇列 | Lite 驗證安裝身分與原子配額；重試、失敗、跨實例與撤回閉環 |
| 使用者刪除 | 本機刪除與停止未來上傳分開；尚無完整一鍵研究刪除 UI | 醫療照護／訓練撤回與後台閘門 | 客戶端→來源→標註→資料集→訓練清單的可追蹤狀態；不得承諾可逆消除已訓練模型影響 |
| 環境 | Release 預設 legacy URL | 新裝醫療 Release 預設 demo；升級保留既有設定 | 明示示範／研究／正式环境；不能用 demo 帳號不存在的網址送審 |
| 商店 | icon、PrivacyInfo、版本頁；中文 UI 為主 | 已有 TestFlight 交付歷史；當前審查狀態未核實 | 真正雙語、支援機型、隱私／支援網址、App Privacy、年齡／內容分級、可用審查帳號與說明 |

### 關鍵重新判定

1. **LiDAR 精度不能從感測器名稱推得。** 深度來源、內參、方向／裁切、RGB-depth 配準、輪廓、平滑與拍攝物表面都影響最終量測。現有 Swift 合成測試是幾何驗證，不是實拍 ±X% 證據。拍攝螢幕上的傷口圖片只量到螢幕上顯示範圍，不是原病灶的面積／凹陷。Apple 也將 depth accuracy 與 calibration 連結：[AVDepthData.Accuracy](https://developer.apple.com/documentation/avfoundation/avdepthdata/accuracy)。
2. **不要把建議拍攝條件誤寫成全部強制閘門。** liteVerdict 目前對 <22cm、coverage<50%、部分面積比情況拒絕；>50cm 或傾斜主要是警告，25–40cm 是指引。需明訂「有效範圍／警告範圍／只存照片」並用實拍證据設定，不憑 UI 綠框判定尺度準確。
3. **無 LiDAR fallback 不能直接放寬現有 gate。** LiteRecord.surfaceCm2/projectedCm2 目前必填，ingest 無深度即不能量測；必須先引入 measurementMethod／nullable metrics 與品質狀態。避免相對雙鏡頭深度混入絕對面積：幾何 helper 本身未以 accuracy==absolute 把關，目前依賴採集路徑。
4. **推論使用與研究留存應拆成獨立同意。** 現在研究=true 才使用雲端，並不提供「僅辨識、完成後刪除」選項。建議分成必要推論傳输同意、可選研究捐贈；拒絕研究仍可保留個人工具價值。不可在日後付費方案中默認要求捐贈研究資料。這是建議架構調整，非本輪已完成實作。
5. **完整本機紀錄不等於雲端備份。** 本地照片／深度加密，但 LiteStore 把位置、量測、輪廓、雲端綁定與待送內容直接 JSON 寫檔；未見 isExcludedFromBackup 設定。ThisDeviceOnly 金鑰移機不可用，需要對資料恢復／匯出／刪 App 的限制給清楚說明。這不是宣稱可在鎖定手機上直接讀到資料；OS sandbox／Data Protection 仍存在。
6. **3D 採集資料需分品質級。** confPng 目前只有有效值=255／無效=0，是 validity mask，非感測器 confidence。Float32 本機深度上傳時量化為 PNG16 mm；沒有完整原生 RGB-D 時間／方向／畸變／變換 provenance。保留 raw／derived 分類、有效遮罩名稱與缺值原因；禁止把缺深度或未確認資料列為完整 3D 訓練樣本。
7. **資料退出仍是發布阻斷。** 已重現深度拒收回成功、撤回後重送復活、標註原文保留；匿名碼不是所有權證明。安全安裝身分（目前規劃 App Attest）需配服務端授權、一次性 challenge、counter 與持久化，不是單靠 SDK 即完成保護。
8. **公開研究與 demo 必须分開。** demo health=healthy 但 LocalStore、Lite API=false；研究正式儲存需持久化、隔離權限、資料與稽核保留政策分層。不可為研究影像便利任意放寬正式稽核閘門；也不可把可撤銷 retention 或舊 WORM 字串當不可逆鎖定證據。
9. **全球雙語／營運尚未完成。** 專案 knownRegions 為 Base/en，但未找到 .lproj／xcstrings，主要 UI 為中文；這不構成英文功能完成。首發先選能支援語言／營運／法規的地區。每天5次是本機候選政策，匿名碼可替換、跨實例競爭與全域帳本掃描仍需處理；不建議直接承諾無限雲端。購買／訂閱 StoreKit 尚未實作。

### 商店／研究核對依據

Apple 1.4.1 要求健康量測聲明有方法與精度依据；5.1.1 涉及政策入口、同意、資料最小化及服務提供主體；5.1.3 涉及健康人體研究同意與獨立倫理審查。具體研究歸類與發行地要求需由適當倫理／法規窗口確認，不能以「個人參考／去識別」字樣直接豁免。來源：[App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)。
現有 Lite Settings 無正式政策／聯絡連結，研究頁未完整列出期間、風險、參與者聯絡與完整退出流程；應一併補齊並與服務真實行為及 ASC 問卷一致。開發者帳號是個人或組織，本次未驗證，列為提交前確認。

### 驗收與交付順序（建議，不表示已通過）

| 階段 | 產出／完成條件 | 適用版本 |
|---|---|---|
| A 裝置與產品邊界 | capability 狀態分離、無 LiDAR 可讀資料＋照片紀錄、nullable metrics、方法分流、政策與支援入口 | Lite 公開版 |
| B 實拍量測 | 至少涵蓋支援 LiDAR iPhone、另一代裝置、無 LiDAR iPhone、宣告支援的 iPad；預登記平面／曲面 phantom、25/30/40cm、傾角、光線、操作者重複。報 bias、MAE、相對誤差、重複性、失敗率與適用區間；小面積同時報絕對誤差 | Lite 與醫療參考計算 |
| C 資料與研究 | 分離推論／研究同意；安全身分；完整逐資產回執；儲存／更新／撤回／重試／衍生資料的版本追蹤；來源研究權利／倫理審查明確 | Lite 雲端研究、醫療正式研究 |
| D 營運 | 限流與成本告警、用量交易、容量不足、備份恢復／密鑰、日誌遮蔽、事件回應、客服；沒有真實量測資料進 demo | 共用 |
| E 真正外測 | 鎖定原始碼 SHA、裝置／Release測試、Windows V2與遠端CI、demo最低權限帳號、精確URL、審查可重現資料；查核ASC實際build狀態後才對外 | WoundAI TestFlight 優先 |
| F 商店候選 | Lite自己的ASC／Archive／TestFlight、政策／問卷／截圖／雙語／機型一致，封版驗收後送審 | WoundLite App Store |

保留既有容積／最深值「估算參考」显示的使用者決策；本輪沒有關閉或移除。它們仍需方法證據與適用限制，不以免責文字替代驗證。
若首發研究條件未成熟，可規劃獨立的個人紀錄首版並延後研究功能，但必須明確列為產品範圍決策，不能一面稱研究未啟用一面沿用 legacy 上傳。

## 2026-10-04：Lite 30 縮放預覽與後台重點驗證

- Jack 回報 Lite 29 前台操作未見明顯問題，屬使用者操作回饋；本輪依要求新增結果／紀錄詳情的雙指縮放與平移（1–6 倍）。照片與青色輪廓共用同一縮放內容，單指保留頁面捲動；「重設檢視」回復全圖，更換照片／視窗尺寸會重設，預覽不改遮罩。
- 按鈕及提示統一「修正圈選」（詳情為「修正圈選・重新量測」）。伺服器 stored=true 未提供逐資產收據，文案改為影像已上傳、深度完整保存尚未確認，不再宣稱已完成自動去識別與完整 RGB-D 保存。
- iPhone 16 Pro Max Release XCTest **56／56，0 失敗、0 跳過**；新增 4 項驗照片／邊界共用變換、重繪不重設縮放、重設／新圖／尺寸變更、雙指 pan 與輪廓更新。一般 Release 建置／簽章通過；手機更新安裝讀回 **1.0（30）**，啟動成功，未解除安裝。實際手勢舒適度仍待 Jack 驗收。
- 後台 8 組：Lite segment 62、revision 15、quota 12、role 30、depth chain 34、depth endpoint 32、endpoint guards 8、P0-4 staging 29 均通過。檔案隔離子行程、LocalStore／合成影像；endpoint guards 為 pytest，最初直接 python 沒執行測試的結果已作廢，以 pytest 實跑 8／8 為準。醫療 depth endpoint 的強驗證不代表 Lite 的 PNG 接收具備同等檢查。
- 額外端點串接通過：合成 RGB／深度／confidence／metadata 4 資產保存 → 同一 revision 重送 2 次只留 1 標註 → 撤回移除 4 資產 → revision 重試回 410。但重現 **P1：深度拒收仍 stored=true；P1：撤回後相同安裝／同意版本可再上傳；P2：標註以 tombstone 隱藏，原輪廓仍物理保留**。不能視為完整刪除／正式 3D 資料庫驗收。詳見研究管線本日驗證節。
- 線上僅唯讀：demo／legacy health 都 HTTP 200；demo Lite API=false，LocalStore，revision `woundai-backend-demo-demo-4005f392-pw2-10020537`。舊服務仍回舊 WORM 字串，這不是 retention 鎖定證據。GCP describe 成功，本次不需登入。未部署任何候選、未寫雲端資料、未發布 TestFlight。
- 證據：`woundai-competitive-review-20261003/woundlite-zoom30-validation-20261004.json` 與可重跑 `woundlite-backend-chain-probe-20261004.py`。

## 2026-10-04：Lite 29 傷口選擇與存檔確認（實機已安裝）

- 依 Jack 操作回饋，左側既有傷口下拉與右側「新增紀錄傷口＋」分離。每次新拍攝預設帶入最近一筆有效日期、已分組紀錄的傷口；取消拍攝不改原量測的選擇。
- 點新增才顯示空白側別／详细部位，按鈕藍底白字表示選取；側別排列左、中線／不分側、右。首次使用沒有舊傷口時提示按新增，不默默建立。既有完整位置沿用，舊紀錄位置不全則要求補齊。
- 點「存入紀錄」先顯示傷口名稱與位置，選「確認並存入」才保存影像、深度及紀錄，之後依既有同意與雲端配對流程處理同步。返回修改不寫檔。確認期間傷口／位置已變更或遭刪除時拒絕保存並提示重新確認。
- iPhone 16 Pro Max Release hosted XCTest **52／52，0 失敗、0 跳過**（新增 5 項預設／確認回歸）。一般 Release 建置與簽章通過，未含測試 bundle；更新安裝後讀回 **1.0（29）** 並啟動成功，未解除安裝 App。
- 驗證結果：`woundai-competitive-review-20261003/woundlite-save-confirmation29-validation-20261004.json`；完整 xcresult `/private/tmp/woundlite-save-confirmation29-20261004.xcresult`。實際畫面操作與視覺尚待 Jack 驗收；本次未部署後端、未發布 TestFlight。

## 2026-10-04：Lite 28 有線實機測試與更新安裝（已完成）

- J-iP16PM（iPhone 16 Pro Max，iOS 27.0.1／24A446）解鎖後完成 Release hosted XCTest：**47／47，0 失敗、0 跳過、0 預期失敗**，官方摘要 runtimeWarnings 為空。
- 核對 26 個 iOS／後端／測試檔案 SHA-256 與前階段驗證 manifest 一致；未為此次實機驗證修改程式。
- 以全新 DerivedData 建置一般 Release，簽章驗證通過、不含 XCTest bundle、PrivacyInfo.xcprivacy 存在。採更新安裝，未解除安裝 App；device API 讀回 **WoundLite 1.0（28）**，啟動成功，稍後仍查得該程序。未讀取使用者紀錄內容，不把更新安裝當成紀錄完整性人工驗收。
- 「設定」的版本（建置號）取自已安裝 bundle。這是 Mac 本機開發簽章候選，未上傳 TestFlight。
- 本節取代下方歷史「Lite 28 鎖定／裝置未連線」狀態。測試使用模擬網路傳輸；真正相機／LiDAR 對齊、畫面逐步操作、長時間記憶體壓力及線上同步仍待驗收。鏡像連線未因有線測試成功而視為修復。
- 可攜證據：工作目錄 `woundai-competitive-review-20261003/woundlite-sync-build28-validation-20261004.json`，以及 `woundlite-device28-summary-20261004.json`、安裝／啟動 JSON、測試與建置 log。

## 2026-10-04 Claude 協作覆核與鏡像連線更新

- GitHub 唯讀核對：PR #16 仍為 Draft，head `d7e36175020f041bef39b130f811862613902180`；最新 main 為 `4005f392a5e77159ee44a140b86ca80af8fe0cdc`。合併預覽 tree `51d3c1577b1411f67f8bc8cd5687b8e5c4f911fd` 無衝突，並未移動 PR 分支。
- Claude 原 patch／`claude/status-20261004.md` 尚未取得。本機獨立重現並修正 `verify_logic` 舊網址 regex 失敗與 `parity_check` 靜默跳過 URL 的問題；共用既有 backend profile guard，缺檔、零執行或失敗均拒絕。
- 方案 B 候選只登記 Android 22／醫療 iOS 25、原因與下一次 Android release 對齊條件。Lite 28 是獨立產品。版號變更、過期／重複／不完整登記、未知 target/config override 均拒絕。候選另宣告 Lite revision endpoint 的產品邊界。
- 本機與 main+PR 合併預覽：static logic 50 項 0 失敗，parity 未宣告差異 0，新增 regression 6／6，9 種後端設定變異全部拒絕。已把兩工具及 regression 加入 phase0-check CI；遠端 CI 尚未重跑，Windows PowerShell 5.1 V2 尚未執行。
- 開發裝置已再次顯示 J-iP16PM available／paired；鏡像僅確認歡迎畫面，尚未成功連線，程序在後續操作前退出，原因未確定。不要把 USB／開發配對成功當成鏡像成功。
- 未讀得當前已登入 ASC 頁面，因此 Build 24 審查狀態及審查備註是否已補上切換 demo URL，仍未核實。保留手機本機紀錄，不用刪 App 模擬新裝。Build 25 新裝／升級的預設 URL 行為依 AppSettings 與登入驗證文件核對。
- Apple 文件確認同版本同時只有一個 build 在審；後續 build 可能不需完整審查，不能保證。[Apple TestFlight 規則](https://developer.apple.com/help/app-store-connect/test-a-beta-version/invite-external-testers)。
- App Attest 為目前 Lite 公開匿名 API 的安全發布阻斷，不宣稱是所有 App 的 Apple 通用必選項。正式桶／IRB、Windows V2、母庫 harvest 仍各自追蹤。


## 2026-10-04：Lite 28 重新圈選同步與防重送（本機候選，未部署）

- 紀錄標題改為「傷口表面積變化趨勢（cm²）」。
- 舊版核對結果：雲端 `image_id` 只存在量測 VM，未寫入紀錄；詳情頁重新圈選只更新本機。舊 annotation 每次呼叫 append 一筆，沒有冪等回執。不能把 `source=cloud` 當成已同步的證明。
- 新紀錄保存原上傳服務、安裝代碼、影像 ID 與座標尺寸。人工重新圈選後先保存本機，再送輪廓、參考量測值、傷口分組／側別／部位；原照片與深度不重傳、不再跑辨識。未人工修正的 AI 輪廓不冒充人工標註。
- 送件前原子保存待送修訂序號與完整 JSON 位元組；同筆操作序列化，重試沿用原序號及雜湊。回執必須同時匹配影像、修訂與完整 SHA-256。送件期間的新修改不被晚到回執覆蓋；本機刪除後不被回執重建。
- 新端點 `/api/v1/lite/annotation/revision` 使用 `append_record_once`：LocalStore 鎖、GCS `if_generation_match=0`。同一影像／修訂的不同內容回 409；原樣重試回同一回執，不再扣一次 annotation 額度。查閱端按修訂序號選最新內容，不依 GCS 物件字典序或抵達次序。
- 舊紀錄沒有可信雲端配對時只更新本機並明示原因。後端未支援新端點時保留待送，不回退至舊 append 端點。可在詳情頁按「檢查並同步修改」重試；目前沒有背景常駐自動重試排程。
- 研究同意版本升為 `2026-10-03.1`，明示量測、傷口位置與修改同步；舊同意不自動授權新欄位。撤回同意阻止新的請求，不能取消已被伺服器接收的送件，也不等同刪除已上傳資料。

驗證：Release 模擬器 **47／47**；後端新同步 **15／15**（含併行重送、同序號衝突、落地後回應失敗、GCS 假傳輸條件建立）、既有 Lite **62 項**、quota **12／12**、endpoint guards **8／8**、P0-4 staging **29／29** 全通過。GCS 是離線假傳輸測試，沒有寫真實桶。iPhone 28 前一輪測試因鎖定未啟動；最終重試時 Xcode 已偵測不到 J-iP16PM，待重新連接、解鎖後驗收；上一階段 iPhone 27 的 **35／35** 已確認通過。

限制：本輪未部署／未上傳 TestFlight／未執行遠端 CI。完整 RGB-D 雲端保存、影像去識別、安全安裝驗證、衍生資料撤回仍屬發布阻斷。新端點繼承既有 `anon_id` 身分不足限制，不能宣稱防惡意冒用。重試去重保證以保存中的雲端 ledger 為前提；demo LocalStore 隨实例消失，不能當正式持久研究儲存。全域 labels 的 fresh read 成本須在大量公開流量前改為具索引的儲存／查詢；配額不是跨實例交易帳本。後端／engineering 檔案仍需符合 repo 的 Windows 所有權交付流程，未繞過 guard 提交。


## WoundLite 當前驗收目標（Jack 指定，2026-10-03）

**完成 WoundLite 全功能驗證及發布阻斷修正，使其具備 App Store 上架送審條件。**
此處是 Lite 的執行目標與進度來源，不能以醫療版 WoundAI 已上 TestFlight 作為達標證據。
目前尚未達標；不以程式碼行數或局部單元測試推估完成百分比。
現有面積、容積、最深值計算與參考顯示保留；完整 WoundAI3D 研究在另一 repo。

| 驗收面向 | 已有成果／證據 | 關閉此閘門所需驗證 |
|---|---|---|
| 離線採集與量測 | Lite 相機、LiDAR、圈選及共用幾何核心；Jack 回報離線無明顯問題 | 新候選版 iPhone 16 Pro Max 實測拍攝、取消、重拍、手動修邊、品質失敗、前背景切換；保存裝置／版本／結果 |
| 本機紀錄與 RGB-D | 傷口分組及原子寫入；v2 Float32 深度側檔、照片配對、舊版解碼、損壞拒收；見研究管線最新驗證節 | 實機拍摄→保存→重啟→讀回→重圈→刪除；不同方向／比例的 RGB-depth 對齊；儲存不足情境 |
| 精度與限制 | 合成幾何測試與 App 說明入口；保留容積／深度參考 | 預登記 phantom 64 次對照尚未執行；不得標實拍 ±3% 或醫療級 95% |
| 雲端輔助與成本 | Lite API 與同意分流；每日成功 5 次本機 patch，已有 quota 測試 | 實際候選服務端到端、逾時／失敗／重試、RGB 與深度落地收據；跨實例原子額度及重放防護 |
| 隱私與撤回 | 同意版本化、加密本機媒體、政策草稿；已有缺口分析 | 上傳前影像／深度一致去識別處理、安全安裝身分、授權撤回、衍生資料追蹤及刪除驗收；現有 anon_id 不能當刪除密碼 |
| 穩定性與相容性 | 新 Lite hosted XCTest 與 Release 建置；CI 加入 Lite 測試及零跳過檢查 | 真機 LiDAR 與列表記憶體／jetsam、支援／不支援機型、字體／無障礙與預定發行語言；遠端 CI 尚須在提交後執行 |
| 研究與商店聲明 | 現有示範政策經核准；正式 Lite 政策仍是草稿 | 依實際用途完成研究倫理／法規與營運確認；政策、同意、ASC 隱私問卷一致；不可沿用 demo 暫存說明宣称正式研究資料庫 |
| 發布交付 | iOS target、icon、PrivacyInfo 與簽章基礎存在 | Lite App Store Connect app／SKU／簽章核對，Archive／上傳，Lite TestFlight 實機驗收、截圖／雙語文案／審查說明完整後提交 |

執行順序：本機資料與重算完整性 → 真機離線全流程 → 隱私／撤回與雲端合成資料端到端 →
候選版穩定性、政策及 ASC 資料核對 → Lite TestFlight → 上架送審。
App Attest 是目前公開匿名 API 設計選用的防護機制，並非所有 App 的通用上架要求。
雲端、正式蒐集、購買／訂閱與公開聲明只在相應成果可核對時推進，不把本機 patch 當已部署。

### 階段成果：本機紀錄生命週期（2026-10-03）

- **Release Simulator XCTest 24/24，0 failed、0 skipped**。其中新增 10 項
  `LiteStoreLifecycleTests`；前階段 RGB-D 9 項與分組 5 項維持通過。
- 修正前先以 7 項測試驗證，5 項失敗（15 個 assertion）、2 項通過，確實重現：
  損壞檔被新增覆蓋、重複 ID、舊 store 覆寫新存檔、舊畫面刪錯附件、不存在的紀錄刪除其他媒體。
  基準 run 在輸出完整測試結果後，Xcode 結果收集未退出，已停止該程序；
  反例依據是保存的 XCTest log，不以該不完整 bundle 當成功結果。
- 現在只把「首次不存在的檔案」視為空清單。讀取／解碼失敗保留原檔及已知紀錄，
  暫停異動，畫面提供重新讀取。重複／空 ID 拒收，存檔前比對最後讀取的 index bytes。
  此比對驗證同程序 MainActor 的循序 store 情境，並非跨程序交易鎖。
- 刪除依最新 ID 查找附件；不存在者不碰檔案，索引寫入失敗不刪媒體，仍被其他紀錄引用的附件保留。
  檔案清理失敗會顯示未完成；跨程序被殺後的孤兒附件清理／持久化重試仍待補強，不宣稱全部刪除情境已閉環。
- 保存→建立新 store 讀回→修改輪廓／數值→再次讀回→刪除與保留其他傷口已通過程式層测试。
  新 store 不是實際 App process 重啟，尚不能替代真機拍攝、畫面操作或 jetsam 驗收。
- 刪除確認文字明示：只刪此裝置的資料，不會撤回已上傳研究資料。
- 結果：`/private/tmp/woundlite-lifecycle-fixed-20261003.xcresult`；可攜 log／summary／
  source hash manifest 於同層工作目錄 `woundai-competitive-review-20261003/`。
  仍為本機未提交候選；未部署、未上傳 TestFlight，遠端 CI 未跑此版本。

### 真機程式層驗收（2026-10-03，已完成）

- Jack 解鎖後，等待中的測試恢復並成功退出：**WoundLite Release XCTest 24/24，
  0 failed、0 skipped、0 expected failures**，結果摘要 runtimeWarnings 為空。
- 實機：J-iP16PM，iPhone 16 Pro Max，iOS 27.0.1（24A446），arm64。
  開發簽章測試 host 為 `com.woundai.lite`，版本 **1.0（25）**，最低 iOS 17.0；
  成品含 `PrivacyInfo.xcprivacy`。這是本機安裝的測試候選，不是新的 TestFlight 發布。
- 24 項涵蓋 RGB-D／加密 store／幾何往返 9 項、傷口分組 5 項、紀錄生命週期 10 項。
  本輪未重跑同一組測試；讀取解鎖後完成的官方 xcresult，且 15 個異動 iOS 檔案
  SHA-256 均與前階段驗證 manifest 相符。
- 真機結果 bundle：`/private/tmp/woundlite-device-stage2-20261003.xcresult`。
  log、官方 summary、測試清單及 manifest 保存於
  `woundai-competitive-review-20261003/`，檔名前綴 `woundlite-device-stage2`。
- **尚未驗收**：真正拍攝與 LiDAR 深度對齊、完整畫面點按、實際關閉／重開 App、
  相機／列表混合壓力與 jetsam、雲端及研究撤回。單元測試的新 store 讀回不是 process 重啟。
  已請 Jack 用紙上輪廓或模擬物件，在關閉研究上傳時完成拍攝→圈選→保存→
  關閉重開→詳情重新圈選保存，待回報後再更新該 UI 閘門。

### 圈選介面修正與研究上傳核對（2026-10-03，Lite build 26）

- Jack 回報手動量測與圈選可完成；「看原圖」及圖示不一致仍有問題。
  這是部分實機操作回饋，不代表保存／重啟／刪除或相機對齊已全數驗收。
- 原圖由按住手勢改成點按 Button；預覽時隱藏整個 overlay（包含青色邊界）及游標，
  暫停塗抹、復原／重做；可縮放平移。點回或選編輯工具即恢復，顯示切換不改遮罩。
- 圈選工具與縮放列統一為 SF Symbols＋文字、固定 48pt 高、相同圓角與選取樣式；
  ROI 改「圈選區」，復原／重做不再混用 Unicode／emoji。
- **iPhone 16 Pro Max Release XCTest 27/27，0 failed、0 skipped**。
  新增 3 項覆蓋完整 overlay 隱藏與像素復原、原圖觸控不改遮罩／undo、回編輯後畫筆及 undo/redo。
  同時驗證 boundaryOnly 與醫療模式覆蓋圖；共用 UI 的醫療版 Release Simulator build 成功。
- Lite target 建置號改為 **26**，醫療版維持 25。正式設定的 Lite Release 實機 build 成功；
  本輪未發布 TestFlight。新工具列尚待 Jack 實際點按與視覺驗收。
- 分組仍是保存前手選既有／新增、自動命名，並非照片自動歸組；首頁捷徑、部位／左右側未實作。
- 後端資料核對與可重現反例見 `lite_research_pipeline.md` 晚間核對節：目前不足以稱為完整
  3D 訓練資料契約。只在記憶體執行接收 helper，沒有讀取真實桶、上傳影像或變更雲端部署。

### 新增／沿用傷口位置（2026-10-03，Lite build 27 候選）

- 依 Jack 指定：新增傷口必填側別與詳細部位，同列按鈕＋下拉；窄畫面可換行。
  既有完整傷口沿用位置；舊紀錄需補齊，補齊與新紀錄一次保存，失敗不部分更新。
  詳情重新指定分組亦須完整位置。相同部位仍可建立不同傷口，不自動合併。
- **Release Simulator XCTest 35/35，0 failed、0 skipped**（新增位置測試 8 項）。
  覆蓋必填／無效選項、既有位置沿用、同位置不同 UUID、舊組整批補齊、
  不存在 ID、失敗不部分寫入、詳情分組保留量測。既有 27 項維持通過。
- 真機編譯成功；截至本節紀錄，Xcode 因 J-iP16PM 鎖定而等待啟動測試，
  尚不宣稱 build 27 真機通過或已完成使用者畫面驗收。
- 保存後顯示本機與輪廓上傳狀態；位置只在本機，未冒稱新後端契約已部署。
  完整 RGB-D 與位置研究上傳、持久化重試、授權撤回仍待補齊。
- 證據：`/private/tmp/woundlite-location-simulator27-20261003.xcresult`；
  可攜摘要、log 與 manifest 保存至 `woundai-competitive-review-20261003/`。

## 目前執行基準（2026-10-02）

本節取代下方歷史清單的狀態判定。iOS PR #16 原基準為 `17e0b07`；
後端 PR #15 已合併至 main `4005f392` 並部署 demo。iOS 在
`codex/ios-release-assets-20261001` 的獨立副本實作，未變更原 Developer checkout。

| E 項目 | 本輪成果 | 尚待驗收 |
|---|---|---|
| Icon | 使用者核定兩版重製圖；醫療版十字／鏡頭／傷口、民眾版十字／鏡頭／機械手。1024 PNG 已掛各 target | 實機主畫面視覺確認 |
| PrivacyInfo ×2 | 原本已存在並進 Resources；本輪修正 Lite 的 linked、DeviceID、Health，醫療深度歸健康資料 | ASC 問卷同步與營運方確認 |
| Release | 兩版 Release 建置；檢查成品 DEBUG、圖示、ATS 與 manifest；demo URL 已驗證 | App 預設仍是正式 URL，須在設定頁明確切換 demo；iOS 雲端端到端尚待驗收 |
| App Attest | 決策：在雙端契約成立後才接入雲端請求，見下方契約 | 後端 challenge／驗證／持久化、客戶端實作、真機測試 |
| 隱私政策 | 示範測試版中英說明經 Jack 核准，已發布於既有 GitHub Pages，並儲存至 TestFlight | `site/privacy/` 的正式／民眾版草稿仍待營運與法規覆核；測試版說明不涵蓋正式臨床收案 |
| jetsam（歷史 task #40） | 共用序列載圖 actor、取消檢查、解碼池釋放、禁止原圖快取；修邊保留原座標 | LiDAR 實機壓力／記憶體峰值與 jetsam log，尚未結案 |

### 關鍵修正與證據界線

- 無帳號不代表匿名：`anon_id` 串聯同一安裝的資料，Lite 上傳標為 linked，無廣告追蹤。
- 醫療雲端量測本身會傳影像；②訓練同意是研究用途閘門，不能宣稱無②即不上雲。
- 政策移除概括 WORM、全資料 AES、原始 IP 絕不留存及已具一鍵撤回等不實保證。
- Lite 同意版本改為 `2026-10-01.1`；舊版選擇需重新閱讀，未取得新版同意時不走研究上傳。
- App Attest 是本專案公開匿名 API 的安全前提，不是所有 iOS App 一律必裝的 Apple 規定。
- demo01 應使用 nurse 最小流程角色，密碼由 Jack 保管；帳號可重建不等於資料持久化。
- TestFlight／Beta Review 不代表 IRB 核准，本階段只用 synthetic／phantom 資料。

### App Attest 雙端實作契約（待後端協作覆核，尚未實作）

1. 後端提供限時一次性 challenge（隨機 nonce、用途、過期時間），以及 register／assertion 驗證端點。challenge 與 key／counter 必須跨實例持久化，消費需原子化。
2. iOS 以 DCAppAttestService 建 key、對 challenge 雜湊做 attest；後端驗 Apple 憑證鏈、nonce、App ID prefix/bundle、環境及 credential ID。正式服務不得接受測試環境證明。
3. 每個受保護請求用版本化且兩端一致的編碼綁定 challenge、HTTP method、path、實際 request body 的 SHA-256。後端驗 assertion 與單調 counter，再執行操作，拒絕重放與重排。
4. 圈選上傳與資料撤回也要綁同一已登記 key／安裝身分；不能只保護 segment，或把 anon_id 當作任何人都能指定的刪除憑證。
5. 客戶端每個 key 的 assertion／發送序列化；環境不支援、key 遺失或驗證失敗時保留離線量測，不以未驗證匿名請求降級。裝置證明不取代使用者研究同意。
6. 驗收涵蓋 challenge 過期／重用、錯 bundle、錯環境、body/path 篡改、counter 重放、重新安裝、多實例及 key 撤銷；真機成功才宣稱完成。

Apple 依據：[App privacy details](https://developer.apple.com/app-store/app-privacy-details/)、
[Establishing app integrity](https://developer.apple.com/documentation/devicecheck/establishing-your-app-s-integrity)、
[Server validation](https://developer.apple.com/documentation/devicecheck/validating-apps-that-connect-to-your-server)。

### jetsam 實機驗收程序

以合成影像建立 100 筆本機紀錄；連續快速捲動與開關詳情 50 次、拍攝／圈選／取消 20 次，
再做前背景切換。以 Instruments Allocations／VM Tracker 記錄初始、峰值、靜置後 resident memory，
確認沒有持續累積或 JetsamEvent，並核對修邊前後像素座標與量測結果一致。
需歸檔裝置型號、iOS、build SHA、測試資料量、峰值及診斷 log；模擬器單元測試不替代此驗收。

### 2026-10-01 本機驗證結果

- 醫療版與民眾版 Release Simulator build 均成功；bundle 分別 com.woundai.app / com.woundai.lite。
- Release XCTest 51/51（4 支新增影像測試）；測試獨立開 ENABLE_TESTABILITY，ad-hoc 模擬器簽章。
- 首次測試因 Release 未開 @testable、未簽章 Keychain 與 Documents Finder metadata 失敗；改以 /private/tmp 的測試產物完成，正式設定未降低。
- 成品核對：兩份 manifest 與來源一致、AppIcon 名稱與 1024 無 alpha PNG 正確、無指定 DEBUG 診斷與 localhost 預設字串、ATS 禁止任意明文。
- 修正 CaseAndConsentViews 的兩個 actor 呼叫缺 await；本輪未再出現 Swift 6 isolation 警告。
- 未宣告 parity 落差 0；Mac owner_guard 通過。這些結果不等於雲端 IAM、簽名 Archive、實機 jetsam 或 Beta Review 已通過。
- build 仍是 22，這是本機驗證候選；下一次上傳需由正式發布流程分配更高 build number。

### nurse 送審流程補驗證（2026-10-01）

原 `25b5b7f` 的五項 CI 檢查已成功，遠端 XCTest 51/51。其後準備 nurse 審查步驟時，
另發現快速量測與紀錄複核將任意修邊直接設成 doctorVerified=true。
本輪補上伺服器回傳的 gt.verify／annotation.submit 權限判斷；nurse 仍可修邊與保存，
但不會標成醫師確認，重修舊輪廓也會清除原先確認狀態。訓練／深度送件按鈕依權限停用，
送件前重新登入查身分，伺服器仍是權限最終判定端。離線本機修邊不額外等待網路。
新增 5 支權限測試（完整 XCTest 56 項），新 head 需重新取得 CI，不能沿用舊 head 綠燈。

### 2026-10-02 demo 與實機記憶體進度

- PR #15 已合併為 `4005f392a5e77159ee44a140b86ca80af8fe0cdc`；正式服務未變更。
- demo revision：`woundai-backend-demo-demo-4005f392-pw2-10020537`，100% 流量。
  demo 密碼固定引用 Secret Manager 第 2 版，未讀取或記錄密碼內容。
- Jack 回報登入成功；另以瀏覽器唯讀驗證 `default:demo01` 顯示護理師、Dashboard 載入、0 筆資料，
  store 為 `local:/app/flywheel`。這不等於 iOS 登入或反覆冷啟動驗收完成。
- iOS `d8eb0a0` 的 CI 5/5 已通過；本輪新增 `ImageMemoryStressTests` 後，
  iPhone 16 Pro Max / iOS 27.0.1 的 Release XCTest **57/57** 通過。
- 新測試：100 張加密 4032×3024 合成影像、500 次縮圖、50 次完整解碼。
  physical footprint 初始 58.3 MiB、抽樣峰值 160.6 MiB、最後一輪 112.8 MiB；
  暖機後增量 7.7 MiB（五輪約 105.1、109.5、113.3、112.5、112.8 MiB）。
  這是元件層測試，並非持續採樣峰值；未涵蓋相機、LiDAR、SwiftUI 列表或前背景操作，jetsam 不結案。
- 新測試只刪除自己建立的 UUID 影像；不清空既有病例、照片或帳號。

### iPhone 示範環境驗收（未完成）

1. 開啟設定，在「後端連線」填入
   `https://woundai-backend-demo-z4kgfkob4a-de.a.run.app`；使用 `demo01`，
   密碼由 Jack 在裝置上直接輸入，按「儲存並測試連線」。記錄登入成功／nurse 身分。
   不可把一般 Release 的正式預設 URL 當成 demo；送審說明必須包含此設定步驟。
2. 僅使用新建虛構個案、合成影像或印刷模擬圖，完成量測 → nurse 修邊 → 本機保存 → 重開紀錄。
   不提交既有真實病例，不啟用研究送件。nurse 修邊不得標為醫師確認。
3. 依上方 jetsam 程序完成畫面／相機／LiDAR 壓力測試，歸檔 Instruments 與診斷結果。
4. 審查聯絡資料、測試者與測試版隱私說明已完成設定；仍需完成上述實機驗收再送外部審查。

### TestFlight Build 23 現況（2026-10-02 晚間）

- Archive／IPA 來自 `4cb25de`，版本 `1.0 (23)`；簽章、隱私 manifest 與匯出後 38 個 Mach-O sections 均已核對。Apple 處理完成，狀態為「準備提交」。
- iPhone Release 測試 57/57、該 head 的 CI 測試 57/57 通過；記憶體測試仍僅證明元件層，不取代前述 UI／相機／LiDAR 驗收。
- Build 23 已加入 WoundAI Internal QA。內部與外部群組各有一名經指定的測試者，狀態「已邀請」；尚未驗證接受邀請與實機安裝。
- 外部群組仍為零個建置版本。審查流程可前進至「提交以供審查」且按鈕可用，但尚未提交；表單通過不等於審查帳號登入驗收通過。
- 審查聯絡資料、登入需求、測試內容與隱私 URL 已保存。密碼由 Jack 直接在 Apple 表單處理，不讀取或寫入版控；私人電話不記錄於本文件。
- 經核准的[示範測試版隱私說明](https://jackh0001.github.io/WoundAI_Proj/woundai-beta.html)位於 `gh-pages` commit `605ec2b`；公開 HTML 與核准檔逐位元組相同。一般／必要雲端日誌的 30／400 天設定已唯讀核對，不代表示範病例持久化或 WORM。
- Mac 可列出已配對 iPhone，但實際 CoreDevice 連線重設，首次 iPhone 鏡像設定亦逾時。Jack 已同意鏡像操作測試，仍待手機端完成解鎖與連線。

### 接續順序

1. 完成 iPhone 連線、接受內部測試邀請並確認安裝 `1.0 (23)`。
2. demo 已完成部署、固定 URL、IAM 與瀏覽器 nurse 登入；接續 iPhone 示範環境端到端及 UI／相機／LiDAR 記憶體驗收。
3. 正式版 ASC 隱私問卷與民眾版政策另依實際開放功能處理，不把本次測試版政策發布當成完成。
4. 實機驗收後提交醫療版 Beta Review；審查核准後核對外部群組版本與測試者可安裝狀態。
5. 民眾版對外雲端測試等待 App Attest、撤回流程與研究／法規前提完成。

---

以下為歷史規劃與紀錄，不代表上述項目已通過現行版本驗收。


兩條路線分開走，**不互相等待**：

| | 醫療版 WoundMeasurementApp | 民眾版 WoundLite |
|---|---|---|
| 目標 | TestFlight 外部測試（臨床收案） | App Store 公開上架 |
| 審查 | Beta App Review（較寬鬆、1–2 天） | App Review（嚴格） |
| 阻擋 | App Icon、示範帳號 | 全部 4 項（見下） |
| 可動工時間 | **本週** | App Attest 完成後 |

---

## A0. 進度（2026-08-21 更新）

| 項目 | 狀態 |
|---|---|
| App Icon ×2 | ✅ `tools/make_app_icons.py`（SSOT，可重生） |
| `PrivacyInfo.xcprivacy` ×2 | ✅ 已進兩個 target 的 Resources |
| 隱私權政策 URL | ✅ 已上線並驗證可公開存取 |
| TestFlight demo 帳號 | ✅ `demo01`（密碼由 Jack 保管，Claude 不經手） |
| Release 設定體檢 | ✅ 後端網址正確、DEBUG 診斷不外洩 |
| jetsam 記憶體修正 | ✅ 待實機回歸 |
| App Attest（僅民眾版） | ⬜ 未開始——**民眾版上架的硬阻擋** |
| 醫材法規分類諮詢 | ⬜ 未開始——**最大變數** |

**隱私權政策網址**（填進 App Store Connect）：

- 民眾版 WoundLite：`https://jackh0001.github.io/WoundAI_Proj/woundlite.html`
- 醫療版 WoundAI：`https://jackh0001.github.io/WoundAI_Proj/woundai.html`

站台原始碼在 `site/privacy/`（main 分支），發佈於 `gh-pages` 分支。
**改政策時改 `site/privacy/` 再重推 gh-pages**，不要直接編 gh-pages
——那會讓兩邊分岔，而 main 上的版本才是有版本控管的那份。

⚠ 政策內容已逐條對照實作核對（EXIF 剝除、AES-256-GCM、ThisDeviceOnly、
結案後 90 天清除、IP 加鹽雜湊），未寫程式做不到的事。
**但這是準確的技術描述，不是法律意見**——正式送審前請法務或熟悉個資法者過目。

## A. 硬阻擋（沒有這些連上傳都不行）

### A1. App Icon 完全沒有圖 🔴 兩版都擋

`Assets.xcassets/AppIcon.appiconset/` 只有 `Contents.json`，**沒有任何 PNG**。
Xcode Archive 會失敗或 App Store Connect 直接退。

需要：1024×1024 無圓角、無 alpha 的 PNG（Xcode 15+ 單張即可自動衍生）。
兩個 App **必須是不同圖示**——同圖示會讓使用者混淆，也可能被審查質疑
「兩個 App 是否重複」（Guideline 4.3 Spam）。建議：醫療版偏臨床（深藍＋
量測十字），民眾版偏親和（綠＋傷口輪廓）。

### A2. `PrivacyInfo.xcprivacy` 缺席 🔴 兩版都擋

2024-05 起 App Store 強制。**本專案一定要宣告的 Required Reason API**：

- `NSPrivacyAccessedAPICategoryUserDefaults` → 理由碼 `CA92.1`
  （AppSettings／LitePrefs 都用 UserDefaults）
- `NSPrivacyAccessedAPICategoryFileTimestamp` → `C617.1`（若有讀檔案時間）
- `NSPrivacyAccessedAPICategoryDiskSpace` → `E174.1`（若有查容量；
  醫療版 `LocalImageStore.totalBytes` 要確認是否觸發）

以及資料蒐集宣告（`NSPrivacyCollectedDataTypes`）：

| App | 蒐集項目 | 連結身分 | 追蹤 |
|---|---|---|---|
| WoundLite（同意研究時） | 照片/影片、其他診斷資料 | **否** | **否** |
| WoundLite（未同意） | 無蒐集 | — | — |
| 醫療版 | 健康與健身、照片、其他使用者內容 | 是（帳號） | 否 |

⚠ 民眾版的「否連結身分」是我們的設計優勢（anon_id 不連結個人），
要在 App Privacy 問卷據實勾選——它同時是行銷賣點。

### A3. 隱私權政策網址 🔴 兩版都擋

App Store Connect 必填。內容至少涵蓋：蒐集什麼、為何蒐集（研究）、
保存多久、如何撤回（民眾版 `DELETE lite/data/<anon_id>`、醫療版同意撤回）、
聯絡方式。需要一個可公開存取的網址（GitHub Pages 或後端 `/privacy` 靜態頁皆可）。

### A4. App Attest（僅民眾版）🔴

後端契約已白紙黑字寫著：`anon_id` 是客戶端自產字串，**擋得住誤觸、
擋不住任何有意的濫用**；正式對外開放流量前必須換成裝置證明。
匿名端點沒有這層 = 公開一個誰都能按的計費按鈕。

工作量：iOS `DCAppAttestService`（產 keyId → attest → 每次請求帶 assertion）
＋後端驗證 Apple 憑證鏈。iOS 端約 1 天，後端約 1–2 天。

---

## B. 最大風險（不是技術，是分類）🟠

**傷口面積量測是否構成醫療器材軟體（SaMD）？**

事實面：
- 台灣 TFDA 對「醫用軟體」有分類指引；**用於診斷、治療決策**的量測軟體
  通常落入醫材管理；**純紀錄、衛教、生活型態參考**通常不落入。
- Apple Guideline 1.4.1：醫療 App 若提供不準確的資料可能造成傷害，
  須有依據；宣稱診斷功能者，審查會要求法規許可證明。
- 我們目前的定位語句（App 內）：「健康參考工具，非醫療診斷。傷口惡化、
  發燒或大量滲液請就醫。」——這是**正確的框架**，但要一致貫穿到
  App Store 描述、截圖文案、隱私政策、官網。

必要動作：
1. **文案紅線**：全平台不得出現「診斷」「判讀」「醫療級」「取代就醫」
   「精準測量傷口以評估癒合」等字樣；改用「記錄」「追蹤」「參考」。
2. **法規諮詢**（建議在提交前完成）：把 App 定位、功能清單、誤差數據
   （phantom ±5%／斜拍校正 ±3%）交給熟悉 TFDA 醫材分類的顧問或
   法務確認是否需要查驗登記。我不是法規顧問，這一條必須由專業人士拍板。
3. 醫療版走 TestFlight 內／外測，**不上架**，可暫時迴避此問題
   （TestFlight 屬測試用途，但仍不得對外宣稱醫療效能）。

---

## C. 送審素材清單

### C1. 兩版共同

- [ ] App Icon 1024×1024（各一）
- [ ] `PrivacyInfo.xcprivacy`（各一，內容不同）
- [ ] 隱私權政策 URL、支援 URL
- [ ] 出口合規：`ITSAppUsesNonExemptEncryption=false` 已設。
      理由：只用 Apple 平台加密（CryptoKit AES-GCM 保護本機資料）
      與標準 HTTPS，屬豁免範圍。**把這句寫進送審備註**，被問就有答案。
- [ ] 截圖：6.9"（iPhone 16 Pro Max）＋ 6.5"，各 3–5 張。
      ⚠ 必須用 **Release build** 截圖——Debug 的 🔧 診斷行不能出現在商店頁。

### C2. 醫療版 TestFlight 專屬

- [ ] **示範帳號**（Beta App Review 需要能登入）：建一個 `demo01`
      角色 nurse、綁示範組織，只用合成／模擬資料。⚠ 密碼由你設定並填入
      App Store Connect，我不經手。
- [ ] 測試資訊：「本 App 為臨床研究用傷口量測工具，僅供受邀醫護人員測試」
- [ ] 外部測試組：臨床測試者 email 清單
- [ ] 90 天到期提醒：TestFlight build 90 天失效，收案期要排定重新上傳

### C3. 民眾版 App Store 專屬

- [ ] **審查備註必須寫**（否則極可能被誤判為「App 無法使用」）：
      ```
      This app requires a LiDAR-equipped iPhone (iPhone 12 Pro or later
      Pro/Pro Max). On other devices it shows a compatibility notice by design.
      Please test on: iPhone 15 Pro / 16 Pro / 17 Pro.
      Measurement works on any object — no wound required for testing.
      Tap 拍攝傷口 → outline any small object → area is computed from LiDAR depth.
      No account or login is required.
      ```
- [ ] App 描述含裝置需求（第一段就要講）
- [ ] 年齡分級問卷：醫療/治療資訊 → 通常 12+
- [ ] 類別：醫療（Medical）或健康與健身（Health & Fitness）。
      **建議 Health & Fitness**——Medical 類審查對法規證明的要求更高，
      而我們的定位就是健康參考工具。
- [ ] 若同意研究上傳：App Privacy 問卷據實填「照片/影片、其他診斷資料，
      不連結身分、不用於追蹤」

---

## D. 建議時程

| 週 | 醫療版 | 民眾版 |
|---|---|---|
| W1 | Icon＋隱私宣告＋demo01＋Archive → TestFlight 內部測試 | Icon＋隱私宣告＋隱私政策頁 |
| W2 | Beta App Review → 外部測試上線、臨床收案續行 | App Attest（iOS＋後端）＋法規諮詢送件 |
| W3 | 收案中；90 天到期排程 | Release 截圖＋描述＋App Privacy 問卷 |
| W4 | — | 提交審查（預留 1–2 輪退件往返） |

## E. 我這邊接下來可立即動工的

1. 產生兩份 `PrivacyInfo.xcprivacy` 並掛進 project.yml（半天）
2. Release build 設定體檢（`#if DEBUG` 診斷行確認不外洩、Release 後端網址正確）
3. App Attest 客戶端實作（等你決定要不要現在做）
4. 隱私權政策草稿（中英，依實際資料流撰寫，非樣板）
5. jetsam 記憶體問題（task #40）——**上架前必修**，閃退是最常見的退件原因

需要你決定或提供的：App Icon 設計、隱私政策上架位置、法規諮詢對象、
demo01 密碼（你設定，不告訴我）、TestFlight 測試者名單。
