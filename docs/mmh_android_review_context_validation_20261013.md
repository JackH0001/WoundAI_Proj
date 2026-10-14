# Android MMH／示範環境隔離本機驗證 — 2026-10-13

## 範圍與來源

分支 `codex/mmh-full-alignment-20261013`，基底 `dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00`。本次是未提交的 Android 層修正，接續既有 W0、W1–W3、原生傷口遮罩、示範後端模式及 iOS context 五份固定 patch；沒有推送、部署、修改雲端權限或更新實機／商店。

使用者提供的 `codex_worklist_backend_integrity_20261013.md` 是覆核參考。W0–W3 的獨立交付與 14 項變異證據見 [完整性覆核](mmh_backend_integrity_review_20261013.md)。MMH 不加來源分級；格式、幾何、同意與權限仍檢查。W3b 與 IAP 完整取件另案待做。

## 實作

- MedicalRuntime 將帳密、資料庫、影像、同意待辦、登入工作階段與 backend client 綁在固定環境。切換環境、換帳密或關閉 context 後，舊請求回覆失效。
- MMH 保留 Google IAP；獨立示範入口只使用自己的 WoundAI 帳號。傳送帳密前先驗精確服務、環境及 revision；拒絕重新導向，不攜帶 Google 門禁 header／cookie。
- 量測、時間軸、修邊、個案與設定頁使用同一 context。同意待辦先落盤再等待網路；晚到的回覆不能清掉另一環境待辦。
- 示範與 MMH 的圖庫輸出分相簿。這仍是 MediaStore 圖庫，尚未完成使用者要求的「私密資料夾＋可選匯出」；不可視為匯出資安需求已完成。

## 驗證結果

| 檢查 | 結果 |
|---|---|
| MMH validation JVM 單元測試 | 105/105；無跳過 |
| Pixel_7 模擬器定向測試 | 15/15；帳密／資料範圍隔離、設定介面、晚到回覆及門禁 |
| validation APK＋androidTest APK | 建置成功 |
| MMH internal、Release Kotlin | 編譯成功；非商店 AAB 驗收 |
| 工程 runner | 111/111 檔通過 |
| tools | 執行 344 項，343 通過、1 跳過 |
| verify_logic | 50 項通過 |
| parity_check | 未宣告落差 0；已登記落差仍存在 |

模擬器測試全部使用本機合成 fixture，沒有用 Google 真實登入或雲端上傳。沒有宣稱實機、完整 UI 視覺驗收或商店版已對齊。

初次門禁測試因缺少成對遮罩而在 HTTP 前返回，兩項預期 IOException 的測試失敗。補有效遮罩後，另修正 fixture 的 Float／autoBaseline 參數；最終確實驗證門禁阻止 socket 開啟。建置一度被 app/build 內重複的 KSP `*_Impl 2.java` 生成檔阻擋，執行 Gradle :app:clean 重新建置成功；未刪原始碼或 Git 資料。初始失敗 log 保留。

## 待完成與交接

Android 持久上傳 outbox、私密可選匯出、IAP 完整資料取件、穩定入口取代 candidate、實機／商店驗收及新示範雲端部署仍待完成。新版遮罩收據需手機與後端成對部署，不能只更新其中一端。

此批是 Android patch，不新增 Backend／engineering 提交。既有後端 patch 仍交 Windows 覆核。清單列 NumPy 2.4.6，但目前 requirements-windows-test.lock.txt 是 2.5.2；本機既有 runner 使用 2.4.6，不宣稱完成 Windows 鎖版驗證。

證據目錄：`woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-review-context-validation-20261013/`，含逐項測試、初始失敗、patch 重建驗證及 SHA-256。沒有新增 commit SHA；請用 patch manifest 的基底與雜湊覆核。
