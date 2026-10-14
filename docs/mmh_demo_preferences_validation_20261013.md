# MMH 與合成示範的帳密及同意待辦隔離驗證

2026-10-13 完成兩平台帳密與同意待辦的固定儲存範圍，保留既有 MMH 的 `woundai_settings` 與欄位名稱。新增的合成示範範圍使用 `woundai_settings.synthetic-review-v1`。現有 AppSettings 仍固定使用 MMH 範圍；沒有全域可變的「目前環境」設定，也沒有啟用示範入口。

本次在 `3e1b0e6eb122250b86938c3f647af9513d43485c` 上追加本機修改，分支仍為 `codex/mmh-demo-storage-isolation-20261013`。待確認推送的手機 `4fc41f1` 與後端 `eade0de` 不變；本輪無推送、部署、實機安裝或商店提交。

## 修正與實際行為

兩端新增 MedicalAccountPreferences，由建構時指定固定 scope，處理加密帳密、撤回清單與重新同意清單。既有 AppSettings 的公開呼叫仍可使用，內部轉交 MMH 設定儲存區；舊的密文與待辦不需要搬移。iOS 的 MedicalLocalStores 另提供對應 scope 的 accounts 與 session epoch，供後續固定環境 context 接線。

同意清單以 process 內共用鎖保護 read-modify-write，避免多個 instance 同時新增時覆蓋其他代碼。撤回與重新同意仍為不同清單。這項修改沒有解決同一代碼多次同意變動的完整時序同步，也不宣稱能跨 process 協調；這些仍需在背景同步接線時驗證。

測試發現共用 PhiCrypto 為相容早期病歷，會原樣回傳没有 `enc1:` 前綴的文字；舊的密碼讀取也沿用了這條相容路徑。本次只在帳密儲存層要求加密前綴與成功解密：毀損或非密文回空值，要求登入，不當成可用密碼。病歷讀取仍保留原有相容行為。密碼加密失敗時不變更既有帳號，也不使既有 session 失效。

兩範圍仍在同一 App 沙箱、使用既有裝置加密金鑰。設定隔離不是 OS 權限隔離，也不是 Google／WoundAI 身分授權。每次實際送件仍必須通過伺服器授權與保存收據檢查。

## 本輪測試

| 測試 | 結果 |
|---|---|
| iOS MMH 完整回歸 | 108/108，包含新增 6 項設定儲存測試 |
| 一般 iOS 醫療版設定相容測試 | 10/10，包含原後端預設／保留既有位址与新增設定測試 |
| Android MMH validation 單元測試 | 97/97，0 skipped |
| Android Pixel_7 instrumentation | 11/11，0 skipped；設定隔離4、資料儲存隔離3、登入按鈕狀態4 |
| Android validation APK／test APK／internal Kotlin | 建置成功 |

測試使用獨立偏好設定名稱與合成資料，未讀取或輸出使用者帳密。涵蓋相同帳號／同意代碼的跨 scope 保存、重開後讀回、只清除指定 scope、舊密文相容、毀損拒絕、登入等待與門禁失效顯示。两端各執行100次併發新增待辦並核對所有代碼仍存在。

初輪 iOS 毀損密碼案例失敗，揭露上述明文相容路徑；修正後完整108項通過。一般 iOS 首次命令誤用目錄名 WoundAITests 作為 target，未執行測試；改用實際 WoundMeasurementAppTests 後10項通過。這些失敗紀錄與最終成功紀錄均保存。

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/mobile-parity-20261013/preferences/`，包含建置／測試 log、Android XML 与 summary.json。

## 仍需完成的接線與發布

儲存與偏好設定具備固定 scope，不表示 App 已能完整切換環境。ConsentSync、Google SDK／IAP transport、背景上傳 worker、匯出及 AppState 仍須綁定同一不可變的環境 context；離開環境後，舊回應不可更新新環境狀態。尚未建立合成示範雲端服務或審查入口。

手機 UI／登入修正與後端 auth/session 仍待成對來源公開提交、CI 與候選部署；新手機版不得直接配對尚未具備端點的線上版本。TestFlight 外測、Play 安裝版實機登入及連拍延遲驗收仍未完成。這輪沒有新的端到端延遲數據。
