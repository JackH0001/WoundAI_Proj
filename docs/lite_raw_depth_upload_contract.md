# Lite 原始深度接軌契約（本機已整合，未部署）

## 2026-10-04：原始 RGB-D 簽章上傳與撤回第十五階段（本機候選，未部署）

新拍攝的 DepthCapture 資料完整且來源方向已知時，LiteMeasure 使用同一次 quality 0.92 JPEG 編碼建立 lite.rgbd/1 封包，經原有 App Attest 簽章送到 segment；不再把此路徑的原始 Float32 降成毫米 PNG。封包移至共用 Core/LiteRawDepthPacket.swift，醫療 target 的 BackendClient 也能正常編譯。缺來源方向等必要資訊的舊資料仍走既有 PNG 路徑，不產生原始深度已驗證的宣稱。本機紀錄的 quality 0.85 JPEG 仍是另一份編碼，不將其 hash 假改為上傳影像 hash。

後端 raw multipart 僅在裝置驗證與持久 writer ticket 均存在、同意有效時接受；拒絕重複 parts、混用 PNG、未知欄位、錯誤 digest／長度。模型前完成格式與資產配對驗證；LRD1 原始包採不可覆寫的單物件保存並讀回。相同影像／原始包重送回相同資產回執，不同深度或 metadata 綁同影像 ID 回 409；寫入或讀回未確認回 503。App 核對回執的 owner、capture、三份 digest、整包 digest 與長度後，才隨 LiteCloudBinding 保存「原始深度已核對」證據。

DELETE 在持久撤回且所有 writer 排空後清除該安裝的 raw namespace，包括後續步驟失敗留下的原始包，再清理研究 ledger。讀回失敗、清除失敗或仍有 writer 時不回報完成。只證明 live objects／rows；歷史版本、備份、匯出與衍生模型不在這次保證內。

驗證：新增 signed raw HTTP **9/9**；含 raw contract／store、App Attest、撤回 fencing、ledger purge、回執、修訂、service profile、quota、reader 及隔離的 **13 套、171 項 unittest 全過**。Release Simulator：**WoundLite 134/134、醫療版 72/72，皆 0 skipped**。另以真 Swift multipart → 真 loopback HTTP → 合成簽章驗證 → 暫存 LocalStore → 真 Swift 回執核對，確認 JPEG／metadata／Float32 逐位元一致；原簽章重放 401，簽章撤回 200 並清空本安裝 raw namespace，listener 已停止。這不是 Apple 真機持有證明、真 GCS 或線上模型精度驗收。

第一輪新 HTTP 測試以整個 lite_raw/ 列表要求全空，但 LocalStore 會回傳留下的空安裝目錄；已改為按實際清除契約驗本安裝 namespace，原始失敗日誌另存，修正後 9/9。verify_logic 50/50、parity 未宣告差異 0、git diff --check 通過。

限制：現行 app 與雲端服務尚未更新；沒有新的 TestFlight 上傳。未取得原生 confidence、相機 pose、曝光時間，registration 仍 not_verified、training_admission 仍 not_evaluated，不稱為完整多視角建模資料。為相容既有影像／修訂入口，LRD1 包之外仍另存 JPEG，增加儲存成本；相同包不重建，但既有 index 仍會附加請求行，並非全部請求冪等。還需處理來源 test_only 分類、AI 未手改時的最終量測 metadata、crashed writer 回復、真機記憶體與正式部署驗收。

證據：repo 外 woundai-competitive-review-20261003/lite-raw-business-20261004/validation.json、13 套測試日誌、兩份 xcresult、native-roundtrip.json 及來源快照。

## 以下為分階段歷史紀錄

下文「尚未接入／未傳送」描述各階段當時狀態；目前候選實作以上方第十五階段為準。

## 現碼核對與實測

2026-10-04：LiteMeasure.liteCloudSegment 把 work UIImage 編為 quality 0.92 JPEG，上傳 segment；saveMeasurement 重新編為 quality 0.85 JPEG 保存本機。LiteDepthCodec v2 的 rgbSHA256 是本機 JPEG 的完整位元組雜湊。兩者即使尺寸與拍攝來源一致，也不保證位元組一致。

使用目前完整 LiteDepthCodec.swift（未改算法）在 macOS 編譯，僅以最小 DepthCapture DTO 提供資料：64 個 Float32 樣本（含 negative zero、NaN payload、Infinity）在正確 JPEG 下逐位元保留；同尺寸但不同壓縮 JPEG 回 imageMismatch。合成 JPEG 由 Pillow 產生；不是 UIImage 編碼品質的跨平台一致性測試，也不是實際手機照片驗收。

## 必要決策與實作方向

1. 新拍攝在背景工作中產生一次研究上傳 JPEG bytes。用這一份 bytes 同時建立 raw depth envelope 的 SHA-256 與 HTTP image part，不能重新編碼兩次後只比較尺寸。
2. 原始 Float32 必須來自該 frame 的 DepthCapture，不能由毫米 PNG 反推；儲存原位元與有效性分類，保留正規化 EXIF、內參及其 reference dimensions、accuracy/filtered。此處「原始」指 App 取得且已記錄方向變換的 Float32，不表示未經 Apple 感測器處理。
3. 定義獨立 wire schema（例如 lite.rgbd/1），不可把本機側檔版本自動當雲端 API 版本。標明 float32_le、公尺、尺寸、樣本數、RGB digest、depth digest、內參參考空間及轉換沿革。保留 NaN bits 不等於 NaN 可參與幾何计算；消費端用 validity 排除。
4. 缺少 pose／曝光時間／原生 confidence 時用 explicit unavailable；不補 identity matrix、server received_at 或 255 信心值。普通分類用途、單幀表面研究與多視角建模的資格分開。
5. 後端以實際收到的 JPEG bytes 計算 SHA-256、完整解碼核對尺寸；嚴格拒絕未知 schema/units/endian、長度不符、hash 不符、不合理尺寸／參考空間。HTTP 與解碼前有大小上限，檢查成功才寫入。保存回執逐資產附 digest 及狀態；不以 stored=true 泛稱完整 RGB-D。
6. JPEG 與 raw depth 完成持久保存後才產生 committed manifest；失敗可重試而不重複計入資料。這需要審查跨物件一致性、半套資料清理與撤回 fencing，不是多放一個檔案就完成。
7. 同意、資料來源分類（test_only）、最後圈選版本、傷口分組與撤回世代要綁 manifest。研究用途與撤回狀態不由客戶端單方字串證明，仍需裝置持有證明與後端授權紀錄。
8. 舊照片不靠尺寸推定原始位元配對。若未保存原上傳 JPEG，既有側檔可留作本機重算；雲端補傳前必須另立能驗證的衍生關係和授權，不把本機 rgbSHA256 改成雲端 hash 來假造相同資產。

## 效能及驗收

原始 Float32 平面 576×768×4 = 1,769,472 bytes；JSON base64 約 2,359,296 bytes，另有 envelope/HTTP 開銷。這只是未壓縮格式估算，不是雲端帳單。優先使用二進位 multipart 避免多層 base64；量測峰值記憶體、低速網路及失敗重試流量，不能使現有 jetsam 風險回歸。

實作驗收至少涵蓋：Swift 真正 payload→Python strict decoder→持久層讀回逐位元比對、JPEG 錯配、payload 截斷／額外位元、NaN/Inf 保存但不作有效幾何、不同方向非方形測例、並行重送、半套寫入、撤回／重新同意、來源隔離及 payload 上限。獨立 WoundAI3D importer 最後才能依 manifest 宣告的資料級別讀取，不直接把 validity 當 ARKit confidence。

目前這份是可實作的契約草案，沒有新增公開端點，沒有宣稱 raw depth 已上傳，也不變更既有用戶同意與雲端資料。


## 離線可執行契約（2026-10-04）

`Backend/Flask/lite_raw_depth_contract.py` 實作 draft lite.rgbd/1 strict validator：metadata 上限 16 KiB、JPEG 16 MiB、Float32 最多 1024² 個樣本；JSON 重複／未知欄位拒絕，實際 JPEG 完整解碼及 SHA-256 配對、樣本長度、尺寸、單位、little endian、內參參考空間與來源方向皆檢查。raw bytes 保持原樣；有效遮罩依 Swift Float32 的 0.05 < m < 60 規則衍生，避免 Python double 下 0.05 的邊界分歧。

10/10 測試通過，含 NaN payload／negative zero 保存、錯 JPEG、截斷／額外樣本、損壞、未知語義、boolean 偽裝整數、重複 metadata key、尺寸與參考空間錯配。既有真 Swift codec 產生的合成側檔经明示離線轉換 metadata 後可驗證，64 個 Float32 位元保持一致。這不是新 Swift wire client 的測試，也不是原始深度已上傳。

此模組尚未掛公開 route，不寫儲存、不授權研究、不證明來源真實；訓練資格回 not_evaluated。先前固定 12 檔後端交付包不包含這個後續模組，不能沿用該包的 79/79 證據宣稱新模組已進入該包。後續需完成真正的 binary multipart 客戶端與後端原子保存／回執整合，再獨立封裝驗證。


## Swift wire 封裝（2026-10-04）

新增 `iOS/WoundLite/LiteRawDepthPacket.swift`，在提供的 DepthCapture + 實際 JPEG bytes 上建立 draft lite.rgbd/1 metadata、rawDepth binary 與相同 JPEG。檢查尺寸／內參／方向來源／JPEG解碼，拒絕無來源方向的舊資料，不猜造 pose/曝光時間/confidence；沒有加入自動傳送呼叫。

4 項新增 XCTest：exact bit pattern、缺方向／比例錯配、截斷／極端尺寸、無效影像／內參。Release Simulator 全套 **72/72、0 skipped** 通過。另將實際 Swift wire 封裝以合成 DTO 在 macOS 執行，生成的 metadata 無需轉換即可由 Python strict validator 讀取，64 個 Float32 樣本逐位元不變，NaN／negative zero／Infinity／0.05 邊界皆按規則分類。這比上一節本機側檔轉換更接近最終協定，但尚未完成 multipart 傳送、後端原子保存及回執，沒有宣稱 raw depth 已上雲。


## 原生 multipart 跨語言 HTTP（2026-10-04）

Swift `multipart(boundary:)` 將原 JPEG、metadata JSON、Float32 binary 分為三個 part，不做深度 base64；boundary 字元／長度與 payload 碰撞檢查。Python `parse_multipart` 拒绝缺少／未知／重複 parts，metadata 16 KiB、depth 4 MiB、JPEG 16 MiB 有界讀取後再 strict validate。正式 route 仍須先設定總 HTTP/body 限制，不能用這個 helper 取代認證、同意或撤回閘門。

真 Swift 所產的 multipart binary 送入臨時 loopback Werkzeug 接收器，HTTP 200 且 metadata 與 Float32 digest 不變；單 byte 篡改回 400。測試 listener 已 shutdown/join，無持久儲存寫入。Python 套件 14/14；Release Simulator 73/73、0 skipped。這不是部署到 Cloud Run 或 App 自動上傳；原子保存／回執及授權整合仍未完成。


## 單一物件持久保存草案（2026-10-04）

`lite_raw_depth_store.py` 將 strict validated metadata（canonical JSON）、JPEG、Float32 raw 組合為一個 LRD1 framed binary bundle，以既有 Store.put_blob_immutable 保存至 `lite_raw/<installation>/<capture>.rgbd`。LocalStore 使用完整寫入後 exclusive hard link；GCS 使用 generation=0。同 capture 相同內容重試給相同 digest receipt，不同內容 ImmutableConflict，不覆寫既有包。回執只在讀回整包相同且再次解包／驗證後回傳。Immutable 指不得覆寫，非 retention lock/WORM。

7/7 測試涵蓋單物件往返、12 個並行相同請求、同 ID 衝突、讀回失敗後重試恢復、寫入失敗、GCS 假傳輸 generation=0，以及截斷／尾碼／路徑越界。實際 Swift wire fixture 另以 temporary LocalStore 保存重載，JPEG 與 Float32 bytes 完全不變，回執原樣重送一致。未寫真 GCS。

此 helper 不可直接當公開 API：裝置身分、同意／撤回 fencing、原始包與既有影像／修訂索引映射、資料保留清理及 App 回執持久化尚未接線。既有 delete endpoint 不會清這個未上線 namespace；未完成整合前不得啟用研究收集。單物件保存解決三個資產半套落地，不等於解決跨請求撤回競態。


## 精確回執綁定修訂（2026-10-04，取代上節 canonical metadata 設計）

保存端現在保留驗證後的原 metadata bytes，不重新序列化。LRD1 bundle 使用大端 32-bit 三段長度 + 原 metadata/JPEG/Float32。回執新增 metadata_sha256；App `verifiesReceipt` 檢查 schema、status、installation/capture scope、三份 digest、bundle 長度及串流計算的完整 bundle digest，仍要求 registration=not_verified、training_admission=not_evaluated。以未驗證資料包的回執不產生研究或精度放行。

实际 Python save_capture 產生的回執由 Swift 接受，另 11 個錯 scope／內容／狀態反例拒絕；這些新檢查由 native macOS probe 執行。Storage 7/7、既有 Release Simulator 73/73 回歸通過。App 尚無自動 raw upload 呼叫、回執尚未接入持久重試佇列；公開授權入口與撤回仍待實作。


## 正式回歸納入（2026-10-04）

`LiteRawDepthReceiptTests` 與純合成 `Fixtures/lite_raw_receipt.json` 已納入 Xcode 測試 bundle。fixture 的 metadata/depth/JPEG 來自原生 Swift 封裝、receipt 來自 Python LocalStore save_capture，不使用個案照片。驗證正確回執、逐欄修改／刪除、bundle 長度／型別、metadata 或 raw bytes 更動、跨安裝／跨拍攝回覆。

Release Simulator **76/76，0 skipped**；完整 engineering runner **81/81 測試檔**，包含原始深度契約與持久保存的新 suites，原始碼 snapshot 前後一致，runtime 已清除。這更新的是本機候選驗證，不更新先前固定 12 檔修補包的內容或證據；也不代表真機、部署端、App 上傳佇列或正式撤回整合已通過。


### 原始資料包清理驗證（2026-10-04）

新增 `erase_installation_objects`，僅清除指定 installation 的 `.rgbd` 物件；先驗證整份清單的範圍和檔名再刪除，逐件確認不存在，最後再次列出清單。遇到未知物件、讀取或刪除失敗、清理期間新增物件均不回報完成。可重試且不觸及名稱相近的其他裝置。13/13 儲存測試通過，使用 temporary LocalStore 與 fake transport，未刪除任何線上資料。

此 helper 尚未接到公開 DELETE 路由，不是完整撤回證明。呼叫者仍須驗證所有權、先持久化撤回狀態並阻止／排空同時進行的寫入，清理後持續拒絕舊授權寫入。清單為空僅證明當次讀取；不能防止晚到請求重新建立檔案。GCS 版本、soft delete 與備份清除也不在此 helper 保證內，須納入保留與撤回政策驗證。
