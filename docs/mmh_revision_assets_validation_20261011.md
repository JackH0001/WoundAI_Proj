# MMH 歷史遮罩、深度修訂與雙登入說明（2026-10-11）

## 結論與範圍

本機修正並驗證：相同影像各修訂保存獨立標註回執、組織遮罩、PNG 深度／置信圖及原始 Float32 深度／metadata 參照。相同內容使用相同不可覆寫物件，修訂間以明確回執關聯，不再依 image_id 猜最新深度。

本輪未部署後端、未改 IAM、未回填或改寫既有雲端資料。舊版已覆寫的遮罩無法憑最新檔案復原。程式的不可覆寫操作不等同 WORM／鎖桶；具備儲存管理權限的人仍可能更動物件。

使用者本輪明確要求在 Mac 修正後端歷史遮罩及 App。此為 Backend/ 與 engineering/ 的本次跨平台修改授權；不變更 owner_guard 的 windows 所有權規則。Windows PowerShell 全套仍待 Windows 端執行，不能拿本輪 Mac Python 測試冒充。

## 保存與讀取契約

- `asset_schema=2`；`revision_signature` 為完整 SHA-256，`annotation_receipt_id` 為其前 16 碼，沿用回執欄位長度。簽章涵蓋個案、影像、操作者／組織、完整輪廓、組織遮罩、柵格定位、分數／面積及校正／PNG 深度等內容；不含登入憑證、care receipt、傳送時間。只改分數或校正也會產生新修訂。
- `supersedes_revision_ids` 指向自己的先前修訂；舊 `supersedes` 保留以相容既有消費端。
- 組織及 PNG 深度／置信圖以 image_id＋內容 SHA-256 儲存，JSON 保存物件鍵與 SHA-256。LocalStore 使用排他 hard link；GCS 使用 `if_generation_match=0` 並核對既有內容。送來的組織遮罩未成功保存時回 409，不把它當成整筆已保存。
- PNG 深度格式不合法仍維持既有「2D 標註可保存、depth_source=rejected」行為，不能宣稱 PNG 深度成功。原始深度上傳有獨立回執。
- 原始深度與 canonical metadata 以完整 `payload_sha256` 組合命名；前一版不刪、不覆寫。depth_index 的 `annotation_receipt_id`、image_id、actor、org、影像 SHA-256 與深度／metadata 雜湊建立版本關聯。
- 修邊後若 raw 與 metadata 不變，回 `comparison=identical`、重用相同物件，同時為新標註建立獨立索引關聯。相同標註、相同 payload 的重試只保留一筆索引回執；稽核仍記錄嘗試。
- 新 App 將標註回執保存進加密 outbox，再隨 multipart 補傳 `annotation_receipt_id`。舊待傳工作缺回執會先重送其原始標註 payload 取回明確回執。舊客戶端未帶回執且同操作者有多筆標註時回 409，不猜最新一筆；不同操作者不能替他人的修訂補傳。
- `replaced_previous=false`：實體檔案沒有覆蓋。`superseded_previous` 表示同標註是否已有不同深度版本。`comparison` 比較相同影像、相同操作者的前一份 payload，與標註版本關聯是兩件事。
- 讀取 GCS 的標註／深度索引依 received_at 與 revision_order_ns 排序，不能用 receipt 雜湊檔名當時序。
- 主控台歷史預覽、RGB、組織遮罩使用 `?revision=...` 指定版本。遮罩讀取與 tissue/interrater manifest 核對內容；舊共享檔名若不符合舊 tissue_sig，拒絕當成該版遮罩。
- 原始深度只按 `(image_id, annotation_receipt_id)` join。舊 image-only index 不自動分派給各歷史修訂。manifest 提供 revision、RGB／組織／PNG 深度／置信度雜湊及 `raw_depth` 參照；研究端必須依 manifest/index 的鍵及雜湊取件，不再自行拼 `<image_id>.f32` 或 `.png`。
- 撤回後所有修訂的 PNG／SVG 讀取與深度補傳均拒絕，訓練清單維持既有排除機制。此修正不新增雲端清理、資料銷毀或復原歷史資料程序。

## 實際驗證

- 新增 HTTP／儲存回歸 **17/17**：歷史遮罩、完全相同重送、平行醫師、延遲深度指定修訂、舊客戶端歧義拒絕、同深度跨修訂關聯、深度改版保留舊檔、RGB 變更拒絕、損毀不覆寫、舊遮罩不符、無證據不 join、撤回、分數變更、GCS 檔名排序、並行重試、GCS create-only precondition、部分寫入失敗後重試。
- 6/6 故障變異被新測試抓到：共享檔名、猜最新修訂、移除操作者檢查、略過遮罩雜湊、略過 RGB 綁定及移除索引時序排序。
- 11 支後端／資料鏈腳本皆 exit 0：depth_endpoint（132 檢查）、depth_chain、record_preview、records_scope、retract_and_records、flywheel_datachain（20 測試）、store_abstraction、tissue_dataset、tissue_export（22 檢查）、p0_4_staging（29 測試）、medical_revision_assets（17 測試）。新測試由既有 Windows runner 的 test_*.py discovery 收入。
- MMH 1.0(32)：**59/59 單元、2/2 UI** 通過；真機 Debug 建置及 codesign 驗證通過。第一次 UI 測試發現說明擠下登入控制項，改為可展開「為什麼需要兩次登入？」後重跑 UI 通過。
- 初次安裝連線逾時；使用者接線解鎖後重試成功。已用 devicectl 讀回 J-iP16PM 的版本 1.0／32，並成功啟動。這只確認安裝與啟動，尚未完成新後端搭配的實機上傳驗收；後端仍是舊候選映像，不可將本機成功當成線上已修復。
- 原始紀錄與完整 log：`../woundai-institution-evidence-20261007/mmh-iap-enable-20261010/revision-assets-20261011/`。

## Google 帳號從哪裡管制

入口：[GCP IAP](https://console.cloud.google.com/security/iap?project=woundai-jackh001)。選取 asia-east1 的 `woundai-backend-mmhps20261007`，在該資源權限新增具名 Google Email，角色選 **Cloud IAP → IAP-secured Web App User**（roles/iap.httpsResourceAccessor）；移除也在此資源處理。不要把同一入口角色擴到整個 project 作為日常加人流程。

本輪唯讀：MMH 服務直接政策為 `user:Jack.Hou@gmail.com`；IAP 的 iap_web 與 cloud_run-asia-east1 上層政策均無直接 binding。本輪未重新稽核 project IAM 的全部有效授權，故這不是「所有可能存取者」清冊。證據：`revision-iap-control-20261011.json`。

OAuth 仍採外部測試模式；新測試者另需在 [Google Auth Platform → Audience](https://console.cloud.google.com/auth/audience?project=woundai-jackh001) 的 Test users 設定。OAuth 測試名單允許登入應用，但不等於取得 MMH IAP 入口權限。

WoundAI 的 dr01／nurse／admin 由 MMH 主控台的帳號管理配置。Google 是入口，WoundAI 是工作角色／稽核送件人；不是雙因素驗證，也未聲稱兩帳號強制一對一綁定。離職或停權需處理兩處，不能只停用 dr01 就認為 Google 入口也撤掉。

官方參照：[IAP 存取管理](https://docs.cloud.google.com/iap/docs/managing-access)、[Cloud Run IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)。

## 與 WoundAI3D 本輪回報對齊

已讀 `WoundAI3D/Docs/MMH_RETRIM_AND_RESEND_VALIDATION_20261011.md` 與 `WOUNDAI_CAPTURE_TIME_HANDOFF_20261011.md`。該工作流的 12/12 物件、20/20 Swift、9/9 時間檢查為對方回報，本輪沒有重跑，不合併進上述測試數。

面積誤差、重複性與資料完整性分別驗收，不要求遮罩每次完全相同。修邊後的 14.91 cm²、−7.35% 與固定深度 3D 表面積 17.82 cm² 不是統計精度合格證明，亦不能將餘下偏差全歸因於遮罩。

採集時間固定在捕獲邊界、標明時間來源，與 saved_at／received_at 分開，是下一項獨立修正。本輪未修改 captured_at 語義、不把舊保存時間改成拍攝時間、不忽略 metadata hash。下一步還需將深度完整 payload 回執保存為本機長期版本狀態；目前本輪 outbox 新增的是 annotation revision 回執。

registration 仍是 not_verified；版本與雜湊一致不證明 RGB／遮罩／深度的幾何配準，也不提升 IRB／訓練資料資格。

## 後續手冊核對（同日）

已同步 iOS／Android 共用 manual.html，新增標明 MMH iOS 32 的專節，不將 iOS 的 Google 入口與接續上傳推定為 Android 已完成。修正存檔不等於醫師確認、補送不需故意改遮罩、各上傳狀態、行政副本及深度內容揭露；環境說明移除管理者尚未登入的舊狀態並加入入口管制位置。歷史驗證報告不改寫為新版通過。

本次文件驗證：既有 test_manual_consistency.py 105/105；另以目前 Swift 原始碼逐一比對 12 個 MMH 按鈕／狀態文字，兩平台 HTML 位元相同、HTML 可解析、內部錨點無重複或缺漏；git diff --check 通過。既有 105 項主要檢查 Android 字串／權限，不能視為完整 iOS 語意或畫面驗收。本輪未重新建置 App，因此手機已安裝 32 版內的手冊尚未更新。共用 Android 手冊同步限文件，不修改 Android 功能；依使用者本輪要求更新跨版本說明，保留原 owner_guard 規則。
