# iOS MMH／獨立示範環境隔離驗證（2026-10-13）

## 交付狀態

本機整合分支 `codex/mmh-full-alignment-20261013`，基底 `dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00`。本次為尚未提交的 iOS 接線，疊在 W0、W1–W3、原生傷口遮罩及 review-service-profile 四份凍結 patch 之後。未推送、未部署、未更動 IAP／IAM，也未更新實機或商店版本。

使用者的後端工作清單仍按 W0–W3 分開交付；此 iOS 工作是先前已選定的獨立示範環境配套，不擴大 MMH 資料來源限制。W0–W3 覆核參照 [後端完整性報告](mmh_backend_integrity_review_20261013.md)。

## 實作

- 新增不可變 `MedicalRuntime`：每個畫面樹、量測、時間軸、撤回同步與上傳工作都持有自己的環境；不在執行中重新指定全域後端。
- 首頁可選「合成示範環境」，或返回 MMH；設定顯示所在環境。示範使用獨立 WoundAI 帳號，不要求 Google；MMH 保留 Google IAP 加上 WoundAI 驗證。
- 環境各自持有帳密、資料庫、影像、深度索引、待傳佇列、撤回／恢復待辦及行政匯出設定／目錄。MMH 沿用原路徑，沒有搬移既有紀錄。
- `SyntheticReviewTransport` 只接受固定 HTTPS 服務與 `/api/` 路徑，拒绝 redirect、外站、userinfo、port、encoded path。先驗 health 的服務名稱、revision 及 `synthetic-review` 環境，再傳 App 帳密；不取得或附帶 Google token，不自動重送寫入。
- 服務尚未建立；固定網址為預定入口。此傳輸的 health 驗證是環境誤接保護，不是對伺服器本身的獨立密碼學證明。
- 切換時立即關閉原環境 session generation，取消原上傳 worker，等 worker 完成狀態保存後才切換畫面。收到舊回覆不得登入新環境或將舊工作改送新服務。等待期間有進度提示，不能連續切換。
- 撤回／恢復要求在第一次網路等待前保存；環境關閉或取消後保留待辦，不假裝同步成功。

## 本輪驗證與限制

| 檢查 | 結果 |
|---|---|
| 隔離工程 runner | 111/111 個檔案通過 |
| iOS 機構版 XCTest | 127/127 單元測試、3/3 UI 測試通過；xcresult 合计 130/130 |
| 新增環境與傳輸案例 | 12 項，包含原帳號綁定、跨環境 late reply、關閉後新 client、同意待辦、worker 保存／重開、health 先於帳密、目的地及 redirect 拒絕 |
| 測試 target guard 後重跑 | 12/12 通過；機構測試不進入一般醫療版測試 module |
| 一般 WoundAI／WoundLite | 兩個 iOS Simulator target 均建置成功 |
| tools unittest discover | 344 項執行，1 項既有跳過，343 項通過 |
| verify_logic／parity_check | 50/50；未宣告差異 0，既有宣告差異仍存在 |
| UI 人工檢視 | 已檢視合成示範登入截圖；提示帳號隔離、無 Google 按鈕、WoundAI 登入狀態與儲存並登入可見 |

第一輪最終指令誤植 Simulator UUID，Xcode 拒絕目的地；修正指令後全套成功。原始失敗日誌保留，沒有修改產品以繞過測試。

XCTest 在 MMH「為什麼需要兩次登入？」展開時仍有一則 `Invalid frame dimension (negative or non-finite)` runtime warning。該 UI 測試通過，但不能據此宣稱所有版面警告已清除；需後續定位 DisclosureGroup／登入元件版面。未觀察到示範截圖裁切文字或登入按鈕缺失。

本輪為 macOS／iOS Simulator 驗證，並非 Windows 或真機測試；不聲稱新示範 URL 已可登入。新後端／client 保存契約須配對發布，不能直接以本次 client 安裝後接舊後端。

## 本機操作说明（待發布）

1. MMH 首頁選「進入合成示範（不需 Google）」；頁面會顯示獨立環境說明。
2. 到設定輸入專用示範 WoundAI 帳號，按「儲存並登入」。請勿輸入 MMH、Google 或 OAuth secret。
3. 回到 MMH 時，使用其 Google 與 WoundAI 帳號。切換不會複製病例、待傳內容或匯出副本；未完成工作保留在原環境，回原環境後再重試。
4. 尚未部署示範服務與建立示範帳號，因此以上不能作為現有 Build 33 的使用說明。畫面仍顯示 1.0(33) 是本機配置，不代表商店 Build 33 已包含此功能。

## 下一步

Android 尚未接完相同環境切換。IAP 完整資料下載、WoundAI3D 實體標靶配準、Android 持久 outbox、稽核熱路徑及 Lite 服務仍需各自驗收。後端 Windows owner_guard 不變；本機資料以分層 patch 交覆核，不將 Mac 跨平台 owner 檢查誤稱通過。

新示範環境的資源、公開 invoke 權限與帳號建立須先完成具體部署提案及新增授權。本輪沒有執行這些變更。
