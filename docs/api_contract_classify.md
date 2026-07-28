# API 契約：/api/v1/classify（與相關端點）

對接 Backend/Flask app.py ↔ Android BackendClient。契約測試 `engineering/phase2/test_api_contract.py`、
資料鏈回歸 `engineering/phase2/test_flywheel_datachain.py`、真實 HTTP `engineering/phase2/test_backend_http.py`。
所有端點需 JWT。輔助、非診斷。

> **2026-07-28 更新**：飛輪端點已實作（非「待實作」）；classify 回應新增 `image_id` / `image_w` / `image_h`，
> 且 annotation **強制**帶回這三者。原因見「飛輪資料鏈」一節。

## POST /api/v1/classify
**Req(multipart)**：`image=<jpg/png>`；選配 `cm_per_pixel=<float>`（無 ArUco 時手動校正，
單位為**上傳影像**的 cm/px——App 若先縮圖，須換算後再傳）；選配 `escalate=off` 關閉自動上雲。

**Resp 200(json)**：
```json
{
 "image_id": "aaaabbbbccccdddd",
 "image_w": 2048, "image_h": 1536,
 "stage2_segment": {"model":"student","wound_ratio":0.077,"confidence":0.83,
                    "route":"student","escalated":false,"au_area_ratio":1.02,"iou_student_au":0.87,
                    "wound_polygon":[[x,y], ...]},
 "stage3_calibrate": {"method":"aruco(marker 12.0mm)","area_cm2":8.07,"mm_per_px":0.2034,"note":null},
 "stage4_tissue": {"method":"v2(WB+HSV)","tissue_frac":{"necrosis":0.08,"slough":0.14,"granulation":0.78,"epithelial":0,"other":0}},
 "stage5_severity": {"tool":"PUSH (NPUAP 3.0)","area_subscore":7,"tissue_subscore":2,"exudate_subscore":null,"total_partial_img":9,"total_full":null,"range_full":"0-17(低=癒合)"},
 "disclaimer":"輔助用途、非診斷、需醫師確認;滲液量無法由單張影像判定,需醫師輸入"
}
```

| 欄位 | 用途 |
|---|---|
| `image_id` | 上傳影像內容 sha1 前 16 碼；後端已存於 `flywheel/images/<id>.jpg`。**送標註時必須帶回** |
| `image_w` / `image_h` | `wound_polygon` 與醫師修邊 GT 的座標空間。缺了就無法把 polygon 柵格化成遮罩 |
| `wound_polygon` | 最大連通輪廓（approxPolyDP 0.003），供 App 醫師修邊起點 |
| `mm_per_px` | ArUco 尺度直傳。App 修邊面積＝像素數×(mm/px)²，**不依賴 AI 初始面積** |
| `route` / `escalated` | `student`（端上主力）或 `cloud_escalated(AU)`（難例自動上雲集成） |

錯誤：503 模組不可用 / 400 缺 image・解碼失敗 / 500 推論。
App 取用：area_cm2、total_partial_img/total_full、tissue_frac、confidence、mm_per_px、image_id。

## POST /api/v1/segment/escalate（雙軌難例上雲，已實作）
Req `image` → Resp `{ mask_png_b64, model, model_version, route:"cloud" }`；缺模型→503 graceful。

## POST /api/v1/annotation（**已實作**）
醫師驗證標註 → 再訓練佇列。

**Req(json)**：

| 欄位 | 必要 | 說明 |
|---|---|---|
| `code` | ✔ | 去識別代碼，須符合 `^WD-[A-Za-z0-9_-]{1,32}$` |
| `gt_polygon` | ✔ | ≥3 點，座標須落在 `image_w×image_h` 內 |
| `exudate` | ✔ | 0–3（PUSH 滲液子分；醫師輸入，單張影像判不出來） |
| `doctor_verified` / `deidentified` / `consent_train` | ✔ | 三者皆須為 `true` |
| `image_id` | ✔ | classify 回傳值，`^[0-9a-f]{16}$`；後端須查得到該影像 |
| `image_w` / `image_h` | ✔ | classify 回傳值 |
| `mm_per_px` / `route` / `seg_model` / `app_version` / `correction_iou` / `care_note` | — | 溯源，建議都帶 |

**Resp**：
- `200 {status:"enqueued", code, image_id, note}` — 已入佇列（`note` 非空表示本筆是同影像的醫師修訂版）
- `200 {status:"duplicate_skipped", ...}` — 同影像同遮罩已在佇列，自動略過
- `400 {error, issues:[...]}` — 守門未過（缺欄位／格式不合／三同意不全／座標超界／影像不存在／該影像已撤回同意）

## POST /api/v1/consent/withdraw（**已實作**）
**Req**：`{code}`（選配 `image_id`）。
**Resp 200**：`{status:"withdrawn", code, image_ids, quarantined, effect}`

撤回涵蓋**整張影像**：由 code 回查其 `image_id`，該影像的所有標註（含被取代的舊版）一律排除於
訓練集與統計，影像檔移入 `flywheel/quarantine/`。佇列 jsonl 維持 append-only（稽核軌跡不竄改），
排除發生在消費端；實體銷毀另走資料刪除程序並留紀錄。

## GET /api/v1/flywheel/stats（**新增**）
**Resp 200**：
```json
{"total":20,"orphan_no_image":0,"malformed":0,"image_file_missing":0,
 "withdrawn":0,"consent_invalid":0,"superseded":2,"trainable":18,
 "images_on_disk":20,"quarantined":0}
```
`trainable` 才是實際可訓練樣本數。統計守恆：`total = orphan + malformed + withdrawn + consent_invalid + image_file_missing + superseded + trainable`。

## 飛輪資料鏈（為什麼 image_id 是必要的）

2026-07-28 稽核發現舊版標註只存 `gt_polygon`，**沒有影像也沒有影像尺寸** →
佇列裡 8/8 筆都是「孤兒 GT」，既無像素可訓練，也無尺寸可柵格化。修正後的鏈路：

```
classify（存影像、回 image_id + 尺寸）
   → App 醫師修邊（GT，座標空間＝image_w×image_h）
   → annotation（強制綁定，缺者 400）
   → stats（看 trainable）
   → engineering/phase2/export_flywheel_dataset.py → images/masks/manifest/資料卡
```

其他細節見 `Backend/Flask/flywheel/README.md`。

> 註：iOS 走的是另一套 `/annotations` 契約（`openapi/annotation_segmentation.yaml`、
> `AnnotationFlywheelService.swift`），**尚未對齊本次修正**，是已知待收斂項。
