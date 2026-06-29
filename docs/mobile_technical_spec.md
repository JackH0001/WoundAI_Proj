# WoundAI 行動端 技術規格（初步 v1）

範圍：iOS / Android 行動 App（平板 Pad 直/橫版響應式）。後端對接雲端去識別化標註訓練平台。輔助、非診斷、需醫師確認。

## 1. 架構
- **單一真實來源 SSOT**：`engineering/phase0/preprocessing.json` → `gen_preprocessing_constants.py` 產生三端常數（`engineering/generated/{ios,android,windows}`）。三端**編譯期吃同一份**，禁硬編碼。
- **端上推論**：student_fp16.onnx（256/imagenet/NCHW/thr0.4，logits→sigmoid）；ONNX Runtime（Android NNAPI / iOS CoreML 或 ORT）。
- **雙軌路由**：端上 student + wsm 分歧度 IoU<0.5 → 呼叫雲端 `/api/v1/segment/escalate`（A∪U）。對應 `engineering/phase2/dual_track_router.py`。
- **校正**：標準 ArUco（DICT_4X4_50 id=7）→ 面積比例法；`markerMmActive=12`（square_20mm_v2，由 SSOT 帶）。
- **嚴重度**：PUSH（面積子分帶 + 組織子分最差順序 + 滲液醫師輸入），三端讀 `pushAreaBands/tissueWorstOrder`。
- **組織**：tissue v2（白平衡+HSV，遮罩內互斥）。

## 2. 畫面與導覽（對應原型 WoundAI_App_全流程_v2）
個案清單 → (點舊個案) 個案詳情 →{過去紀錄詳情 / 繼續拍攝 / 趨勢} ；(＋新增) 新增個案 → **知情同意+電子簽名** → 拍攝(單螢幕/品質把關) → 量測結果 → 修邊與標註(邊界GT/組織筆刷/放大鏡/治療附註) → 去識別化上傳 → 傷口時間軸(整合趨勢)。
- 同意書**首次新增患者簽署一次**；繼續拍攝回退→個案選擇。
- 平板：直版單欄、橫版雙欄（修邊：畫布左/工具右；時間軸：圖左/歷次右）。斷點 手機<600 / 平板直 600–1024 / 平板橫>1024。

## 3. 資料模型
- **擷取容器**（`captureFields`）：rgb / depth_mm(Float32,LiDAR選配) / intrinsics_K / sticker_pose / timestamp / deidentified。
- **同意紀錄**：consentRequired=[care]、consentOptional=[train]、電子簽名(影像)、簽署時間、可撤回旗標。
- **標註**：gt_polygon、tissue_classmap、exudate、care_note(治療計畫)、correction_iou、doctor_verified。
- 可識別資料(姓名/病歷號)**僅存本機**；上傳前去識別化。

## 4. API 契約（對接後端）
- `POST /api/v1/classify`：image[, cm_per_pixel] → 面積/組織/PUSH/信心度（marker_mm 讀 SSOT）。
- `POST /api/v1/segment/escalate`（JWT）：難例 → A∪U 遮罩(b64)。
- `POST /api/v1/annotation`：gt_polygon/classmap/exudate/care_note/correction_iou/doctor_verified → 飛輪（需去識別+同意）。
- 撤回：`POST /api/v1/consent/withdraw` → 對應去識別資料下架、排除訓練、稽核。

## 5. 隱私與合規（個資法/醫療法）
去識別化（EXIF/個資遮蔽/病歷號雜湊/裁切）+ 雙層同意 + 電子簽名 + 可撤回下架 + 稽核。詳見 `WoundAI_法規與合規送審文件`。

## 6. 實作與驗證狀態
| 項目 | 狀態 | 驗證 |
|---|---|---|
| SSOT→三端常數(含 marker12/PUSH帶/擷取容器/同意) | ✅ 已產生 | 三端一致(markerMmActive=12.0、recommendedSticker=square_20mm_v2 3/3) |
| 端上分割 student_fp16 | ✅ 權重就緒 | fp16 無損 Dice0.764 |
| 雙軌路由邏輯 | ✅ 參考實作+回歸測試 | route 0.900、test_dual_track PASS |
| 面積校正 | ✅ | 乾淨ArUco 5/5、誤差均2.68% |
| PUSH/組織v2 | ✅ 規則+測試 | tissue_severity 4/4、clinical_rules 30/30 |
| 原生畫面(Kotlin/Swift) | ⏳ 待實作 | 需 Android/Xcode 工具鏈(沙箱外);依本規格+generated 常數實作 |

## 7. 下一步(行動端實作順序建議)
1. 各端載入 generated 常數 + student_fp16/ wsm 模型，端上分割→面積→PUSH 串通（單元測試對齊 SSOT 數值）。
2. 拍攝品質把關 + ArUco 偵測 + 知情同意+電子簽名（首次）。
3. 修邊與標註(畫布)＋治療附註；去識別化上傳＋撤回。
4. 平板直/橫響應式；時間軸整合圖。
5. 端到端與後端 `/api/v1/*` 整合測試；對齊 SSOT 計分（三端差異=0）。
