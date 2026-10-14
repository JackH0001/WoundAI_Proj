# MMH 原生傷口二值遮罩與成對保存契約（2026-10-13）

## 狀態與發布邊界

本機整合基底 `dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00`，疊加已交付 W0–W3，再加入本文件的跨平台修改。尚未 commit、push、部署或替換實機／商店版本。不改 MMH 來源分級、IAP、角色、同意、撤回或稽核政策。

舊版只有外輪廓與組織遮罩，無法完整保留傷口孔洞；組織碼 0 也同時可能代表未知組織，不能據此猜傷口邊界。新版本獨立保存使用者確認後的 `EditRaster.mask`。

## 線上資料

`POST /api/v1/annotation` 增加：

- `mask_contract`: `woundai.annotation-masks/1`。
- `wound_mask_png`: 與 `tissue_raster` 同 mw×mh 格點的無損 PNG，R 通道 0/1，GB=0、A=255；後端也接受等義 8-bit L。1 是傷口內，0 是傷口外，孔洞與分離區塊保留。
- `wound_mask_sha256`: 原始 PNG 位元組的 SHA-256。與組織遮罩分開核對。
- `tissue_mask_png` 必須成對提供；非零組織標籤不得出現在最終傷口外。傷口內的未知組織 0 合法。

位置共用 `tissue_raster={rx0,ry0,mw,mh,m_scale}`；影像座標邊界矩形是 `(rx0,ry0,mw/m_scale,mh/m_scale)`。兩端不重新由外輪廓建造這份上傳遮罩。

後端先驗完整成對資料，再執行保存；遮罩驗證失敗回 400，不写資產／入列 intent。儲存失敗不得回保存成功；可重試，不覆寫既有內容定址資產。遮罩存在但組織資產不完整時也不可確認成功。

傷口資產位於 `wound_masks/<image_id>/<SHA256>.png`，納入原修訂 signature。相同封包重送回原 `annotation_receipt_id`；只改二值遮罩、即使外輪廓與組織碼不變，也會建立不同修訂。原始深度延續現有明確 revision 綁定，不自動改指最新標註。

## 成功收據與重送

新契約的 enqueued／duplicate_skipped 回覆均必須包含正確的個案、影像、16 位修訂收據、mask_contract、wound_mask_sha256、tissue_mask_sha256。iOS／Android 對兩個預期雜湊逐一核對；HTTP 200 本身不代表成功。

新客户端建立送件時必須有兩份遮罩；缺少或編碼失敗則保留本機紀錄並拒絕送件。舊 iOS outbox 封包照原位元組重試，後端仍接受舊契約，但不聲稱已有原生二值遮罩。Android 持久 outbox 不在本次完成範圍。

`GET /api/v1/flywheel/record/<image_id>/wound_mask.png?revision=<receipt>` 保留角色／標註者、機構及撤回檢查；回傳 no-store。讀回、去重確認與 manifest 皆重新驗證已保存的雜湊與遮罩關係，歷史修訂不改寫。

## 匯出

manifest 的 wound／tissue 與 interrater 包含 mask_contract、wound_mask_key、wound_mask_sha256。舊紀錄留空，不補猜。

本機 wound exporter 優先使用已驗證原生遮罩，以影像像素中心映射到編輯格點：`floor((x+0.5-rx0)*m_scale)`，y 同理，不插值類別；超出格點部分為 0。原生資產失敗時不退回輪廓冒充成功。輸出 manifest 說明重採樣表示法、原生 hash 與格點。原生解析度的細節仍以原資產為準；縮小可能丟失小孔洞。舊資料才用全部輪廓聯集。

本次尚未完成 IAP 取件工具的原生傷口／組織／深度全套下載，不能據此聲稱 WoundAI3D 雲端匯出或完整 3D 配準已驗收。

## 本機證據

- 後端 native mask HTTP 測試 10/10：孔洞、分離區塊、未知組織、去重、新修訂、延遲深度、有效 PNG 替換、撤回、他人存取、儲存失敗與舊客戶端相容。
- iOS MMH 模擬器整組 115/115；新增實際 PNG 編碼／解碼、缺漏擋下與收據核對。
- Android 單元測試及兩種 variant 編譯；隔離 emulator 的 PNG／實際 localhost HTTP 儀器測試 11/11。
- 兩端實際生成的合成 JSON 交給 Python 後端驗證：2/2，包含真實 PNG 位元組，不只比對欄位名稱。
- 7/7 變異被測試攔截。初輪缺少有效 PNG 替換反例，已補測；並未放寬程式檢查。
- 完整工程、tools、秘密掃描及數量詳見本次證據 `native-mask-validation-20261013` 的 summary.json。

## 後續

先完成來源覆核與 Windows 所有權提交，再以相容後端部署、客戶端更新、實機同筆上傳／重送／深度读回的順序驗收。新客戶端不應先連到尚未支援成對雜湊收據的舊後端。此修改沒有完成 TestFlight 外測發布或新增示範服務。
