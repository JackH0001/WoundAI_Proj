# 交辦：Windows → Mac（2026-08-19）

Windows 端本輪的變更與查證結果，以及需要 Mac 端接手的項目。
每一項都附「為什麼」與「怎麼驗」——只寫「請改 X」的交辦，接手的人無法判斷改對了沒有。

---

## 一、本輪 Windows 端上線的東西（pull 後會看到）

| 項目 | 位置 | 對 iOS 的影響 |
|---|---|---|
| `POST /api/v1/depth` | `api_flywheel.py` | **有**，見交辦 4 |
| `lite` 角色（權限全空） | `auth_users.py` | 無（Lite 用 classify，不查角色） |
| 主控台「改角色」＋角色下拉改由 `ROLES` 產生 | `api_console.py` | 無 |
| 「我的送件」對送不出件的角色不再空白 | `api_flywheel.py` / `api_console.py` | 無 |
| Android 接上 `quality` 與 health 降級警示 | `BackendClient.kt` 等 | **有**，見交辦 1 |
| Android versionCode → 22 | `Android/version.properties` | **有**，見交辦 2 |
| `tools/owner_guard.py` | — | 建議兩端提交前都跑 |

---

## 二、交辦 Mac 端

### 1. ⚠ iOS 從來沒有真的送出 `quality`

**證據（不是推測）**：主控台送件清單裡**每一筆的品質欄都是 `—`**，
包含 2026-08-09 那四筆 iOS 送的（`depth_source=lidar_local`）紀錄。

讀碼確認：

```
BackendClient.swift:617   if let v = quality, !v.isEmpty { obj["quality"] = v }   ← 有
MeasureFlowView.swift:500 try await backend.submitAnnotation(...)                 ← 沒傳 quality
ReviewView.swift:271      try await backend.submitAnnotation(...)                 ← 沒傳 quality
```

`ClassifyResult.quality` 有解析（`BackendClient.swift:475`），但**兩個呼叫端都沒有傳下去**。
`MeasureFlowView.swift:365` 的 `m.quality = "backend"` 是實體的字串欄位（資料來源標記），
與品質指標無關。

**後果**：後端 `/api/v1/dataset/manifest` 的品質門檻
（`min_focus`／`max_clipped`／`max_skew`／`min_marker_frac`）
對**缺欄位的紀錄一律放行**（舊紀錄本來就沒有，擋掉會把早期樣本整批丟掉）。
所以模糊、過曝、角度過斜的 iOS 樣本會照樣進訓練集，而 manifest 報表上看不出來。

**修法**：兩個呼叫端把 `quality: <classify 回來的 quality>` 傳下去。
Android 端的做法可參照 `MeasureViewModel.lastQuality`——
在 classify 當下承接、送出時原樣回傳，**端上不挑鍵、整包收**
（硬編一份鍵清單去挑的話，後端日後加的新指標永遠不會落盤，且沒有任何地方會報錯）。

**怎麼驗**：量測一次並送出標註 → 主控台該筆的品質欄應出現五個指標
（`focus_lapvar`／`clipped_frac`／`roi_short_px`／`marker_frac`／`marker_skew`）。

**順帶更正 `docs/PARITY.md`**：先前宣告「`quality`：iOS 有、Android 無」。
實際是 **iOS 讀了但沒送、Android 現在讀也送**。那條宣告的方向是反的。

### 2. iOS 版號推到 22

`iOS/project.yml:48` `CURRENT_PROJECT_VERSION: "21"` → `"22"`。

Android 已在 `4bfdcb4` 推到 22 並建出 b22。目前 `tools/parity_check.py` 唯一的紅燈就是這個：

```
[version_mismatch] Android versionCode=22 vs iOS CURRENT_PROJECT_VERSION=21
```

**怎麼驗**：`python3 tools/parity_check.py` 回 0。

### 3. Mac 的 clone 沒有 git-lfs

`.gitattributes` 第 55/59 行把 `*.onnx`、`*.zip` 掛了 LFS。版控裡存的是 **131/132 bytes 的指標**，
真檔在 GitHub LFS。Mac 端看到的「`wsm_stub.onnx` 131B→262KB」就是這件事——
Windows 有 LFS smudge、拿到真檔；Mac 沒有、拿到指標。

**修法**：`git lfs install && git lfs pull`

**為什麼要處理**：沒補的話 Mac 手上沒有真正的 stub 模型（跑不了需要它的東西），
而且哪天不小心 `git add` 到二進位檔，可能把真檔換成指標——**commit 會成功、push 會成功**。

（`Android/bugreport-*.zip` 已於本輪移出版控並補進 `.gitignore`。它含
`jack.hou@gmail.com` ×130 與完整裝置清單，但無憑證外洩、是模擬器、無病患資料；
該 email 本來就公開於每一筆 commit 的作者欄，故未改寫歷史。）

### 4. ReviewView 補送接上 `POST /api/v1/depth`

契約與**四種退件條件**寫在 `docs/woundai3d_depth_capture_plan.md`。
若與 `docs/depth_capture_contract.md` 有出入，**以計畫書為準**——
端點的驗證邏輯是照它寫的。

要點：

| 條件 | 為什麼擋 |
|---|---|
| `len(bytes) ≠ w×h×4` | 原始 f32 沒有魔術數字，這是唯一抓得到截斷的檢查 |
| 缺 `fx/fy/cx/cy` | 反投影不了，存下來是「有資料但不能用」的假庫存 |
| 有效覆蓋率 < 5%（0.05–5 m） | 一次擋掉全零、單位寫成毫米、大端序三種 |
| `format` 不是 `f32_le_meters` | 端點不做單位或位元組序轉換：猜錯不會有錯誤訊息 |

上傳成功後把該筆 `depth_source` 由 `lidar_local` 升級為 `lidar`。
後端**不會**回頭改寫佇列裡那一筆（唯讀累加），而是寫進 `depth_index.jsonl`，
由 `/api/v1/flywheel/records` 側檔優先 join。

**怎麼驗**：補傳後主控台該筆的深度欄由 `lidar_local` 變成 `lidar` 並顯示位元組數。
目前有 4 筆 `lidar_local` 可當回填對象（2026-08-09，WD-158B61F0 ×2、WD-BF636F89 ×2）。

---

## 三、兩端共有、還沒解的問題

### A. `classify` 完全不寫稽核

`app.py` 只有 5 處 audit，全是登入類（`login`／`login_failed`／`otc_*`）。
影像在 `app.py:1529` 算出 `sha1[:16]`、`:1549` 寫進 `images/`，**沒有任何稽核記下這件事**。

所以 `lite_backend_contract.md` 交辦第 9 點「按 actor=lite01 時段清點」**目前做不到**：
稽核裡沒有 classify 事件，只能靠 `images/` 的檔案時間推，而那推不出操作者。

IRB 會問的正是「收到哪些影像、來自誰、什麼時候」。Windows 端會補
`image_stored`／`image_reused` 稽核，`lite/segment` 上線時沿用同一套。

### B. 補送路徑不帶 `quality`

`quality` 只在**當次量測**送得出去。從時間軸補送標註時指標已不在手上——
兩端的 measurement 紀錄都只有 `quality: String`（值 `"backend"`，是來源標記不是指標）。
補送的那一筆在訓練集匯出時篩不掉。

因為兩端行為一致，`parity_check` 不會報，但它仍是缺陷。
要消除得加 DB 欄位（`qualityJson`）與 migration，兩端同步做。

### C. `parity_check` 的第三次盲點

交辦 1 那個 bug，`parity_check` **報不出來**：它檢查「送出程式碼裡有沒有出現這個欄位名」，
而 `obj["quality"] = v` 確實在那裡——缺的是呼叫端。

這是同一個弱點的第三次：

```
resume = null                參數在、值是 null
initialPolygons = emptyList()  參數在、值是空清單
quality:（無呼叫端）           參數在、沒人傳
```

三次都是**宣告在、值不在**。要靜態判斷「有沒有呼叫端傳值」比前兩次難，
硬加一條容易變成另一個假綠，所以本輪**沒有動 parity_check**——
先記在這裡，值得單獨想清楚再做。

---

## 四、lite01 稽核查證（回應 `lite_backend_contract.md` 第 9 點）

主控台稽核軌跡查證結果：

```
459–452  2026-08-19 05:31–05:44  default:lite01  login  role=physician  ×8
450      2026-08-19 03:32:32     default:admin   user_upsert default:lite01 role=physician
```

**lite01 至今只有 8 筆 login，沒有任何 annotation。訓練集是乾淨的**，
不需要標記排除任何標註。第 9 點只剩「那兩張影像」的部分，而那受限於問題 A。

`lite` 角色已建立（權限全空），主控台也補上「改角色」入口。
**部署後**把 lite01 由 physician 改成 lite——現在改會被 `role not in ROLES` 擋下。

⚠ **不要刪帳號**。稽核軌跡引用著 `default:lite01`，那 8 筆 login 要留著。
帳號管理刻意不提供刪除，理由同此。

### lite01 掛 physician 的暴露面（回應「告知它目前掛的角色以評估暴露面」）

physician 是權限最大的角色。後端**實際會查**的權限裡，lite01 多拿到的是：

```
gt.verify          doctor_verified 的唯一來源
annotation.submit  送訓練標註
patient.manage     對任意 WD 代碼撤回同意
flywheel.stats     看送件統計
```

民眾版服務帳號握著醫師背書，意味著民眾拍的照片**有辦法**帶著醫師身分進訓練集。
零濫用不代表零風險——當時擋住它的是 Lite 沒寫那段程式碼，**而「還沒有人這樣做」不是控制措施**。

另一件今天就已成立的損害：稽核軌跡裡那 8 筆的角色記載是 physician。
那份紀錄說的不是真正發生的事，而稽核的價值正在於此。改角色不會回溯修正它們，
所以本文件把事實記在這裡。

（Jack 確認：lite01 的帳密**沒有寫進 App**，由使用者在 Lite 設定頁「進階」自行輸入。
所以沒有從二進位萃取的風險。若 TestFlight 階段要發給測試者，那條風險就會回來。）
