# 獨立示範後端隔離模式驗證 20261013

獨立審查示範環境的後端模式已完成本機實作與回歸。它沿用目前整合版的量測、標註、修訂與深度契約，使用自己的服務、機構和三個資料桶；沒有放寬 MMH 的 IAP、機構綁定或稽核規則。這是部署前驗證，尚未建立雲端服務，也尚未完成 App 環境切換與 TestFlight 外測發布。

## 精確設定與拒絕行為

`review_service_profile.py` 定義 `synthetic-review-validation`，固定 project `woundai-jackh001`、service `woundai-mmh-review-demo`、org `mmh-review-demo`。媒體、安全、稽核三桶分別為 `woundai-mmh-review-{media,security,audit}-421209514056`，prefix 固定 `flywheel`。

只要任一示範識別設定存在，就必須具備完整的精確設定；不能因缺少其他欄位退回 LocalStore、一般醫療服務或 MMH 模式。Lite 必須停用；不允許沿用舊 demo 的 seed 設定。Store 工廠在建構 GCS client 前檢查，不重用其他模式的快取 store。

`SyntheticReviewValidationStore` 共用現有條件式寫入、完整稽核鏈核對和競態處理，但覆寫獨立的資源範圍檢查。仍須即時確認稽核桶沒有保留政策；未鎖定模式不能通過正式七年鎖桶 gate，亦不宣稱 WORM。它同樣存在逐次讀完整鏈的成本，這次沒有解決長期稽核延遲。

## 資料與帳號界線

- 新示範標註由伺服器標記 `deployment_environment=synthetic-review`；不依賴客戶端自報。既有 `source` 欄位維持原值，不新增 MMH 資料來源分級限制。
- 示範服務的研究 manifest 端點拒絕匯出；兩個離線 exporter 在具備示範執行設定時也會拒絕。這不代表任意手動複製出去的資料都已被全域隔離；未來跨環境取件工具仍須逐筆檢查 metadata。
- 機構 JWT 檢查保留；MMH 身分即使用相同使用者名稱也不能用於示範。示範的 admin／review01 尚未在雲端建立。
- 原生傷口遮罩保留孔洞與分離區塊，組織遮罩逐檔核對；相同封包去重，改動產生不同修訂。原始深度回覆綁定相同影像與明確標註收據，不自動轉指最新版本。
- 不重跑模型、不讀取正式資料或密文，不修改現有帳號／IAM／OAuth／流量。後端模式本身不會停用或設定 IAP；示範的無 Google 網路入口另待具體部署與授權。

## 本轮實測

| 檢查 | 結果 |
|---|---|
| 全套隔離工程測試 | 111/111 檔通過 |
| 新示範模式 | 33/33 通過，已包含於上列全套 |
| tools | 29 檔通過，344 個 unittest cases |
| 刻意移除保護的變異測試 | 9/9 被攔截 |
| verify_logic | 50/50 |
| parity_check | 未登記差異 0；保留 10 項已登記差異 |
| diff 格式 | 通過 |

33 項新測試包含錯誤 project／service／桶／org、設定缺漏、禁止舊 seed、禁止混用快取 store、未鎖定稽核的改寫及 generation 競態、JWT 跨機構拒絕、實際 annotation／depth HTTP 藍圖與成對遮罩讀回、去重、撤回和匯出限制。HTTP fixture 使用隔離 LocalStore 與正式機構 JWT 回呼，GCS 行為用 fake client；不是雲端驗收，也沒有用本輪結果宣稱兩平台實機通過。

9 項變異分別針對 project 精確範圍、桶參數、舊 seed、部分設定、store 工廠、伺服器 metadata、manifest、wound exporter、tissue exporter。每次在暫存複本移除一項保護後，新測試失敗；原始工作樹保持完整。

首輪全套為 110/111：新測試預期所有錯誤均為 PermissionError，但不認識的 audit mode 先由既有 guard 回 RuntimeError。測試改為接受這兩種拒絕，仍要求完全沒有建構雲端 client。產品驗證未被放寬；原失敗日誌保留。

執行環境為 macOS Python 3.12.14；沒有 Windows／PowerShell 實跑。後端與 engineering 仍交付 patch，未改 owner_guard 規則或聲稱跨平台提交已通過。

## 下一個發布關卡

1. App 固定環境 context：目前 ClinicalUploadOutbox、ConsentSync、ReviewView、MeasureFlowView 和設定頁仍有 MMH 全域設定引用。必須讓一筆作業從登入到保存都使用同一個不可變 context，切換後舊回覆不可寫進新環境。
2. 明確的示範入口、獨立 WoundAI 帳號登入，以及只允許固定示範 origin 的無 Google transport；MMH 保留 Google IAP。
3. 固定並覆核來源後，準備具體雲端資源、權限與建置／回復命令，再取得新增資源及對外入口的授權。
4. 真實 GCS 重啟讀回、合成 RGB-D 全流程、兩平台實機與商店安裝驗收，才可提交審查並回報外測完成。

基底為 `dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00`，前置依序為 W0、W1–W3、native-wound-mask patch。本輪 patch 不含既有手機改动，且尚未 commit／push。完整日誌、可重建 patch 與雜湊清單位於 `review-profile-validation-20261013` 證據資料夾。
