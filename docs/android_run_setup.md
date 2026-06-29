# Android 最少步驟跑起模擬驗證（範例圖/實拍）

讓 `SamplePickerScreen` + `SampleValidationTest` 能在你本機實機/模擬器跑端上管線(分割→ArUco面積→組織v2→PUSH)。

## 1. build.gradle（app 模組,dependencies 區）加入
```gradle
dependencies {
    // ONNX Runtime(端上分割 student_fp16)
    implementation "com.microsoft.onnxruntime:onnxruntime-android:1.17.0"
    // OpenCV(ArUco 偵測;4.7+ 才有 objdetect.ArucoDetector)
    // 方式A:用社群 Maven(QuickBirdStudios)
    implementation "org.opencv:opencv:4.9.0"
    // 方式B:官方 OpenCV Android SDK 匯入為 module 再 implementation project(":opencv")
    // Compose 導覽 / ViewModel
    implementation "androidx.navigation:navigation-compose:2.7.7"
    implementation "androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0"
    implementation "androidx.activity:activity-compose:1.8.2"
    // 協程
    implementation "org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3"
    androidTestImplementation "androidx.test:runner:1.5.2"
    androidTestImplementation "org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3"
}
```
> 若用官方 OpenCV SDK，`import org.opencv.objdetect.*` 路徑相同;社群套件版本需 ≥4.7。

## 2. App 啟動初始化 OpenCV（Application 或首個 Activity onCreate）
```kotlin
import org.opencv.android.OpenCVLoader
if (!OpenCVLoader.initDebug()) Log.e("OpenCV", "init 失敗")
```

## 3. 放模型與範例圖
- `student_fp16.onnx` → `app/src/main/assets/student_fp16.onnx`（執行 `engineering/phase2/deploy_student.py` 產生）。
- 範例圖(實機驗證測試用) → `app/src/androidTest/assets/sample_std.jpg`（用 `test_images/傷口測試範例照片_標準化/` 任一張）。

## 4. 跑起來（兩種）
**(a) App 內互動**：在某 Activity setContent 掛 `SamplePickerScreen(measureVM)`
```kotlin
val analyzer = WoundAnalyzer(OnnxSegmentationModule(this).also { runBlocking { it.loadModel() } })
val vm = MeasureViewModel(analyzer, ArucoDetector())
setContent { MaterialTheme { SamplePickerScreen(vm) } }
```
→ 「載入範例圖/拍照」即跑管線,畫面顯示 面積/組織/PUSH/信心度。

**(b) 自動化實機測試**：`./gradlew connectedAndroidTest`
→ 跑 `SampleValidationTest`;Logcat 搜「WoundValidation」看面積/PUSH/route。

## 5. 精確度檢核（對齊後端）
- 用標準化照片(已知乾淨 ArUco) → 面積誤差應 ~2.7%(與 `新ArUco校正_報表.json` 同量級)。
- PUSH/組織 應與後端 `/api/v1/classify` 一致(同 SSOT;金標 `engineering/generated/*_golden.json`)。
- 純計分(免裝置)：`./gradlew test`(PushScorerTest/TissueClassifierV2Test)。

## 6. 常見錯誤
- `ArucoDetector` 找不到類別 → OpenCV 版本 <4.7 或未連結;改方式B 官方 SDK。
- 面積 null → 範例圖未含可偵測貼紙(graceful);換含 square_20mm_v2 的標準化圖。
- 模型載入失敗 → assets 無 student_fp16.onnx 或檔名不符(OnnxSegmentationModule.MODEL_FILENAME)。
