# Claude 覆核後補修：面積一致性與 Windows 驗證（2026-10-15）

基底：公開庫 PR #28 `393ac1d`；母庫 PR #12 `b46b41a`。使用者已選定：傷口邊界與校正尺度未變，就保留原面積。

## 面積規則與資料邊界

- 兩平台以本次進入編輯器時的已確認遮罩為基準，比對所有傷口像素及校正尺度；不再用相對 AI 原始遮罩的 IoU >= 0.9999 判斷本次是否修改。原 AI IoU 繼續保留原用途。
- 查看、縮放、平移、只改組織、畫筆後完全復原不改面積；空白 ROI 擴張不視為傷口改動。即使只改一個傷口像素，或校正尺度改變，也重新計算。
- iOS 移除 applyEdited 與 saveToTimeline 的柵格面積強制覆蓋。編輯器、結果頁、時間軸、送件與行政 overlay 使用同一筆已確認面積。Android 保留原本已確認值的保存路徑，並套用同一編輯器規則與續編校正更新。
- 原始傷口／組織遮罩、cm²/像素、原始 RGB-D 與修訂關聯仍保存；沒有把係數改成強行符合保留面積。多邊形與柵格轉換可能有取樣差異，研究端可從原始遮罩與係數重算；臨床顯示不因無操作重新取樣而漂移。未變更 LiDAR 表面積估算或後端稽核。

## Windows 補修

- 簽章工具於 Windows 只從指定 JDK/bin 解析 java.exe、keytool.exe、jarsigner.exe；Mac/Linux 保持無副檔名。不退回不明 PATH 工具。預設尊重 JAVA_HOME，簽章測試也使用 MMH_TEST_JAVA_HOME 或 JAVA_HOME。
- Windows 不再用 POSIX 0600 數值驗證 ACL；簽章內容／所有項目覆蓋／錯誤憑證／遭追加內容等實際驗證保留。測試只建立一次性假金鑰，未讀取使用者 keystore。
- Windows 3D 階段改為真正呼叫 pytest，保存日誌、JUnit XML、summary.json；拒絕零項、全部跳過、失敗、缺測試檔及舊 XML。未指定 SkipWoundAI3D 時缺 repo 也會失敗，不再默默略過。
- WoundAI3D 原工作目錄及 PR #3 未修改；修正的是共用 repo 的 Windows 驗證入口。

## 實跑結果

- 工程測試：111/111 個檔案通過。
- tools：363 項，362 通過、1 跳過（PowerShell 7 未安裝）。簽章專項16/16，含真實 JDK 一次性金鑰簽署及篡改拒絕。
- pytest runner 故障案例：通過、失敗、零項、全跳過、缺檔，5種輸入均符合預期；每次預置舊 XML 確認不被沿用。
- 真正 3D portable suite：58/58，0跳過、0失敗。
- iOS 面積／手勢／存檔／輸出專項：25/25。
- 完整醫療 iOS：197項，196通過、1系統資料夾授權跳過、0失敗。
- MMH iOS：139項，138通過、1系統資料夾授權跳過、0失敗（與醫療方案有共用案例，不能直接相加當不同測試）。
- Android MMH JVM：117/117；validation APK與internal Kotlin建置成功。舊build目錄曾出現重複「 2.dex」而失敗，改獨立 /private/tmp 建置目錄後成功；沒有刪除使用者資料。
- 第一輪新增iOS存檔fixture未設定phantom來源，被機構同意檢查正確擋下；修正fixture對齊UI後通過，未放寬同意檢查。
- 變更來源機密掃描0命中；最新CI另以PR新head為準。
- logic 50/50、parity 無未宣告落差。

以上為 Mac 實跑。Windows .exe 分支以檔案 fixture 測試；原生 Windows PowerShell 5.1、JDK 簽章與完整入口仍待 Windows Claude 跑，不能以 Mac 結果替代。

## Windows Claude 追加覆核

從新的覆核分支 HEAD 建立乾淨 checkout，勿移動正在跑舊 SHA 的工作目錄。先記錄完整 SHA，設定 JDK 17 的 JAVA_HOME，再用 repo venv 執行：

```powershell
& $ReviewPython -B -m unittest discover -s tools -p 'test_sign_mmh_aab.py'
if ($LASTEXITCODE -ne 0) { throw 'Signing tests failed' }
& $ReviewPython -B -m unittest discover -s tools -p 'test_3d_portable_runner.py'
if ($LASTEXITCODE -ne 0) { throw 'Runner regression failed' }
& $ReviewPython -B tools/windows/run_3d_portable_tests.py --test (Join-Path $WoundAI3DRoot 'scripts/phantom_validation/test_phantom_validation_analyzer.py') --out (Join-Path $ReviewOut '3d-portable')
if ($LASTEXITCODE -ne 0) { throw '3D portable tests failed' }
```

尤其檢查：歷史 AI 遮罩不同但本次無修改、只改組織、單像素差異、畫筆復原、校正倍率改變、ROI 空白邊距、相同已確認面積走完時間軸／送件／overlay。沒有合併 main、部署或更新商店。

證據：repo 外 `woundai-institution-evidence-20261007/mmh-iap-update-20261011/review-fixes-20261015/`。
