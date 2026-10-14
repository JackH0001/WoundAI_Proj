# MMH 手機來源整合與發布檢查（2026-10-11）

## 來源與範圍

以公開庫 main `b7f9dc2b28bc4a270c2df531838df18c69cfff07` 為基準，從本機整合 `3ad03ce` 保留 Android／iOS 的 MMH 登入、標註收據、保存版本、等待提示、預覽、圖示及操作文件，整理到獨立分支 `codex/mmh-mobile-alignment-20261011`。不含 AccessManager 或帳號 CAS 的後端實作；其獨立審查來源仍為 `3e96f8aba5b00d187fec073f792a67879c7ad882`。

本分支包含既有兩平台醫療圖示及使用者後續指定的 Lite 圖示；不代表 Lite 功能已完成上架驗收。沒有改動使用者手機資料或雲端服務。

## 本輪新修正

1. 兩平台共用手冊的事後補送段落仍引用舊按鈕，且容易誤解成必須每次修邊。改為先核對保存狀態；只有缺醫師確認或需要修改時才進修邊。明確說明縮放、移動、取消不算完成確認，正確遮罩可不修改直接確認。
2. 手冊與契約工具改用實際 Android 標題「修邊標註（檢視／修正遮罩）」及「確認送出已存檔的標註」，增加兩平台手冊完全一致檢查。
3. 網址檢查工具原本未識別 mmhInternal，誤判為重複 mmhValidation。新增獨立配置及操作文件對照；測試錯接舊後台、未知／重複配置仍會拒絕。
4. 更新 PARITY 的預覽／疊圖與私密匯出差異，保留 Android 無完整深度採集、私密行政副本未對齊的限制。新增檢查接入既有 CI，但本分支尚未推送，沒有 GitHub CI 結果。

## 本輪實際驗證

| 項目 | 結果 |
|---|---|
| Android MMH JVM 單元測試 | 64/64，0 skipped |
| Android mmhInternal AAB 建置 | BUILD SUCCESSFUL，JDK 17，offline，22 秒 |
| AAB jarsigner | 明確 unsigned，僅供建置檢查，不可提交商店 |
| 手冊／原始碼與 iOS／Android 手冊一致性 | 107/107 |
| 後台網址配置及錯誤設定拒絕 | 11/11 |
| 共用靜態邏輯 | 50/50 |
| 標註契約比對邊界 | 4/4 |
| 既有 IAP 計畫／手機驗證契約 | 16/16 與 9/9；純離線測試，未套用雲端 |
| CI 靜態檢查 | 12/12 |
| parity_check | exit 0；保留已登記平台差異，不代表功能等價 |
| git diff --check | 通過 |

AAB：`/private/tmp/mmh-mobile-review-build/app/outputs/bundle/mmhInternal/app-mmhInternal.aab`。
SHA-256：`1b59c732c8a3ee7a21f680eef24debb21558438045c5e8b1cdee7dc7146c563e`。

第一個 Gradle 嘗試因隔離環境無法寫既有 wrapper 鎖檔停止；取得本機快取存取許可後成功。未讀取 keystore 密碼或以 debug 簽章冒充發布簽章。

## 尚未完成

- 本輪沒有重跑 iOS XCTest、手機／平板安裝、RGB-D 實機配準或雲端送件。先前測試詳見各階段文件，不能計入本輪實測數字。
- iOS Build 33 已啟用 TestFlight 內測；外測審查登入路徑仍待指定與驗收。本輪手冊修正不在已上傳的 Build 33 中。
- Android 仍待既有上傳金鑰的安全簽署設定、Play 公開 App Signing SHA-1、對應 release OAuth 與商店分發登入驗收。Play 畫面尚未有第一個 AAB 的上傳金鑰憑證。
- 本機 review 分支未推送、未合併或部署。MMH 管理服務亦未部署。

參照：[原始測試日誌、JUnit XML 與產物摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/mobile-source-review/summary.json)、[商店進度](mmh_store_release_checkpoint_20261011.md)、[平台差異](PARITY.md)。
