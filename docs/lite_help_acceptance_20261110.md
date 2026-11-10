# WoundAILite 操作協助驗收 — 2026-11-10

## 結論與變更

操作指南已有 Apple 裝置端 AI 主題分類，並不是尚未實作。11/10 前一版上市表把它
和廣告一起列成「仍待實作」，本輪已更正。AI 只回傳主題代碼；畫面只顯示原本寫好的
說明，不接受自由文字、動態連結或操作指令，也不讀取照片、帳號與研究資料。

但「有模型」不代表分類可靠。這輪不改產品提示、不新增雲端客服、不更動方案額度，
也不採用沒有改善的提示修改。新增一項原生 UI 驗收：沒有關鍵字匹配仍能打開離線
PDF 說明，重新啟動不保留問題。原有 PDF 搜尋畫面測試一併執行。

本機 iOS 26.2 專用模擬器：5 項原有指南 XCTest、2 項 UI 測試全部通過，零失敗、
零略過。UI 測試包含上述 1 項新增測試；兩份 `.xcresult` 原始產物和 3 張截圖保留。
截圖中的 1.0（43）是 repository 候選專案預設版號，不是新的 TestFlight 交付，
不能把這次編譯當作使用者手機的 44／本機簽署候選 45 已重新驗收。

## 模型實測範圍

基準程式 `bc728b5` 的 `LiteOperationGuide.swift`，先逐字抽出 View 前面的原有型別與
函式，連同測試驅動器編成 Mac 可執行檔；沒有更動分類或允許清單。
執行環境 macOS 27.2（26B123）、Apple Silicon、Xcode 27.2（27B52）、Swift 6.4.2。
`SystemLanguageModel.default` 回報 available，`zh-Hant` 支援，採 greedy／20 tokens。
**不是 iPhone 實機 AI 驗收，也不是模擬模型回應。**

| 合成問題集 | 與預期主題相符 | 操作問題相符 | 非操作／醫療／規則改寫未導向主題 |
| --- | --- | --- | --- |
| 初始 18 題 | 12/18 | 6/12 | 6/6 |
| 在第一個改版前保留的 26 題 | 20/26 | 16/22 | 4/4 |
| 最後原版確認 15 題 | 11/15 | 7/11 | 4/4 |
| 原版合計 | 43/59 | 29/45 | 14/14 |

有 3 題在真正 UI 已可由關鍵字找到，正常不會呼叫 AI。其餘 56 題的 AI 路徑為
41/56 相符（操作 28/43、非操作 13/13）。上表是直接呼叫分類器的測量，不能把它
當作整個 App 的成功率。題目由工程端設計、樣本少、單一平台及 OS，不代表臨床、
不同語言或不同裝置的準確率。單次原版查詢 p50 約 0.65 秒、p95 約 1.17 秒，包含
冷／暖啟動混合，不能保證 iPhone 的延遲。

實際錯分包括：買過後換手機被分到帳單而非恢復購買；不再提供資料做研究被分到帳單；
拍攝距離被分到面積或圈選。輸出允許清單擋住自由生成內容，但不保證選對主題。

另外兩個暫存提示候選在原 18 題皆為 13/18，在 26 題皆為 19/26（原版 20/26）；
沒有一致改善，均未寫入產品。第二候選是在看過 26 題結果後評估，屬探索測試，不能再
稱為盲測。全部原始結果、未採用版本及 5 題原始輸出診斷均保留，合計 152 次本機模型
呼叫；不把「有回應」計作測試通過。

## 操作、安全與成本

- 預設仍是關鍵字搜尋及全部主題。沒有匹配且模型與繁體中文可用時，使用者才可點 AI。
- 一般版不列個人服務主題；App 只接受當版清單中的代碼。點入後顯示選單路徑與固定說明，
  不會代為購買、退款、刪帳或撤回。AI 未匹配、不可用時仍可查說明。
- 本輪僅傳合成文字給裝置端模型。程式路徑不呼叫 WoundAI 服務、不查模型餘額，沒有
  新增伺服器辨識或雲端客服費用。這不是全機封包監聽；Apple 系統的模型下載／診斷與
  裝置電力成本不在此量測內。App 不寫搜尋歷史，但不代 Apple 系統保證任何診斷政策。
- 這是使用協助，不是醫療判讀或完整 AI 客服。iPhone／iPad 模型可用性、真人是否容易
  找到正確頁面、低電量／取消時的體驗仍待外測；本輪無須重拍傷口、購買或改研究同意。

## 重跑與覆核

原始檔在 `woundailite-help-acceptance-20261110` 覆核包：三份原版 JSONL、兩個探索候選、
`source-binding.json`、合成題目、`Replay.swift`、`GuideBefore.swift` 與 XCTest 原始結果摘要。
Mac 可編譯 `swiftc -target arm64-apple-macos26.0 GuideBefore.swift Replay.swift -o replay`，
再以 `./replay cases.json` 重播。需 Apple Intelligence 與繁中可用；不可用時明確退出，
不改用網路模型。此命令只使用包內合成問題。Windows 可核對雜湊和統計，不能重現 Apple 模型。

本輪只新增 UI 測試與核對文件；不部署、不更新 TestFlight，也不改既有紀錄、研究原始檔或稽核。

## 參照

- [Apple：SystemLanguageModel](https://developer.apple.com/documentation/foundationmodels/systemlanguagemodel)：裝置端模型與不同 OS 版本的差異。
- [Apple：生成內容與執行工作](https://developer.apple.com/documentation/FoundationModels/generating-content-and-performing-tasks-with-foundation-models)：先核對模型可用性。
- [Apple：語言與地區支援](https://developer.apple.com/documentation/foundationmodels/supporting-languages-and-locales-with-foundation-models)：依當前裝置核對語言，提供替代入口。
