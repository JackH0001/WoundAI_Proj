# iOS 資料飛輪薄切片（對接新標註 API）

- `AnnotationFlywheelService.swift`：對接 `openapi/annotation_segmentation.yaml`：
  - `segment(image:modelId:imageId:)` → POST `/segment`（multipart）→ `SegmentationResult`（缺模型回 503 → 仍可解析 status=`unavailable`/`manual_fallback`）。
  - `submitAnnotation(_:)` → POST `/annotations`（JSON）→ `AnnotationRecord`（修邊即標註）。
  - `annotationTasks()` → GET `/annotation-tasks`。
  - `baseURL` 預設 `http://localhost:8000`（即 `engineering/phase2/app.py` 的 Flask 服務；生產換 FastAPI + 認證）。
- `Views/FlywheelDemoView.swift`：現場 demo 最小流程（選圖→初稿→修邊→上傳）。校正量測/修邊請接既有 `StandardStickerCalibrationView`、`EnhancedAnnotationView`。
- 契約防漂移：`engineering/phase2/verify_ios_contract.py`（CI 強制）確保 Swift DTO 欄位名/必填與 OpenAPI 一致（無 Swift 編譯器時的把關）。

> 註：iOS 需於 Xcode 編譯執行；本薄切片只新增非 IP 的前端/網路層，模型權重仍留私有後端。
