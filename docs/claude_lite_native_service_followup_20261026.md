# WoundAILite 原生服務回執與恢復覆核 — 2026-10-26

本輪接續 `915742e880b3b064c1edc1f2a33773b15e75847e` 的獨立服務後端。
完成 iOS 可注入的操作日誌、已登記裝置簽署傳輸與結果核對；沒有接入公開操作畫面，
沒有變更研究同意、現有影像／歷史資料、每日額度設定或 App 組建號。
原有免費 2 次、Plus 總計 10 次的工程契約不變。

## 已實作與驗證

- 每張原始正規化 JPEG 使用固定 capture／operation ID，保存影像雜湊、尺寸、
  裝置身分、原始日與收到的 request digest。重試不產生新 ID。
- 每次傳送前先持久化「下一步查詢 status」。若回覆遺失、程序重啟或本機存檔失敗，
  先查原回執；已完成／仍處理中時不讀取或再傳照片，只有伺服器明確 ready 才送原影像。
- 同時點擊、CAS 衝突、磁碟寫入錯誤、損壞日誌、身分更換、原圖不符均停止或保留原工作。
  每次 resume 最多三個業務請求，未新增背景輪詢器。
- Swift 與 Python 共用三條服務路徑的 App Attest 簽署位元組 fixture；簽署器沿用既有
  裝置身分，服務許可由呼叫者明確提供，不會因研究撤回自動重新登記或建立新研究身分。
- 後端新增 `result_json`，提供與回執 SHA-256 對應的原始 canonical JSON 字串。
  手機驗證原字串雜湊、解碼結果、尺寸／座標／數量、操作／日／grant／期限一致性。
  避免 Swift 與 Python 浮點數重新序列化差異造成假性不一致。
- 後端明確 404 `lite_operation_unknown`。只有尚未固定回執時，才能以同 ID 再 prepare；
  已固定回執卻找不到操作則停止核對，不能當作新辨識。
- 提示值分開：已核對辨識結果、未存入量測紀錄／研究庫、額度用完、容量等待、
  驗證／網路未完成、資料不一致及查詢期限已過。這是可測試的訊息值，尚未接上公共 UI。

## 本輪測試證據

| 組別 | 結果 | 範圍 |
| --- | --- | --- |
| Python 後端／契約回歸 | 390 通過，0 失敗 | 合成裝置與簽章、Flask、localhost HTTP、配額、修訂、撤回、原始深度與 Apple 驗證器 fixture |
| iOS Debug，WoundLiteCommerceLab | 270 通過、1 跳過、0 失敗 | 含本機 StoreKit、既有研究流程與新增原生服務 26 項 |
| iOS Release，WoundLite | 260 通過、1 跳過、0 失敗 | 同一份服務 26 項；Release 使用 ENABLE_TESTABILITY=YES 供 XCTest 存取 |
| 新增跨語言契約 | 2 通過 | 已包含在 Python 390 項，不重複加總 |
| 新增原生服務測試 | Debug／Release 各 26 通過 | 已包含在上列 iOS 數字，不把雙組建當作 52 個獨立測試 |

兩個 iOS 組建均只跳過 `HealthDataBackupTests/testDeviceFileProtectionClassSurvivesAtomicReplacement()`：
模擬器不回傳 protectionKey，仍需日後實機驗收，不算通過。
本機環境為 Xcode 27.1、iOS 18.6 iPhone 16 Pro Max 模擬器、Python 3.12。
原生 HTTP 使用受控 mock；Python 另外有真正 127.0.0.1 HTTP。
不是實體手機 → Apple → 雲端服務全鏈路驗收，亦非分割／面積／深度準確度測試。

初始簽署失敗是 Documents 建置產物被檔案同步服務增加 FinderInfo，改在 `/private/tmp` 建置後通過；
初次 Release 測試缺少 `ENABLE_TESTABILITY=YES`，補上測試參數後通過。
fixture 初次比對包含隨機測試 installation，已只將頂層身分固定為 synthetic-native-service，
實際後端回覆欄位及雜湊未改動。初始與最後日誌皆保留，重跑不重複計數。

## CI 與覆核方式

- Linux 手動 workflow `lite-prelaunch.yml` 已納入跨語言契約檢查。
- macOS 手動 workflow `lite-commerce-lab.yml` 已納入原生服務／簽署器 Debug 與 Release 測試。
- 本輪只做本機測試、YAML 與 shell 語法檢查；沒有推送或觸發遠端 Actions。
- Python：依 `lite-prelaunch.yml` 與 `requirements-commerce-test.txt` 的固定依賴，
  設定 `PYTHONPATH=tools:engineering/phase2:Backend/Flask:.`，執行
  `python -m unittest -v test_lite_service_http test_lite_service_loopback test_lite_native_service_contract`。
- iOS：完整相同 checkout 執行 `cd iOS && xcodegen generate`，依 macOS workflow 執行。
  覆核包包含精確變更與必要 Python 依賴；不是完整 iOS 專案，不宣稱單靠 ZIP 可建置 App。

## 成本與尚未啟用項目

重跑原 GCS adapter 假傳輸探針：新工作 5 寫／37 讀，完成後 status 0 寫／10 讀。
本輪沒有增加後端儲存操作；實際回覆現在多帶一份 canonical 結果字串，會增加回應 bytes，
尚未計入完整營運成本。只證明避免特定重試情境重送 JPEG／重跑模型，不能宣稱整體已盈利。
廣告正式投放、Apple 真實 Sandbox、實際雲端延遲與 egress 尚未驗證。

啟用前仍須完成：可信服務帳號註冊／訂閱升級與恢復、原生 factory／UI 接線、舊額度凍結與基準
遷移、結果連接歷史及可選研究標註／深度、結果暫存生命週期與未知工作核對。
目前 24 小時只是可讀期限，**不是物理刪除**。清理與競爭方案見
`engineering/contracts/lite-native-service-v1.md`；沒有執行排程、GCS 清理或原資料變更。

## 建議 Claude 優先覆核

1. 原生回應不明、CAS／寫入失敗及跨身分時是否可能產生第二操作或誤顯示成功。
2. `result_json` 的原位元組驗證、圖像座標界線與 grant／期限固定是否完整。
3. 服務許可是否確實與研究同意分開；既有研究撤回保護是否保持。
4. 日後清理只能壓縮結果 payload、保留終態／稽核與 ID tombstone，不能把舊 ID 變成新工作。
5. 實際接線前，需明確解決 claimed-but-no-result 及長期 pending，不能只讓使用者持續等待。

本輪沒有部署、更新 TestFlight、公開上架或傳送此文件給第三方。
