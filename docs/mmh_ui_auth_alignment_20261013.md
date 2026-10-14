# MMH 醫療版兩平台 UI、登入與延遲核對（2026-10-13）

本輪為本機修正與驗證；未推送、未部署、未更新實機或商店。基底 `b984c8729e5443f03874382b6be791e38fcfa130`，隔離分支 `codex/mmh-review-environment-20261013`。先前選定的獨立合成審查環境方向保留，本輪未建立該環境。

## 實際版本與差異

Mac 本輪讀到 iPhone 同時安裝一般 WoundAI 1.0(27)、WoundAI MMHPS 1.0(33)、Lite 1.0(33)。Android USB 清單為空，不能宣稱其目前安裝版本；先前商店26與本機驗證r4不是同一產物。以目前兩平台共同分支核對後，缺失確實也存在於原始碼，並非單憑版本較舊就能解釋。

| 模組 | 本輪核對與處置 | 驗證界線 |
|---|---|---|
| 結果影像 | iOS 原本只有輪廓／校正框，新增組織圖層、來源與圖例，保留双指縮放 | 合成像素與座標測試通過，未作真實傷口精度聲明 |
| 時間軸預覽 | 讀取已保存 raster，畫布不符拒絕疊加，不把歷史缺漏猜成標註 | 不重新分類覆寫醫師修改 |
| 初次組織預覽 | iOS 使用共用 TissueSeg 背景計算並明示自動預覽；與保存遮罩分開 | 與後端數字完全相同尚無保證，需醫師檢視 |
| 修邊工具列 | Android 移除 emoji／文字箭頭，統一 Material 圖示與可及性名稱；標題改修邊標註 | iOS 使用 SF Symbols，對齊功能與語意，不強求平台圖示逐像素相同 |
| 原圖查看 | Android 點按切換原圖／回修邊，原圖模式不畫筆、不復原／重做，保留列高 | 模擬器切換後仍回傳同一 raster，面積與修改像素數不變 |
| 輸出圖 | iOS 匯出配色與編輯／預覽共用 EditPalette | 未改原始影像、遮罩或量測內容 |
| Google 登入 | iOS 官方 SDK restorePreviousSignIn；Android 官方 Credential Manager 授權帳號自動選擇 | 仍須成功通過 IAP health；尚未真機驗收 SDK 恢復 |
| WoundAI 帳號 | 同 Google subject 保留加密帳密，變更身分／明確登出清除；設定重入仍驗證權限 | 首次升級尚無 subject 綁定時需重新登入一次 |
| 登出與失敗 | 不把暫時網路失敗當成主動登出；明確登出後停止自動恢復 | 授權撤銷、多帳號選擇、SDK 恢復失敗仍可要求互動 |
| 保存與重送 | 沿用修訂、保存收據、去重及深度綁定；本輪未改其契約 | 相關既有回歸通過，未做新雲端樣本上傳 |
| 雙平台手冊 | 兩份 manual.html 位元相同，補登入恢復／預覽來源／原圖模式 | 未改 App Store 審查備註 |
| Lite | 共用 WoundImagePreview 新增可選圖層，預設 nil | 未加入醫療組織分類 UI、未宣稱 Lite 發布驗收完成 |

## 效能：已改與尚待量測

- 移除 iOS 每次 Google 登入先清除 SDK 記憶與 WoundAI 帳密的行為，改由官方機制恢復原身分，不能以保存舊 token 取代新授權驗證。
- 初次組織預覽移至背景並限制尺寸；切換組織／輪廓可見性、縮放不重跑分類，已保存 raster 不重新分類。
- MMH Google 恢復已驗證 health，不再於 App 啟動重送一次相同預熱請求。
- 基底包含尚未發布的 MedicalSessionPool：十次合成量測共用一次密碼登入，每次仍核對帳號版本與權限；不是十次免驗證。現有相關回歸本輪重跑通過。
- iOS IAP transport 仍把量測與研究上傳排入同一序列。線上既有 min=0、concurrency=1、max=1 的限制會造成冷啟動與排隊；本輪沒有放寬，也沒有省略稽核。不能把所有等待都歸因於 Google 或稽核。
- 下階段應將「排隊／Google 恢復／工作階段驗證／保存授權／影像傳送與推論／稽核／原始深度保存」分開計時。用同一合成標靶分冷、暖啟動，各至少10次，另做前筆深度上傳中再量測，報 median、p95、失败率與保存收據完整率。
- 只有證據確認稽核讀取或排隊是瓶頸，才評估受限平行讀取、前景量測優先、工作佇列與服務 concurrency。寫入順序、CAS、撤回、不可覆寫修訂與收據不可為提速而略過。min=1 會增加固定成本，需另提出具體估算。

本輪沒有真機／雲端端到端延遲樣本，因此不宣稱已從一分鐘縮短到特定秒數。

## 實測結果

- iOS MMH：85/85 通過，包含新增6項遮罩座標／透明／保存分類／縮放與2項登入恢復／明確登出測試。
- Android：97/97 單元測試通過；MMH validation APK 與 internal Kotlin 編譯通過。
- Android Pixel_7 模擬器：8/8 instrumentation 通過（RasterProvenanceTest、ReviewControlsTest），包含原圖切換與放大後不修改 raster、位置不跳動、輪廓預覽控制。
- 一般 iOS WoundMeasurementApp build 成功。
- 手冊檔案一致、git diff --check 通過。
- 初輪 Android 命名參照及 instrumentation build-type 選項已修正後重跑；初輪 iOS 像素測試多做一次讀回翻轉，修正測試方向後完整通過。不隱藏這些初輪失敗。

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/mobile-parity-20261013/`（三份建置／測試 log 與 summary.json）。

## 發布與實機仍待

此分支的基底 session pool 依賴 `GET /api/v1/auth/session`。後端修正仍在 `eade0de80ac4f9af5d4ce091213fb9dc47fe5c76` 本機分支，線上 `85775e8` 沒有此端點；不能直接把這批手機版裝到正式測試手機後假定相容。需完成成對來源覆核／公開提交／CI、後端候選驗收，再建置帶新版本號的兩平台 App。

真機驗收需涵蓋：首次登入、重開 App、返回設定、離線後恢復、授權到期、主動登出、換 Google 帳號、停用 WoundAI 帳號，以及連續合成拍攝／研究上傳。Android 原始深度仍不可視為等同 iOS LiDAR；不得捏造不存在的深度資料。

## 官方 SDK 參照

- Google iOS：https://developers.google.com/identity/sign-in/ios/sign-in
- GIDSignIn restorePreviousSignIn：https://developers.google.com/identity/sign-in/ios/reference/Classes/GIDSignIn
- Android Credential Manager authorized account / auto select：https://developer.android.com/identity/sign-in/credential-manager-siwg-implementation
