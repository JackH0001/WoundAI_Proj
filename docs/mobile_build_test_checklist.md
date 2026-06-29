# WoundAI 行動端 本機編譯與測試 檢查清單 v1

目的：在你本機(Android Studio / Xcode)驗證行動端骨架,確認三端計分與後端一致(差異=0)、面積校正準度、合規守門。輔助、非診斷、需醫師確認。

## A. 前置依賴
**Android**
- Android Studio + JDK 17、Android SDK。
- **OpenCV Android SDK 4.7+**（內含 `org.opencv.objdetect.ArucoDetector`；若用舊版 `org.opencv.aruco` 需改 import）。
- ONNX Runtime Android（`com.microsoft.onnxruntime:onnxruntime-android`）。
**iOS**
- Xcode 15+。
- **opencv2.framework**（cv::aruco；Apple Vision 無 ArUco）+ Objective-C++ 橋接實作 `ArucoDetecting`。
- ONNX Runtime / CoreML（CoreML 用 `StudentSeg.mlmodel`）。

## B. 模型與常數放置
- `student_fp16.onnx` → Android `app/src/main/assets/`；Backend `models/`。
- `wsm.onnx`（雙軌備援，選配）→ assets。
- iOS：`StudentSeg.mlmodel`（內含正規化+sigmoid）。
- 雲端 A∪U：`a_unet.onnx`/`unetpp.onnx` → Backend `models/`（端上不放）。
- **先重生 SSOT 常數**：`python engineering/phase0/gen_preprocessing_constants.py`（產出 `engineering/generated/{android,ios,windows}`，已含 marker12/PUSH帶/擷取容器/同意）。建議納入 CI 前置步驟。
- ⚠ 權重為私有 IP，勿入協作 repo（.gitignore 已用 *.onnx 排除；stub 例外）。

## C. 單元測試（金標 = 後端 SSOT）
**Android**：`./gradlew test`
- `PushScorerTest`：面積子分 0..10、8.66→12/14、2.78→8/10、0→2/None。
- `TissueClassifierV2Test`：7 組 RGB→組織碼（暗紅→肉芽、暗低飽和→壞死…）、HSV≈OpenCV。
**iOS**：Xcode Test（⌘U）
- `PushScorerTests`、`TissueClassifierV2Tests`（同金標）。
金標來源：`engineering/generated/push_golden.json`、`tissue_golden.json`（由後端 clinical_rules / cv2 產生）。
**通過條件**：兩端全綠 → 三端計分與後端**差異 0**。

## D. 端到端手動驗證
1. 用標準化照片 `test_images/傷口測試範例照片_標準化/`（已合成乾淨 ArUco square_20mm_v2）。
2. App 拍攝/載入 → `MeasureViewModel.analyze` → 結果卡。
3. 對照後端數值：
   - **ArUco 偵測**：5/5。
   - **面積誤差**：方形 20mm 應 ~2.7%（與 `WoundAI_Web*`/驗證圖一致）。
   - **PUSH/組織**：與後端 `/api/v1/classify` 同（同 SSOT）。
4. 雙軌：人為降低品質/遮罩分歧 → route 應轉 cloud（呼叫 `/api/v1/segment/escalate`）。

## E. 合規守門驗證
- 知情同意①未勾或未簽 → 快門鎖定（不可拍）。
- 同意②未勾 → 不上傳訓練；撤回 → 雲端下架、排除訓練、稽核。
- 上傳前去識別化（EXIF/個資/雜湊/裁切）。

## F. 驗收矩陣
| 模組 | 測試/方法 | 期望 |
|---|---|---|
| PUSH 計分 | PushScorer(Test/Tests) | =金標 |
| 組織 v2 | TissueClassifierV2(Test/Tests) | =金標、HSV≈cv2 |
| 面積校正 | 標準化照片實測 | 偵測5/5、誤差~2.7% |
| 雙軌路由 | 分歧度測試 | 難例 route=cloud |
| 三端一致 | 三端跑金標 | 差異 0 |
| 合規 | 同意/去識別/撤回 | 守門生效 |

## G. 排錯
- OpenCV 找不到：確認 SDK 已加入 module、`OpenCVLoader.initDebug()`（Android）/ framework 連結（iOS）。
- ArUco API：4.7+ 用 `objdetect.ArucoDetector`；舊版 `aruco.Aruco.detectMarkers`。
- 面積為 null：未偵測到貼紙（corners=null，graceful）→ 檢查拍攝品質/字典/ID(7)。
- 計分不一致：確認已重跑 codegen、generated 是最新 SSOT（sha 比對 banner）。

---
*對應：docs/mobile_technical_spec、engineering/generated/*_golden.json、pipeline/* 骨架。本機工具鏈外的編譯/實機測試由你執行；沙箱已驗證演算法對金標、面積公式對後端、HSV 對 cv2。*
