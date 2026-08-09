# Handoff — iOS 對齊 Android v21 ＋ WoundAI3D 深度地基（2026-08-08～09）

**分支**：`ios-verify`（基底 `c4f97e3`，其上為本輪未提交變更）
**版本**：兩端一致 **21**（iOS `project.yml` CURRENT_PROJECT_VERSION；Android `version.properties`）

---

## 一、本輪成果摘要

1. **iOS 從「前半段垂直切片」補到與 Android v20/21 工作流對齊**：量測存檔／時間軸（趨勢圖×2＋解密縮圖＋±%＋徽章）／修邊畫布（921 行級全功能移植）／送訓練標註／複核（重修邊＋補送）／相機（拍照/相簿/檔案）／個案部位型態輸入＋摘要／結案與刪除／使用手冊（共用 manual.html）／主控台一次性登入碼入口／90 天清除。
2. **多輪廓資料鏈**（classify `wound_polygons` → PolygonJson → 畫布 → `gt_polygons`/`area_cm2`）兩端同構。
3. **WoundAI3D 深度 MVP**：LiDAR RGB-D＋內參擷取、AES 加密 sidecar（不動 DB schema）、`depth_source` 三值真值（none/lidar_local/lidar）。規劃見 `docs/woundai3d_depth_capture_plan.md`。
4. **Android 回頭同步**：結案＋空個案刪除＋未送出量測刪除（repo 層）、個案頁兩顆動作鈕、ArUco 貼紙誤認輪廓排除＋提示。
5. **修掉的真 bug**：iOS Release 預設後端指向舊部署（→ `woundai-backend-421209514056`）；實機 Debug 預設 localhost（→ 模擬器才 localhost）；冷啟動吃在登入步而無進度提示；筆刷單位 px/pt 差三倍；`&&` autoclosure 內 await。

## 二、變更檔案

**iOS 新增（10）**：`Core/PolygonJson.swift`、`Core/MaskTrace.swift`、`Core/DepthCapture.swift`、`UI/WoundEditView.swift`、`UI/TimelineCharts.swift`、`UI/CameraCaptureView.swift`、`UI/ReviewView.swift`、`UI/ManualView.swift`、`Resources/manual.html`（複製自 Android assets，兩端共用）、`WoundAITests/EditPipelineTests.swift`（15 案）

**iOS 修改**：`BackendClient`（wound_polygons／gt_polygons／area_cm2／depthSource）、`TissueCodes`（網格分類＋3×3 多數決）、`Entities`（多輪廓存取）、`CaseRepository`（updateMeasurement／deleteCaseIfEmpty／deleteMeasurementIfUnsubmitted／purge 連動深度）、`LocalImageStore`（警告修正）、`AppSettings`（URL 修正＋冷啟動追蹤）、`MeasureFlowView`（存檔/修邊/送標註/三入口/貼紙排除/滲液鎖）、`CaseAndConsentViews`（部位型態輸入＋摘要＋時間軸完整版＋設定主控台＋刪除選單）、`WoundAIApp`（.review/.manual／清除接線）、`Info.plist`、`project.yml`（schemes＋resources＋v21）、`.gitignore`（xcodeproj 排除）

**Android 修改（7）**：`WoundCaseDao`／`MeasurementDao`（delete/getById）、`data/repo/CaseRepository.kt`（兩支刪除規則）、`CaseSelectScreen`（結案/刪除鈕）、`MeasureViewModel`（貼紙排除）、`MeasureValidationEntry`（排除提示）、`version.properties`（21）

**docs**：`ios_android_parity.md`（狀態更新）、`ios_completion_plan.md`（進度記錄）、`woundai3d_depth_capture_plan.md`（新）、本檔

## 三、已驗證

| 項 | 方式 | 結果 |
|---|---|---|
| 單元測試 | iPhone 17 模擬器 | **41/41 過**（含多輪廓 JSON 相容、雙元件追蹤、貼紙前置的分類器金標） |
| 靜態稽核 | swift_audit（26 檔）＋verify_logic | 0 重複宣告／0 括號失衡；49/49 金標 |
| 模擬器 ↔ 正式 Cloud Run E2E | dr01 實測 | health／帳密分流／雙軌路由（student＋cloud_escalated）／修邊互動／送標註 enqueued／**去重 duplicate_skipped**／**image_reused 警告**／**同意撤回→重簽 restore 死局回歸**全過；佇列對帳 28→29、臨床 12 不變 |
| 實機（J-iP16PM） | Jack 已測 | 建置安裝／ArUco 面積（13.87/50.01cm²）／修邊 IoU／時間軸圖表縮圖／病患-同意-個案全流程 |
| Android 修改 | 沙箱無 SDK，錨點替換全中＋diff 括號淨差 0 | **待 Android Studio 編譯** |

## 四、推送前驗證清單（Windows 檢查 → 實機 → push）

**iOS（先 `cd iOS && xcodegen generate`，⌘R 到實機）**：
1. 拍照見「📐 LiDAR 深度：擷取中」→ 存檔訊息含「（含 LiDAR 深度）」；送標註後主控台（設定頁新按鈕一鍵登入）看該筆 `depth_source: "lidar_local"`
2. 重拍貼紙誤認的 phantom：提示區出現「已自動排除 N 個…」、疊圖貼紙處無輪廓
3. 冷啟動（隔 15 分鐘）：快門後立即出現「喚醒雲端中…」長文案
4. 筆刷預設變細、組織按鈕實心；長按個案列＝結案/刪除、長按時間軸單筆＝刪除（未送出限定）
5. 主選單「使用說明書」離線開啟
**Android（Studio 建置 v21）**：①空個案刪除／結案 ②phantom 重拍看排除紅字 ③安裝不報 downgrade

## 五、已知缺口（下一輪優先序）

1. Android 時間軸**單筆刪除 UI**（repo 已備，差一顆按鈕）
2. 最近就診（兩端 iOS 缺）、快速量測來源選擇器（sample/phantom 二選一）、RBAC 身分列＋設定頁完整版、相簿匯出、App icon
3. **後端**：`/api/v1/depth` 端點（深度本體上傳，見規劃 §四）；分割前遮罩 marker 區域（貼紙誤認治本，兩端受惠）
4. 深度 P2：重力向量＋畸變表；P3：ARKit 多視角序列
5. 契約測試補：貼紙排除規則、深度 sidecar 往返

## 六、提交建議

PowerShell 中文多行訊息務必走 `COMMIT_MSG.txt` ＋ `git commit -F`（見 .gitignore 檔尾註解；2026-08-05 踩過 parse error）。建議拆三筆：
1. `feat(ios): 對齊 Android v21 完整工作流`（多輪廓鏈／畫布／時間軸／複核／相機／手冊／刪除結案／UI 修正）
2. `feat(woundai3d): LiDAR 深度擷取 MVP 與資料規劃`（DepthCapture／sidecar／depth_source／docs）
3. `feat(android): 同步 v21 — 結案刪除規則與貼紙誤認排除`
`iOS/*.xcodeproj` 已在 .gitignore（XcodeGen 產物）；`Resources/manual.html` 要進版控。
