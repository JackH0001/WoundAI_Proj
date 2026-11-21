# WoundAILite 操作指南：iPhone 實際分類與畫面驗收狀態

2026-11-12；產品來源 `4b2f5d73e394b8d394d68bcefe902a91d65de9c0`。
本輪不改產品提示或功能，不以 Mac 結果代替手機。仍維持可選的主題索引與離線固定說明，
未宣稱可靠 AI 客服、外測送審或全部功能驗收完成。

## 裝置與隔離

- 實體 iPhone 16 Pro Max，裝置工具／XCTest 回報 iOS 27.2.1 (24B156)。
- 來源中的 `LiteOperationGuide.swift`、`LiteGuideCopy.swift`、中英文 `LiteGuide.strings`
  逐位元組複製到獨立測試 host，SHA-256 與 Git 來源一致；沒有採用上一輪被否決的精簡提示。
- 重用既有本機簽署資產，不使用 provisioning update，不建立 Apple 憑證或上傳商店。
- 獨立 QA bundle 為 `com.woundai.storageqa`，測前不存在；不覆蓋 `com.woundai.lite`。
  TestFlight 44 的 app metadata 測前後相同，未讀取其資料容器。此證據只支持未操作容器，
  不能寫成逐筆紀錄／媒體雜湊已比對。
- 本輪兩個合成 QA App 已移除並讀回確認。第一次清理遇到裝置連線中斷，保留錯誤紀錄；
  重新唯讀確認可連線後才續接清理，原 TestFlight 44 的 metadata 再次核對相同。
- 測試只輸入 60 題合成文字，沒有傷口照片、帳號、Apple 憑證、研究資料或雲端辨識呼叫。
  沒有執行全裝置網路封包監聽，不代 Apple 系統保證診斷或模型下載行為。

## 真實模型結果

SystemLanguageModel.default 在手機上回報可用，繁中、英文均可用；greedy、最多20 tokens。
每題每語言單次執行。兩個 XCTest 測量驅動器通過，零失敗／略過；這表示完成收集且未遇到
unavailable，**不表示60題分類全對**。題目與上一輪 Mac 比較相同，沒有依手機結果調參。

| 合成題目 | iPhone 正確主題 | 同題 Mac 原版 | 非操作題回 none | 實際無關鍵字匹配的 AI 路徑 |
| --- | --- | --- | --- | --- |
| 繁中30題 | 24/30 | 24/30 | 5/8 | 16/20 |
| 英文30題 | 21/30 | 29/30 | 5/8 | 12/16 |

題目由工程端設計，包含22題操作、8題非操作／醫療／要求代為執行。不是獨立真人盲測或
臨床資料，不適合推估所有使用者準確率。這次手機與 Mac 的英文結果不同；OS、模型與
執行環境均不同，不能把差異歸因於單一硬體或宣稱某機型較差。

兩語均曾把「面積變小，是否可以停止換藥」導到面積說明，把直接要求代為取消／刪帳或
解除額度限制的句子導到固定主題。允許清單仍只回傳主題 ID，畫面不顯示模型自由回答，
也不觸發付款、刪帳或提供醫療判讀，但這些結果不符合原定 none 分類目標。

操作問題亦有拍攝擺位錯分、取消續費被當成恢復／購買。離線關鍵字可能先匹配部分題目，
所以分類器總分不等於整個 App 的使用成功率；無關鍵字子集另列於上表。

## 畫面測試尚未完成

準備四項實機 UI 測試：繁中／英文 PDF 查找，以及兩語在無關鍵字時仍可開固定說明、
重開不保留查詢。Xcode 的 runner 在啟用 UI automation 時逾時：

`The test runner failed to initialize for UI testing. Timed out while enabling automation mode.`

四項測試均未開始。xcresult 記錄1個 runner failure、0項通過；這不是4項產品測試失敗，
也不是通過。再次唯讀核對手機仍解鎖。已請使用者確認是否有系統自動化提示，尚待回覆，
沒有改動手機的隱私或開發者設定。保留首次失敗原始紀錄，不以重跑覆蓋。

## 決策與下一步

- 保留原提示及離線全部主題；不因某一語言分數改善而採用跨語言退步的候選。
- 尚不能將「AI 使用輔助」標為可靠客服完成。後續以實機畫面、真人能否找到正確操作頁
  為驗收，避免持續只對同一題集調提示。
- 維持顯示「AI 建議的主題可能不符」，使用者須核對標題與操作路徑；此功能不讀取健康
  資料、不代為變更服務狀態。真實 Apple／後端端到端與外測送審仍另行完成。

## 覆核與重跑

本機證據：`woundailite-guide-device-20261112`。覆核包含精確來源、隔離 host、兩個測量
XCTest、四個 UI 測試、原始合成逐題輸出、可用性、來源雜湊及統計重算工具。
原始 xcresult 與裝置診斷留本機；公開覆核只包含必要的合成附件與去除裝置識別碼的摘要，
不把診斷封存、簽署描述檔或裝置資料容器納入。

Windows 可核對 SHA-256、逐題預期及統計，不代表在 Windows 重跑 Apple 模型。
重跑手機須相容 Mac／Xcode、已配對解鎖手機與既有隔離簽署資料；UI 自動化需先排除
上述啟動問題。不得改用 TestFlight bundle 作測試 target。

參照：[Apple Foundation Models](https://developer.apple.com/documentation/foundationmodels/generating-content-and-performing-tasks-with-foundation-models)、
[Apple 生成輸出安全](https://developer.apple.com/documentation/foundationmodels/improving-the-safety-of-generative-model-output)。
官方文件是模型可用性、限制輸出與替代體驗的設計依據，不是本 App 準確率證據。
