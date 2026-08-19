import UIKit

/**
 地端分割模型的**保留槽**（2026-08-18 規劃；模型成熟後直接上機）。

 ## 上機程序（不動任何流程碼）

 1. 把訓練好的 Core ML 模型命名為 `WoundSegLite.mlmodel`（或 .mlpackage），
    放進 `iOS/WoundLite/Models/`。project.yml 的該目錄是 optional glob——
    `xcodegen generate` 後 Xcode 會自動編譯進 bundle（.mlmodelc）。
 2. 實作下方 `segment(_:)` 的模型推論段（規格見 `Models/MODEL_SPEC.md`）。
 3. 完成。`available` 轉真後，`LiteMeasureVM.ingest` 的來源優先序
    （地端 → 雲端 → 手動）自動改走這裡，離線也能自動圈選。

 ## 為什麼現在就把槽位鑄好

 流程分流、來源標記（`source: "local"`）、單一中心傷口、微調入口全都已接妥；
 模型上線那天只補「張量進、遮罩出」一段，不需要再動 UI 與資料鏈。
 */
enum LiteLocalSeg {

    /// bundle 內存在已編譯模型 → 啟用地端路徑。
    /// Xcode 會把 .mlmodel 編成 .mlmodelc 目錄；查編譯後的名字才對。
    static var available: Bool {
        return Bundle.main.url(forResource: "WoundSegLite", withExtension: "mlmodelc") != nil
    }

    /**
     影像 → 傷口輪廓（**原影像座標**）。

     模型上線時的實作骨架（規格詳見 MODEL_SPEC.md）：
     1. `work` 置中裁縮到模型輸入（例 512×512 RGB）。
     2. Vision/CoreML 推論 → 二值機率圖，閾值 0.5 → mask。
     3. `MaskTrace.traceAllBoundaries(mask, ...)` → `MaskTrace.rdp(eps: 1.5)`
        → 座標換回原影像空間（同 `LiteTrace` 舊實作與醫療版 finish() 的走法）。
     回 nil＝模型缺席或推論失敗 → 呼叫端自動退雲端/手動，**不擋流程**。
     */
    static func segment(_ work: UIImage) async -> [[[Int]]]? {
        // 佔位：模型未上線。刻意回 nil 而不是 fatalError——available 與實作
        // 分開檢查，就算有人先丟了模型檔沒補這段，App 也只是退回雲端/手動。
        return nil
    }
}
