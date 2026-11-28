# WoundAILite 量測頁啟動與研究選擇的驗收

候選版會先顯示量測頁，再非同步準備裝置上的個人服務元件。SwiftUI 在此期間沿用原有的量測狀態。先前畫面的登入入口會更新，但負責拍攝的物件仍持有啟動時的空值。這會讓後續照片無法建立服務草稿，已存入紀錄的草稿也要等到再次開啟頁面才有機會結案。

修正後，原量測頁在服務就緒時接回同一個服務，重新讀取本機草稿。每張照片沿用開始拍攝時的處理方式。在服務準備期間已經開始修邊的照片仍可手動存入；下一張拍攝可建立個人服務草稿。拒絕研究仍可使用個人辨識，須由使用者選擇照片並送出。候選版的設定只顯示適用的個人服務額度；原研究版仍使用自己的辨識額度。

## Claude 覆核方式

比較父版與此版的 `iOS/WoundLite/LiteMeasure.swift`、`iOS/WoundLite/LiteConsent.swift` 及 `iOS/WoundLiteTests/LiteNativeSessionTests.swift`。使用既有 WoundLite Release 測試設定，執行下列 XCTest。測試使用產生的資料、暫存目錄及合成服務回覆。

| 驗收情境 | 應讀回的結果 |
| --- | --- |
| `testMountedMeasurementAttachesLateSessionAndRecoversCommittedDraft` | SwiftUI 實際掛載量測頁後才注入服務。父版未結案；修正版在同一頁讀回既有紀錄、結案並保留原照片，沒有送出網路請求。 |
| `testLateSessionPreservesOfflineEditsAndAppliesToTheNextCapture` | 開啟服務前拍下的照片能完成修邊與存檔，下一張建立獨立的本機草稿，兩張均未自動上傳。 |
| `testPersonalRecognitionWithoutResearchKeepsTwoSeparateLocalTimelines` | 明確送出辨識後只執行一次。另一傷口手動圈選後另存，兩筆均無研究待傳。第一處的 PDF 可讀回且不含第二處的位置，匯出未呼叫雲端。 |

另以合成後端的 `test_lite_wound_roster`、`test_lite_research_ingest_flow`，及 `test_lite_service_http` 的 `test_research_withdrawal_does_not_reset_or_block_service`、`test_signed_prepare_execute_and_receipt_without_research_media`，核對追蹤名額、重送和撤回後仍可使用個人服務。Apple Sandbox 的真實交易、系統簽署的裝置驗證與外測審查須另行讀回。
