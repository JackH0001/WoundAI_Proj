# WoundLite 民眾版後端契約

狀態：**端點已實作（2026-08-19，Windows/Backend）**，尚未部署。
本文件由 Mac/iOS 端起草（2026-08-18），Windows 端於 2026-08-19 回寫定稿欄位。

實作與提案的差異、以及**上架前的必要條件**見下方〈2026-08-19 實作回寫〉。

---

## ⚠ 上架前必須先解決：`anon_id` 擋不住任何有意的濫用

契約寫「以 App 附帶的裝置匿名代碼限流」。但 `anon_id` 是**客戶端自己產生的字串**，
改一個就換一個身分。所以限流：

- 擋得住：誤觸、失控的重試迴圈、單一裝置的異常用量
- **擋不住**：免費雲端檔案空間、每次呼叫都跑一次分割直接燒 Cloud Run 的錢、
  把影像灌進 GCS

實作已加上**來源 IP 的日配額**當第二道（IP 不是身分，CGNAT 之下整棟樓共用一個，
但它至少不是呼叫端說了算）。這仍然只是提高門檻，不是控制措施。

**正式對外開放流量前必須換成真正的裝置證明**（Play Integrity / App Attest）。
那件事後端單方面做不到，需要 App 端配合。在那之前這個端點不該公開。

把這段話放在文件最前面，是因為「限流有做」很容易被讀成「濫用有擋」，
而那不是同一件事。

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

## 2026-08-19 實作回寫（Windows/Backend）

程式碼：`Backend/Flask/api_lite.py`（**獨立模組**——它是整個服務唯一不需要登入的
資料端點，用一個檔案名就回答得出「哪些程式碼是公開暴露面」）。
契約測試：`engineering/phase2/test_lite_segment.py`（32 項）。

### 與提案一致的部分

欄位表、回應格式、429 語意、`research_consent=false` 不落地、
深度沿用 `/api/v1/annotation` 的 16-bit PNG 判準——皆照提案實作。

### 提案沒寫、實作補上的

| 項目 | 為什麼 |
|---|---|
| 回應多 `stored` / `image_id` | 讓 App 能對使用者**據實**說明這張照片有沒有被保存。同意分流講給人聽才有意義；契約原本的回應看不出來 |
| 來源 IP 日配額（預設 200） | `anon_id` 可偽造，換一個就換身分。IP 不是身分，但不是呼叫端說了算 |
| **IP 一律雜湊加鹽後才落盤** | 原始 IP 是個資，而這是民眾健康 App。為了限流而長期保存真實 IP，本身就是一個要交代的資料蒐集 |
| 先查配額再記錄 | 反過來的話，**被擋下的請求也計入配額**，那使用者被擋一次之後永遠解不開 |
| `DELETE /api/v1/lite/data/<anon_id>` | 提案列為「後續版本」，但沒有它，落地的資料就撤不回來。既然要收就要能刪 |
| 人臉偵測（OpenCV Haar，可用 `LITE_FACE_REJECT=0` 關閉） | 交辦第 8 點。**見下方限制** |

### 落地路徑與撤回

`lite/<anon_id>/<image_id>.{jpg,json,depth.png,conf.png}`，索引 `lite_index.jsonl`。

以 `anon_id` 分前綴是刻意的：撤回需要一個可執行的鍵。代價是同一裝置的影像被歸在一起
——這是「可被遺忘」與「不可連結」之間的取捨，契約選了前者。

撤回端點**同樣是匿名的**：任何知道某個 anon_id 的人都刪得掉它。要求證明「你就是那個裝置」
就需要一個身分，而那與匿名互斥。風險方向是安全的（惡意刪除損失的是資料量，不是隱私），
反過來（無法刪除）才不可接受。

### ⚠ 人臉偵測的實際能力

用 OpenCV 內建的 Haar 正面臉分類器，參數刻意保守（`minNeighbors=8`、臉需占畫面 ≥12%），
因為**誤判的代價是把一張合法的傷口照片退掉**，而民眾版使用者只會覺得壞了。

它抓不到：側臉、部分遮擋的臉、以及所有**非人臉的可識別物**
（證件、名牌、刺青、病房門牌、背景中的人）。

**這是縱深防禦，不是保證。** 真正擋得住的是取景指引與「只拍傷口」的流程約束；
後端這一層是補網。**不可以拿它當作放寬前面那一層的理由。**

契約測試刻意**不驗**它的召回率——用測試去背書一個抓不全的偵測器，
會給人錯誤的安全感。

### 交辦清單狀態

| # | 項目 | 狀態 |
|---|---|---|
| 1 | `lite01` 帳號＋role 權限機制 | ✅ `lite` 角色已加（權限全空）；主控台補上「改角色」。**部署後**把 lite01 由 physician 改過去 |
| 2 | `POST /api/v1/lite/segment` | ✅ |
| 3 | `research_consent=false` 不落地 | ✅ 契約測試直接數儲存目錄的檔案數驗證，不只看回應 |
| 4 | `anon_id` 限流與 429 語意 | ✅ 雙軌（anon_id ＋ IP）。⚠ 見文件最上方的上架前條件 |
| 5 | 回寫定稿欄位／PARITY 宣告／通知 iOS 切換 | ⏳ 本節即定稿。**PARITY 尚未宣告**——iOS Lite 目前仍走 classify，還不是落差；等 iOS 切過來、Android Lite 未跟上時才宣告（過期的宣告比沒有宣告更糟） |
| 6 | 三個非 iOS 端變更的來源 | ✅ 已查明：`.gitattributes` 把 `*.onnx`/`*.zip` 掛了 LFS，Mac 的 clone 沒有 git-lfs 所以拿到 131B 指標。請跑 `git lfs install && git lfs pull`。bugreport 已移出版控 |
| 7 | `ROLES` 補 `lite`＋告知 lite01 目前角色 | ✅ 見 `docs/handoff_2026-08-19_windows_to_mac.md` 第四節（掛 physician，暴露面已列出；稽核查證：只有 8 筆 login，無 annotation） |
| 8 | 人臉偵測 | ✅ 已實作，能力界限見上 |
| 9 | 兩筆內測影像標記排除 | ⏳ **目前做不到**：`classify` 全程不寫稽核，稽核裡沒有 classify 事件，只能靠 `images/` 檔案時間推而推不出操作者。Windows 端會補 `image_stored` 稽核 |

## 地端模型保留槽（iOS 端已鑄好）

`iOS/WoundLite/Models/MODEL_SPEC.md`＋`LiteLocalSeg.swift`：模型成熟後把
`WoundSegLite.mlmodel` 放入該目錄、補推論一段即自動啟用
「地端 → 雲端 → 手動」優先序，離線可自動圈選。訓練資料即本契約收集的
去識別影像＋醫療版醫師 GT。

## iOS 端現況（已完成）

- `iOS/WoundLite/`：target、同意分流、手動圈選、深度量測、本地紀錄。
- 雲端路徑暫以醫療 `/api/v1/classify`＋開發帳號驗證（Lite 設定「進階」），
  正式端點上線後改指 `/api/v1/lite/segment` 並移除開發區塊。
