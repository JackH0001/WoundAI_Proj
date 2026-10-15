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

## 7. CameraX 高解析拍攝（CameraCaptureScreen）
build.gradle 加：
```gradle
implementation "androidx.camera:camera-core:1.3.4"
implementation "androidx.camera:camera-camera2:1.3.4"
implementation "androidx.camera:camera-lifecycle:1.3.4"
implementation "androidx.camera:camera-view:1.3.4"
```
AndroidManifest.xml：
```xml
<uses-permission android:name="android.permission.CAMERA"/>
<uses-feature android:name="android.hardware.camera.any"/>
```
執行期請求 CAMERA 權限後，`setContent { CameraCaptureScreen(measureVM) }` → 預覽+「拍攝」→ 全解析 Bitmap(已修正旋轉) → `vm.analyze`。
> CameraX 1.3+ 才有 `ImageProxy.toBitmap()`;舊版需自行 YUV→Bitmap 轉換。

## 2026-10-13 MMH 私密行政副本（本機候選，尚未發布）

MMH 的新候選版在後端連線設定提供「建立行政影像與結果副本」，預設關閉；啟用後加密保存在不備份的 App 私密空間，不自動寫相簿。需要交付時，按「選擇副本並匯出…」選檔，使用系統文件選擇器另存 ZIP。MMH 與合成示範的設定／副本分離，關閉不刪舊檔或影響研究上傳。完整操作、版本限制與測試參照 [私密副本驗證](android_mmh_private_exports_validation_20261013.md)。此說明不代表現行 Play 26 已更新。

### 2026-10-13 本機候選：時間軸行政副本

啟用行政副本後，新量測和時間軸修訂會先保存紀錄，再產生該保存版本的副本。處理期間等待進度提示完成；同內容不增加副本，修訂內容保留新版本。設定的副本清單以紀錄 ID／修訂摘要識別，拍攝副本另外列出。時間軸成功、行政副本成功與雲端保存成功是不同狀態；副本失敗會明確提示，時間軸紀錄仍保留。尚未發布，詳見 `android_mmh_saved_export_validation_20261013.md` 的驗收結果與限制。

### 2026-10-13 本機候選：研究待傳自動接續

按「確認送出研究資料」表示由目前登入醫師確認此保存版本。等待「已加密排入待傳」後可離開量測頁；到設定或紀錄詳情查看「研究待傳」。只有「研究標註已確認保存」才代表取得有效雲端收據，Android 會明示本筆沒有深度。網路錯誤會退避重試；登入／同意問題先處理，收據異常則核對後按「重新核對並重試」。使用原送件帳號，換帳號不會代送別人的工作。系統暫停或關閉 App 時，下次開啟並登入才接續。第一筆入列仍需當下身份驗證可用；未入列成功的紀錄須再次明確送出。此版本尚未發布，驗證與限制見 `android_clinical_outbox_integration_validation_20261013.md`。
