# Android `MultiModelClassifier` graceful-degrade 修補指引（範例）

**問題**：目前 `MultiModelClassifier` 直接載入 `models/wound_type_fp16.tflite` 等三個**不存在**的檔 → 會崩潰或產生假結果。

**原則**：以 feature flag＋資產存在檢查雙閘控；不存在則停用該功能並回報，UI 顯示「開發中」，**不得回傳偽造標籤**。

```kotlin
// 範例（示意，非可直接編譯）：以資產存在檢查 + flag 取代直接載入
private fun assetExists(name: String) =
    runCatching { context.assets.open(name).close() }.isSuccess

fun classifyOrNull(kind: ClassifierKind): ClassificationResult? {
    val flagOn = FeatureFlags.isEnabled(kind.flagKey)        // 預設 false
    val modelPath = kind.assetPath                            // e.g. "models/tissue_fp16.tflite"
    if (!flagOn || !assetExists(modelPath)) {
        Log.i(TAG, "classifier ${kind.name} 停用（flag=$flagOn, model=${assetExists(modelPath)}）")
        return null                                          // graceful degrade，UI 顯示「開發中」
    }
    // ... 僅在此之後才載入 interpreter 並推論，輸出附 confidence 與「需醫師確認」
}
```

UI：分類結果區若為 null → 顯示「AI 分類（開發中）」並提供「手動標註」入口（human-in-loop，且回饋為訓練資料）。
