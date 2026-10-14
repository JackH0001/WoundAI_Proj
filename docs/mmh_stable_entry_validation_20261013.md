# MMH 穩定入口對齊與驗證 — 2026-10-13

## 本輪變更

基底 dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00，分支 codex/mmh-full-alignment-20261013，接續十份凍結 patch。本輪將 iOS MMH Info.plist、Android MMH validation/internal BuildConfig 與 Google 傳輸白名單、管理服務 session/login/accounts 與網頁主控台連結改為穩定入口：

`https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app`

candidate tag 網址仍可供獨立候選驗收，但不再作為這些本機發布設定的預設入口。Google Web audience、IAP 驗證、服務識別、帳號權限、資料桶及流量均未變更。一般醫療版、WoundLite 與獨立示範入口也未在此輪切換。

新增防止 candidate 回流的檢查：Android 兩種 MMH build、兩端設定與 Android 傳輸 allowlist、管理後端及前端連結需一致。逐一將 6 個傳輸/連結來源與 2 個 Android build 改回 candidate 的測試均會攔截。Android 已驗證的 Google session 也不能把 token/帳密送到 candidate。iOS 設定頁 UI 檢查改驗穩定入口；既有傳輸測試驗證錯誤 host 在取 token 前即被拒絕。新增來源檔已列入 CI paths，管理服務沿用既有整目錄觸發。

## 新鮮雲端唯讀證據

2026-10-13 19:48–19:49 台北時間讀取 woundai-jackh001 / asia-east1 的 MMH 服務：穩定入口及 candidate 均為 woundai-backend-mmhps20261007-lat85775e8，default 100%。IAP enabled=true；匿名 GET /api/health 回 302，並非無驗證取得健康資料。

本次未以真實手機 Google token 完成穩定入口登入／送件，不能把匿名 302 或本機 fixture 當成此項驗收。雲端仍是原映像；新原生雙遮罩契約尚未成對部署。

## 本輪實際驗證

- 工程隔離 runner：111/111 檔通過，31.401 秒；Mac 環境，非 Windows lock 實跑。
- 管理服務 Python：121/121；JavaScript：9/9。
- Android：MMH validation APK、MMH internal Kotlin 建置成功；JVM 106/106，無略過。
- iOS：MMH 模擬器編譯與測試成功；IAP 傳輸 14/14、入口 UI 3/3。
- 直接讀回 iOS 已建置 App 的 Info.plist，核對穩定入口；Android validation/internal 兩份 generated BuildConfig 也一致。
- verify_logic 50/50；parity 未宣告落差 0，已登記差異仍 10；git diff --check 通過。
- 初次 gcloud IAP 欄位投影格式失敗，修正引號後取得 true；未更動服務。初次未升權 simctl 受沙盒限制，隨後已授權模擬器測試成功，並非實機故障。

## 更新與待傳資料限制

這是本機候選來源修正，未提交、推送、部署或更換真機／商店版本；已安裝版本仍依原設定運作。Backend/engineering 仍以 patch 供 Windows 提交，未繞過 owner_guard。

現有 AppSettings 在 MMH 版採固定產品入口，因此更新後新操作會走穩定網址。研究 outbox 工作則固定保存原 origin／帳號／actor／修訂，不自動重寫 candidate 為穩定網址；不以「目前同一 revision」推定所有歷史綁定可遷移。發布前應先在舊版核對／完成待傳工作，或另行實作並驗證有服務、組織及收據證據的受控遷移。未完成此項前，不宣稱可無縫升級所有待傳資料。

下一步仍包括：新 client/backend 成對整合、穩定入口真機登入與同筆去重保存收據驗收、Windows patch 覆核、商店新版與獨立示範環境。此輪沒有完成 TestFlight 外測或 Play 新版发布。

證據：woundai-institution-evidence-20261007/mmh-iap-update-20261011/stable-entry-validation-20261013，含來源差異、patch SHA、逐項測試與唯讀雲端狀態。
