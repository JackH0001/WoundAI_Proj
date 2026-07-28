# flywheel/ — 再訓練飛輪執行期資料（**整個目錄不進版控**）

此目錄存放病人相關的執行期資料，`.gitignore` 已整個排除（僅保留本 README）。
請勿把 `images/`、`*.jsonl` 或匯出的資料集提交到本公開協作 repo。

## 內容

| 路徑 | 說明 | 產生者 |
|---|---|---|
| `images/<image_id>.jpg` | classify 收到的原始影像，檔名＝內容 sha1 前 16 碼（同圖不重複存） | `app.py` `/api/v1/classify` |
| `retrain_queue.jsonl` | 醫師驗證標註佇列（append-only） | `/api/v1/annotation` |
| `withdrawn.jsonl` | 撤回同意墓碑；匯出／統計時排除 | `/api/v1/consent/withdraw` |
| `audit.jsonl` | 稽核軌跡（append-only，不清空） | 全部飛輪端點 |
| `quarantine/<image_id>.jpg` | 已撤回同意的影像，不再進任何資料集 | `/api/v1/consent/withdraw` |
| `archive/` | 歷史歸檔（見 `archive/ARCHIVE_NOTE.md`） | 人工 |
| `dataset_<日期>/` | 匯出的訓練集（images/masks/manifest/資料卡） | `export_flywheel_dataset.py` |

## 資料鏈（2026-07-28 修正後）

```
POST /api/v1/classify        → 存影像、回 image_id + image_w/image_h
        ↓ App 醫師修邊（GT）
POST /api/v1/annotation      → 強制帶 image_id/image_w/image_h，缺者 400
        ↓
GET  /api/v1/flywheel/stats  → total / orphan / missing / withdrawn / superseded / trainable
        ↓
python engineering/phase2/export_flywheel_dataset.py --min-samples 20
        → dataset_<日期>/{images,masks,manifest.json,DATASET_CARD.md}
```

**為什麼 image_id 是強制的**：2026-07-28 前的標註只存 polygon，沒有影像也沒有尺寸，
產出的 8 筆樣本全部無法訓練（「孤兒 GT」）。回歸測試 `engineering/phase2/test_flywheel_datachain.py`
把這件事釘住。

## 排除規則（匯出與統計共用 `api_flywheel.effective_queue`）

孤兒（無 image_id）／欄位格式不合／影像檔遺失／已撤回同意（code 或 image_id）／三同意不全／
同影像被更新版取代 → 一律不進訓練集。統計守恆：
`total = orphan + malformed + withdrawn + consent_invalid + image_file_missing + superseded + trainable`。

## 撤回與重新同意

- `POST /api/v1/consent/withdraw {code}` — 以 **code ∪ image_id 雙鍵**排除該影像的所有標註
  （含被取代的舊版），影像移入 `quarantine/`。重複上傳同一張照片不會讓它復活（classify 會擋）。
- `POST /api/v1/consent/restore {code}` — 受試者重新同意時把影像搬回 `images/` 並解除排除。
  沒有這條，撤回就是死局：影像被隔離、code 被封，日後再同意也補不回來。

## 測試隔離

`test_backend_http.py` / `verify_c1c2c3.py` 預設會寫入**產線**目錄。要完全隔離，
啟動後端時設環境變數：`WOUNDAI_FLYWHEEL_DIR=<暫存目錄>`（`api_flywheel` 與 `app.py` 共用此變數）。
真正收案前請確認 `GET /api/v1/flywheel/stats` 的 `total` 歸零。

## 隱私

僅收去識別代碼 `WD-*`，端點守門要求 `doctor_verified`／`deidentified`／`consent_train` 皆為 true。
影像本身仍屬個資，**不得離開受控環境**。撤回同意採「墓碑＋匯出排除」，
佇列檔維持 append-only 以保稽核軌跡完整（IEC 62304）；若需實體刪除須另走資料刪除程序並留紀錄。
