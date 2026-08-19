# WoundLite 民眾版後端契約（提案 / PROPOSAL）

狀態：**提案中，等 Windows/Backend 端排程實作**。iOS 端已依此設計預留分流；
後端上線前，民眾版一律走本地手動圈選（開發測試可在 Lite 設定頁「進階」填
醫療後端帳密走既有 `/api/v1/classify` 對照）。

本文件由 Mac/iOS 端起草（2026-08-18）。實作時欄位如有調整，以 Windows 端
回寫的版本為準，並同步更新本檔。

## 產品前提（已定案 2026-08-18）

- 民眾版輪廓來源依**研究同意**分流：
  - 同意 → 雲端自動辨識；去識別影像＋LiDAR 深度幾何上傳供精度研究與訓練。
  - 不同意 → 完全離線手動圈選，資料不離機。
- 單一中心傷口（App 端取畫面中央輪廓，其餘丟棄）。
- 民眾版主數字＝表面積（tilt-invariant；phantom 驗證見 2026-08-18 誤差數據）。

## 端點提案

### POST /api/v1/lite/segment

匿名（無醫療帳號）、以 App 附帶的裝置匿名代碼限流。

Multipart 欄位：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `image` | file (jpeg) | 去識別傷口影像（≤2048 長邊，方向已烘進像素） |
| `client` | str | `"woundlite-ios"` |
| `anon_id` | str | 裝置匿名 UUID（首啟隨機生成，不連結任何身分；限流與撤回鍵） |
| `research_consent` | "true"/"false" | true 才可持久化保存；false 時**辨識完即棄**（不落地） |
| `depth_map_png` | str (base64), optional | 16-bit 灰階 PNG，值=mm，0=無效——**與 `docs/depth_capture_contract.md` 完全同格式** |
| `depth_conf_png` | str (base64), optional | 同上契約 |
| `camera_intrinsics` | json str, optional | 深度圖像素空間 fx/fy/cx/cy，同上契約 |
| `depth_scale` | str | `"0.001"` |
| `depth_format` | str | `"png16_mm"` |
| `measured` | json str, optional | App 端算得的 {surface_cm2, projected_cm2, tilt_deg, coverage, median_distance_m}，供後端比對研究 |

回應（200）：

```json
{
  "wound_polygons": [[[x, y], ...], ...],
  "image_w": 2048,
  "image_h": 1536,
  "confidence": 0.93
}
```

錯誤：`429` 限流（App 顯示稍後再試並退手動）；`5xx` App 一律退手動圈選。

### 設計原則

1. **consent=false 不落地**：這是同意分流的可信度基礎。民眾版的同意文案
   寫明「不同意＝資料不離機」，唯一例外是自動辨識本身需要影像過境——
   所以未同意者 App 端根本不呼叫此端點（直接手動），後端的 false 分支
   只是縱深防禦。
2. **與 depth_capture_contract.md 同 wire format**：深度欄位驗證邏輯
   （IHDR bytes 24/25 = 16/0 等）後端已有，直接重用。
3. 不動 `/api/v1/annotation`：那條是醫療版病患同意鏈，兩者不可混。
4. 撤回：`DELETE /api/v1/lite/data/{anon_id}`（後續版本；v1 先保留 anon_id
   欄位讓資料可撤）。

## PARITY 影響

無。此檔為文件提案；後端欄位落地時再依慣例更新 `docs/PARITY.md`
（預期為 backend-only 端點，Android 端無對應功能，需加宣告）。

## 過渡期：WoundLite 專用後台帳號（2026-08-18 決議）

匿名端點上線前，內測不共用 `dr01`，請後端管理者另建**最小權限服務帳號**
（建議名 `lite01`）：

- 權限：僅 `/api/v1/classify`（辨識）。**不給** annotation／病患／個案／同意書 API
  ——Lite 沒有病歷概念，帳號就不該拿得到那些資料面。
- 隔離：Lite 流量掛在獨立帳號下，與臨床樣本統計（n=20 追蹤）天然分開，
  之後清點研究資料也以帳號歸屬切分。
- 撤銷面：帳密若外洩只需停用 `lite01`，臨床端不受影響。
- 帳號建立與密碼由管理者（Windows 端）操作；iOS 端只在 Lite 設定頁
  「進階」由使用者自行輸入，App 與 repo 不儲存明文。
- 正式上架仍走上方匿名 `lite/segment` 端點（anon_id 限流）；帳號只是內測過渡。

### 帳號權限機制（回答「需不需要特別權限或類別」）

- **正式民眾流量：不需要任何帳號**——`lite/segment` 設計為匿名端點，身分即
  `anon_id`，控制手段是限流與 consent 分流，不是登入。
- **過渡帳號 lite01 需要權限分級**。現行後端帳號應該是單一類使用者（登入即
  全 API 可用），直接建普通帳號會讓 Lite 憑證拿得到病患/同意書 API——
  憑證裝在民眾側 App 上，這不可接受。建議 Windows 端擇一：
  - **方案 A（建議）**：`users` 表加 `role` 欄（`clinician` 預設／`lite`）、
    JWT claim 帶 role；`annotation`／`patients`／`consent`／`restore` 等端點加
    `role == "clinician"` 檢查，`classify` 與 `health` 不限。改動小
    （一個欄位＋一個裝飾器），並為未來多角色鋪路。
  - 方案 B（最小）：普通帳號＋約定 Lite 只呼叫 classify。無強制力，
    僅可短期內測，不可帶到 TestFlight 之外。

### 2026-08-19 後端實況核對（Mac 端讀碼確認）

- `/api/v1/classify`＝`@jwt_required()` **無角色檢查** → 任何已登入帳號可辨識、
  影像以 sha1[:16] 內容雜湊存 `images/`（去識別檔名）。
- `auth_users.ROLES` **沒有 lite 角色**（physician/nurse/assistant/engineer/admin）。
  ⚠ 目前的 `lite01` 必掛其中之一——assistant 有 `clinical.view`、engineer 有
  `audit.read`/`gcp.console`，都超過民眾版所需。**最小改法**：`ROLES` 加
  `"lite": "民眾版"`、不加入任何 `PERMS` 集合（敏感端點全查 perm 會自動 403，
  classify/health 只驗 jwt 照常可用），再把 lite01 改指 lite 角色。
- `/api/v1/depth` 要求 `annotation.submit`（僅醫師）＋既有標註綁定 →
  **設計上就不是給 Lite 用的**；Lite 深度／量測數值上傳仍以 `lite/segment` 為準。

## Windows 端交辦清單（推送後接手）

1. `lite01` 帳號＋上述 role 權限機制（方案 A）。
2. `POST /api/v1/lite/segment` 依本檔欄位表實作（深度欄位驗證邏輯與
   `/api/v1/annotation` 現有程式共用）。
3. `research_consent=false` 不落地（辨識完即棄）；true 落地時記 `anon_id`。
4. `anon_id` 限流（建議 30 次/日/裝置起步）與 429 語意。
5. 完成後：回寫本檔定稿欄位、`docs/PARITY.md` 宣告 backend-only 端點、
   通知 iOS 端把 Lite 雲端路徑從 classify 切換到 lite/segment 並移除
   設定頁「進階（開發測試）」區塊。
6. 順帶確認：Mac 工作樹出現三個非 iOS 端的變更
   （`Android/bugreport-*.zip`、兩份 `wsm_stub.onnx` 131B→262KB）——
   若是 Windows 端的真實產出請在該端 commit，否則查一下同步來源。
7. **（2026-08-19 新增）** `ROLES` 補 `lite` 角色（見上方最小改法），並確認
   現有 `lite01` 帳號改指 lite；順帶告知它目前掛的角色以評估暴露面。
8. **（2026-08-19 新增）** `lite/segment` 落地時建議加**人臉偵測**（偵測到
   臉部即拒收並回可讀訊息）：協定層去識別擋不住畫面內容，App 端已加
   「只拍傷口」指引，後端這道是縱深防禦。
9. **（2026-08-19 新增）** 2026-08-19 下午以 lite01 上傳的兩張印刷範例影像
   為內測件（無 anon_id、無 consent 標記）——正式統計時請按 actor=lite01
   時段清點或標記排除。

## 地端模型保留槽（iOS 端已鑄好）

`iOS/WoundLite/Models/MODEL_SPEC.md`＋`LiteLocalSeg.swift`：模型成熟後把
`WoundSegLite.mlmodel` 放入該目錄、補推論一段即自動啟用
「地端 → 雲端 → 手動」優先序，離線可自動圈選。訓練資料即本契約收集的
去識別影像＋醫療版醫師 GT。

## iOS 端現況（已完成）

- `iOS/WoundLite/`：target、同意分流、手動圈選、深度量測、本地紀錄。
- 雲端路徑暫以醫療 `/api/v1/classify`＋開發帳號驗證（Lite 設定「進階」），
  正式端點上線後改指 `/api/v1/lite/segment` 並移除開發區塊。
