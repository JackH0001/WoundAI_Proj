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

## Windows 107/111 回報後的追加修正

- `lite_service_profile` 以 `posixpath` 驗證 Linux 容器的 runtime 路徑，不依規劃工具執行主機的 `os.path`。拒絕根目錄、UNC、反斜線、NUL、相對路徑及未正規化的 `..`／`.` 路徑。
- 兩個 Lite 打包／部署清單測試改用 `.as_posix()` 比對跨平台 manifest。啟動測試的本機暫存路徑轉為同磁碟的 POSIX 表示，仍在各自隔離暫存目錄內。
- 模擬 Windows 原生 `isabs` 不接受 `/tmp/...` 的測試，以及不合法路徑拒絕案例均已加入。第一輪測試替身誤改共用 `posixpath.isabs`，修正為只替換模組的主機 OS 參照後重跑；初次失敗日誌保留。
- `open_mmh_console` 的三個 POSIX process-group 清理測試在無 `os.killpg` 的 Windows 跳過；其餘政策、參數與服務檢查仍跑。這沒有讓該 POSIX 工具宣稱支援 Windows。
- 稽核 UI 的 Node 測試仍為必要檢查，沒有為了全綠而跳過；Windows 請先確認 `node --version` 能成功。
- 簽章 CI 增加 Windows Server 2022，與 Ubuntu 同跑 Python 3.13 + JDK 17 的一次性測試金鑰；原生 Windows 結果以本次新 head 的 CI 為準。

本次 Mac 重跑：工程111/111檔、tools363項（362通過／1個PowerShell缺環境跳過）、logic50/50、parity無未宣告落差。iOS／Android程式碼未於本次追加修改，上節197／139與117項結果屬前一筆面積修正驗證，並非再次實機執行。

追加證據：`woundai-institution-evidence-20261007/mmh-iap-update-20261011/windows-portability-20261015/`。Windows全套仍需Claude以新的固定SHA重跑；不能把舊版本107/111改寫為已通過。PR維持Draft，完成覆核及CI後才提出固定SHA合併；尚未部署或更新商店。

## 原生 Windows 全套追加驗證（2026-10-15）

獨立私有 CI 固定公開庫 `85c8430`、3D `484eb5e`，使用 Windows Server 2022／Python 3.13、repo Windows dependency lock、Node 20／JDK 17；未變更已部署服務。首次實跑為工程109/111檔，tools363項有1個清理錯誤、3個已聲明POSIX跳過，3D58/58、logic50/50與parity通過。這不是先前Claude的107/111紀錄。

本次補修：
- Lite部署規劃器的artifact_sha256鍵改用POSIX路徑，對齊既有跨平台契約；保留所有雜湊檢查。
- 模擬部署測試啟動各PowerShell前清除繼承的PSModulePath，使5.1與7各自載入系統模組；沒有略過Get-FileHash或複製完整性驗證。
- 醫療session fixture僅追蹤、移除與關閉自己建立的FileHandler，再清除暫存目錄；不關閉其他測試的日誌。
- 獨立CI的Android SDK安裝指定platform-tools、API36、build-tools36，排除action預設已移除的tools套件；App程式碼不變。

Mac專項：Lite部署規劃10/10、醫療session21/21通過。新版Windows全套結果須以後續固定SHA的實跑為準，不把上述首次失敗改寫成通過。證據：repo外release-continuation-20261015/windows-first及GitHub WoundAI3D run38671739368。
