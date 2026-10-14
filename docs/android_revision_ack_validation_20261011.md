# Android 時間軸修訂保存回覆驗證

2026-10-11；來源基準 f48e89d。本輪限定 MMH 合成驗證，不改雲端 IAP、IAM、流量或資料。

## 問題及修正

時間軸原先沿用導航傳入的紀錄快照；標註成功後只按紀錄 ID 設為已送出。若其他操作在請求等待期間更新該筆修訂，舊回覆可能把新修訂標成已保存。

- 進入紀錄頁先從資料庫重讀最新資料；送出前再核對送件快照，已保存的相同修訂直接顯示無需補送。
- 送件使用固定的紀錄、輪廓、組織柵格及溯源快照。既有 HTTP 保存收據驗證通過後，才以 Room transaction 比對完整紀錄並標記已保存。影像、遮罩、面積、組織、溯源或醫師確認任何欄位不符，都不標記新修訂。
- 相同保存回覆可重複確認，不新增量測資料；紀錄已刪除時不復活。
- 編輯存檔同樣比較編輯起點，避免舊編輯畫面覆寫較新修訂或已確認保存狀態。交易失敗時清除本次未引用的新柵格檔，保留原檔。
- 沒有變更資料庫 schema；保留既有升級流程與本機紀錄。

## 本輪測試

| 項目 | 結果 |
| --- | --- |
| MMH validation APK、測試 APK 建置 | 通過 |
| JVM 單元測試 | 64/64 |
| 新增 Room 交易測試 | 8/8 |
| 既有資料庫遷移 | 4/4 |
| 修邊控制項 | 4/4 |
| 柵格溯源 | 4/4 |
| 模擬器測試合計 | 20/20 |

Room 測試使用獨立記憶體資料庫，涵蓋重複收據、面積不變但遮罩改變、組織單獨改變、影像／溯源／滲液／醫師確認改變、刪除後回覆、舊編輯覆寫收據、新修訂清除保存狀態與保留採集時間、跨紀錄或並行修訂拒絕。

首輪使用 Android Studio 內附 Java 時遇到 Gradle test report `Type T not present` 設定錯誤；改用專案指定 JDK 17 後建置及測試通過，未更動建置契約規避測試。

## 實機與剩餘範圍

- 使用者已回報 iOS MMH 33「已登入」。這不等於返回設定恢復角色、或前筆待傳時連續拍攝的實機驗收通過。
- 本輪 Android APK 只更新模擬器；未覆蓋平板 r4，未提交 Play／TestFlight。
- 上述第一階段只改善時間軸補送；後續初次量測修正與驗證見下節。
- 舊資料缺少原始模型／品質溯源或組織基準的安全恢復仍未完成；不得猜填並放行。
- 尚未以新版本重跑手機到雲端端對端測試，不把本機交易測試當成雲端保存或 RGB-D 配準證據。

## 參照

- [完整測試摘要與原始日誌](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-receipt-cas/summary.json)
- [Room 保存交易](../Android/app/src/main/java/com/woundmeasurement/app/data/dao/MeasurementDao.kt)
- [時間軸送件與編輯](../Android/app/src/main/java/com/woundmeasurement/app/pipeline/MeasurementReviewScreen.kt)
- [交易驗證案例](../Android/app/src/androidTest/java/com/woundmeasurement/app/data/database/AnnotationAcknowledgementTest.kt)
- [iOS 33 與三筆雲端保存核對](ios_mmh_login_upload_validation_20261011.md)

## r5：初次量測送件與保存對齊

後續基準 8b65660。r5 使用 `com.woundai.app.mmhps20261007.validation`、versionCode 26、versionName `1.0-mmh-validation-r5`，仍為合成驗證 APK。

- 初次標註送出須先保存時間軸；畫面值與已存版本不符時要求重新保存，不傳另一份內容。比對包含影像 ID、座標、輪廓、面積、滲液、組織比例、尺度、來源、模型／品質溯源與醫師確認；再核對無損柵格 metadata 與實際 bytes。
- 後端收據確認成功才以相同 Room 交易標記此版本已保存。相同版本已保存就不發送網路請求，UI 顯示「此修訂標註已保存」並停用送出。
- 重存相同內容保留收據；影像或柵格 bytes、組織或其他綁定變更即清除舊收據狀態。重存不改寫原紀錄時間。這不等於已修正首次保存時間與快門採集時間的差異。
- 存檔／送件中停用修邊、滲液修改、重拍、選個案、歷史及返回操作；VM 邊界也檢查忙碌狀態。失敗會解除鎖定。
- 雲端已保存但本機寫入失敗時，明確區分兩者，不要求重新拍攝。

驗證：64 項 JVM 通過；MMH 模擬器 29 項通過（17 項保存／交易、4 遷移、4 修邊控制、4 柵格溯源）；一般 debug 的實際 loopback HTTP 收據測試 6/6 通過。初次把 loopback 測試放進 MMH variant 時，6 項被 Google 入口拒絕，未走到 HTTP 測試；改用其適用的一般 debug 配置重跑，未放寬 MMH 的 IAP 要求。

本輪未完成 r5 手機到雲端驗收、Play 提交或 TestFlight 發布。不能以 mock upload callback 的資料庫交易測試宣稱真機後端全部通過。

證據：[r5 測試摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-initial-ack-r5/summary.json)。
