# Android MMH 研究待傳接線與 UI 驗證 — 2026-10-13

## 來源與本轮範圍

基底 dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00、分支 codex/mmh-full-alignment-20261013。接續九份凍結 patch，新增 runtime 工作器、量測／時間軸送件接線、前景與登入恢復、狀態 UI 及測試。更新前份 android_clinical_outbox_core_validation_20261013.md「尚未接線」的狀態；原證據不覆寫。

目前只在本機候選版生效。未提交／推送，未更新使用者手機、Play、TestFlight 或 MMH 後端；未更動 IAP／IAM／帳號。未修改 Backend 或 engineering 所有權範圍。

## 實作行為

- 量測頁及時間軸在使用者確認送出後，先以目前醫師身份核對已存版本，再建立密文待傳工作。入列成功顯示「尚未確認雲端保存」，不以入列代替保存成功。
- `ClinicalResearchController` 隸屬不可改指向的 MedicalRuntime。離開單一畫面不取消工作；環境關閉則取消工作器，原檔案保留。App 前景每 30 秒檢查到期工作，登入成功會喚醒；重試仍受持久退避時間限制。
- App 進背景後不啟動下一輪；已開始的網路操作可能完成。Android 可暫停／終止程序，此版本不是 WorkManager／前景服務持續背景保證；下次 App 開啟或登入恢復才接續。
- 每次送件核對原 origin、設定 user、實際 actor、gt.verify 與 annotation.submit 權限、當前環境／session epoch、紀錄與個案綁定、照護／訓練同意、撤回及恢復待辦。MMH 沒有新增資料來源分級；未歸戶紀錄沿用既有 ReviewTrainingConsent 閘門。
- 權限不足、換帳號或登入未完成會保留待傳；撤回或原紀錄／個案失效則停止該工作。HTTP 200 收據不符會暫停，需明確重試；不悄悄略過驗證。
- 以凍結 row／raster 送出完整原生傷口與組織 PNG，沿用真正 BackendClient 編碼及傳輸邊界，送出前再次取得當下同意值。保存回覆經核心驗證並持久保存，才以 CAS 更新同一版時間軸。
- 待傳面板位於設定、量測送件區及時間軸詳情，區分待傳、處理中、需處理、雲端已存但本機待同步、完整標註保存與舊修訂已存。Android 顯示「本筆無深度」，不宣称 3D 完整保存。
- 收據完成後同步量測／時間軸按鈕狀態；只有相同已存修訂會標記成功。使用者在其間修改數值或遮罩，不會被舊回覆標成新修訂已上傳。
- 分開檔案互斥鎖與網路工作鎖。慢速上傳不持有待傳檔案鎖，另一筆入列與狀態讀取可繼續；同工作仍序列處理，避免並行送兩次。

## 本輪測試證據

- 最終建置：MMH validation APK／test APK、MMH internal 與 Release Kotlin 成功；105/105 JVM。
- 最終 Pixel_7 模擬器：49/49，包括 controller 9、待傳核心 19、保存／收據同步 13、IAP 傳輸邊界 2、設定 3、送件按鈕 2、待傳狀態 UI 1。
- controller 使用實際 BackendClient 編碼與 HTTP interceptor 合成回覆：驗證兩张原生遮罩、實際 Room 確認、runtime 重建後恢復、前景工作器無手動 drain 完成、撤回／換帳號／缺權限不送、503 重試同遮罩、错误收據暫停及明確重試、旧修訂不能標記新版本。
- 新增慢速傳輸測試以暫停的 upload 回呼驗證另一個實例可在 1 秒測試期限內讀狀態及入列；不是實機延遲承諾。
- 本輪工程隔離 runner 111/111 檔通過（30.445 秒）；此後變更為 UI 文案、收據同步測試與送出前再次核對 permission。最終 APK 上述 49 項涵蓋新增接線；verify_logic 50/50、parity 未宣告落差 0（已登記差異 10）、diff check 通過。
- 初次接線因送件子 composable 缺 runtime 變數而編譯失敗，已修正；失敗與後續成功 log 都保留。

不把模擬器 HTTP fixture 當雲端驗收；不把 macOS 工程測試當 Windows 鎖版驗收。Play 內測仍非此來源版本。

## 尚待發布前完成

1. 第一筆入列仍需成功取得目前 WoundAI 身份驗證；完全離線且無法取得身份時不入列，原量測保留。已入列工作的斷線重試已接線。若要完全離線確認／入列，需另定可驗證的醫師確認身份保存機制，不能猜 actor。
2. 歷史 doctorVerified 布林未記錄當時醫師；本次按鍵明示「由目前登入醫師確認此已存版本」，固定的是這次明確送件 actor，未宣稱還原歷史 actor。
3. 真實 MMH／IAP 逾時與重登入、OS 強制終止與重新啟動、平板／Play 安裝版全流程、容量壓力與完成／撤回工作清理仍待驗收。工作暫留密文，不自動刪除。
4. 穩定入口／候選分離、新原生遮罩後端成對部署、Android 高清採集關聯、完整 WoundAI3D 匯出、Apple 獨立示範與外測等原目標仍未完成。

證據：woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-outbox-integration-validation-20261013，包含 patch、相依 SHA、建置與逐項測試 log。
