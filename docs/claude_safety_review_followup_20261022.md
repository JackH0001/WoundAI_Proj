# Claude 協作覆核：撤回、重新加入與舊修訂補送（2026-10-22）

本輪接續 PR #35（`codex/lite-reconsent-20261021`），修改前基準為
`0c0406bbf40922c547faaa2be00f90ac87708250`。Lite 候選組建仍為 38；這不是已發布或已完成實機驗收的聲明。
使用者要求後端僅接受最新驗證穩定版本，取消 35–38 同時允許的規劃。

## 問題對照與修正範圍

| 覆核編號 | 本輪結果 | 程式與驗證重點 |
|---|---|---|
| H1 醫療版舊恢復同意佇列 | 已修正本機排程與舊回覆競態，兩平台一致 | 同一 code 僅保留最新方向與隨機世代 token；新撤回取代未送出的舊恢復；驗證登入後及回覆後再次核對 token；重試不建立新意向。舊雙佇列同時存在時採撤回，原舊欄位保留。 |
| H2 Android 舊修訂晚補送 | 已修正本機佇列 | 排序前先比較已保存完整快照，舊修訂改為停止補送；不可覆蓋已確認的新修訂。原 payload、遮罩雜湊及已有回執留存。停止舊修訂不解除伺服器容量等待。 |
| M1 無上限 Retry-After | 已修正兩平台通用等待與撤回等待 | 一般容量等待最長 3600 秒；撤回／恢復同意查核最長 300 秒。超長舊期限載入時限縮並保存一次，不在每次啟動往後滑動；等待到期仍需取得真實回執。 |
| M2 Lite 額度耗盡 | 已修正 | 分割 429 交還 quota 解析器，保留服務回傳次數與說明；沒有再上傳影像。503 與修訂 429 仍維持容量等待，不繞過限流。 |
| M5 / N1 歷史同意版本鎖死 | 已修正已完成歷史紀錄讀取 | 驗證紀錄自身的版本格式、前後一致性、owner、epoch、雜湊與回執；不再要求等於今日同意文字版本。舊紀錄可載入及撤回，不等於取得新版本上傳同意。後端重播已完成續用回執保留原版本。 |
| M6 裝置登記修復 | 保留前一輪修正並回歸 | 不重設既有身分；登記修復仍需有效的 App Attest 與嚴格回執。 |
| M3 醫療病況欄位版本相容 | **本輪未修** | `MedicalWoundProfile` 仍綁當前 catalog；欄位元件尚未接入一般臨床流程。啟用前需版本化 catalog／遷移測試。 |
| M4 同意書部位／病因說明 | **本輪未修** | 啟用上述欄位前，需同步資料用途說明與同意版本；不能用本輪測試聲稱已完成。 |
| 其他低嚴重度項目 | **未全部處理** | 例如 PUSH 顯示、所有 401 文案不在本輪完成範圍。 |

### 必須保留的邊界

H1/H2 的驗證涵蓋尚在本機排程、等待登入、重試及舊回覆造成的本機覆蓋。
它不證明已送入網路且伺服器尚在處理的舊醫療請求能被取消，也不建立跨裝置的醫療後端 CAS 世代排序。
醫療 `api_flywheel.py` 仍使用現有撤回／恢復協定；真實病患研究放行前，須另完成伺服器端延遲請求排序與跨裝置驗收。
Lite 既有新 epoch 隔離與醫療版協定不同，不應混用其安全結論。

M5/N1 這次覆蓋「已完成」重新加入與取消未送出撤回的歷史稽核。
跨同意版本時仍在等待續用回執的舊意向，不在此次歷史完成態測試的保證範圍內。
未知／破損稽核仍拒絕恢復上傳；不得清除稽核來強行解鎖。

## 本機 Release／模擬器驗證

所有網路測試使用合成 fixture；沒有送出病人資料、修改原始影像或刪除使用者紀錄。

| 測試 | 通過 | 跳過 | 失敗 |
|---|---:|---:|---:|
| WoundAILite iOS Release XCTest | 234 | 1 | 0 |
| 醫療版 iOS Release XCTest | 258 | 1 | 0 |
| MMH iOS Release XCTest | 162 | 1 | 0 |
| Android Debug JVM | 141 | 2 | 0 |
| Android MMH validation JVM | 143 | 0 | 0 |
| Android MMH validation 模擬器：5 個相關測試類別 | 57 | 0 | 0 |
| Lite 後端 pytest | 452 | 0 | 0 |
| 封裝、平台契約與資料格式工具 | 73 | 0 | 0 |

後端另有 463 個 subtest、契約工具另有 159 個 subtest 通過，不加算成獨立測試數。
Lite 的 1 項跳過為實機檔案保護；兩種醫療 iOS 的 1 項各為系統資料夾授權驗收。
Debug JVM 的 2 項只適用 MMH validation，已在該建置執行通過。
Android 模擬器 57 項不是整個專案全部 instrumentation tests。

第一次 Android 回歸發現舊工作在排序前未退役，修正後 57 項全部通過；
另一項測試以權限回呼次數模擬撤回，改為實際送出後切換權限，以驗證真正的網路階段。
初次 XCTest 的檔案附加屬性簽署失敗、測試舊等待值失敗，以及 sandbox loopback 限制均保留本機紀錄；上表使用修正後完整重跑的結果。

## 覆核入口

- [iOS 同意佇列](../iOS/WoundMeasurementApp/Core/MedicalAccountPreferences.swift)、[排程](../iOS/WoundMeasurementApp/Core/ConsentSync.swift)。
- [Android 同意佇列](../Android/app/src/main/java/com/woundmeasurement/app/data/store/MedicalAccountPreferences.kt)、[排程](../Android/app/src/main/java/com/woundmeasurement/app/pipeline/ConsentWithdrawal.kt)。
- [Android 修訂控制器](../Android/app/src/main/java/com/woundmeasurement/app/pipeline/ClinicalResearchController.kt)、[加密佇列](../Android/app/src/main/java/com/woundmeasurement/app/pipeline/ClinicalAnnotationOutbox.kt)。
- [Lite 歷史撤回／續用](../iOS/WoundLite/LiteWithdrawal.swift)、[回執驗證](../iOS/WoundLite/LiteReconsent.swift)、[後端續用](../Backend/Flask/lite_reconsent.py)。
- [Lite quota 路徑](../iOS/WoundMeasurementApp/Core/BackendClient.swift)。

優先重跑的反例：

1. `BackendLoginAndCaptureTests.testWithdrawalDuringRestoreLoginPreventsStaleRestoreFromSendingOrRequeueing`；Android `MedicalRuntimeTest.withdrawalDuringRestoreLoginPreventsStaleRestoreSend`。
2. `ClinicalResearchControllerTest.delayedOldRetryCannotReplaceAcknowledgedNewRevision`、`pausedOldRevisionDoesNotBlockNewExplicitlyConfirmedRevision`、`retiringOldRevisionDoesNotBypassServiceCapacityDeadline`。
3. `LiteReconsentTests.testHistoricalConsentRemainsReadableAndWithdrawableAfterTextUpdate` 與 `testHistoricalReceiptMustStillMatchItsOwnConsentVersion`。
4. `LiteSignedBusinessTests.testQuotaExhaustionReturnsServerCountsAndMessageWithoutRetryingImage` 與 `testQuotaBypassDoesNotSuppressBusyServiceOrRevisionWait`。
5. `engineering/phase2/test_lite_reconsent.py` 的舊回執重播；`test_lite_attest_assertion.py::AssertionTests::test_latest_only_policy_accepts_upgraded_assertion_on_same_key`。

完整測試來源在公開 PR；原始資料、裝置資訊、完整測試日誌仍留本機，未放進公開庫。
Mac 證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-area-upload-acceptance-20261021/review-safety-20261022/`。

## 重現與發布條件

```sh
python -m pytest engineering/phase2/test_lite*.py -q
cd iOS
xcodegen generate --spec project.institution.yml
# 對 WoundLite、WoundMeasurementApp、WoundAIInstitution 依 .github/workflows/ios.yml 執行 Release XCTest。
```

Android 使用 Gradle wrapper、JDK 17 或相容 JBR 21，兩種單元測試與 `connectedMmhValidationAndroidTest -PvalidationTestBuildType=mmhValidation`。
本輪模擬器篩選類別為 `MedicalAccountPreferencesTest`、`MedicalRuntimeTest`、`ConsentTransportTest`、`ClinicalResearchControllerTest`、`ClinicalAnnotationOutboxTest`。

公開 PR #35 仍是 draft、疊在 PR #34 之上。最終 commit 的 CI 狀態以 GitHub 該 head 為準，不沿用舊 head 的成功結果。
後端最新版單一版本測試證明：同一已登記金鑰升級後可用 38 簽署請求；35、36、37 的簽署請求會被拒絕。
這是合成政策測試，不是已部署政策。部署前須固定本輪來源、完成新映像與 TestFlight 實機驗收；不能使用舊 `0c0406b` 的封存 IPA 或舊多版本部署方案。
後端歷史稽核可讀性與 App build allowlist 是兩回事，縮減 App build 不得刪除歷史同意紀錄。

本輪未執行 Cloud Build、未改 live allowlist、未更新 TestFlight，未公開或外測發布。
下一實機階段應由使用者明確重新同意，驗證未來新拍一筆、同筆修訂、無改動不重送、撤回；舊紀錄不得自動復活。
