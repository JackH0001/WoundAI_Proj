# API 契約：/api/v1/classify（與相關端點）

對接 Backend/Flask app.py ↔ Android BackendClient。契約測試 engineering/phase2/test_api_contract.py。所有端點需 JWT。輔助、非診斷。

## POST /api/v1/classify
**Req(multipart)**：image=<jpg/png>；選配 cm_per_pixel=<float>（無 ArUco 時手動校正）。
**Resp 200(json)**：
```json
{
 "stage2_segment": {"model":"student","wound_ratio":0.077,"confidence":0.83},
 "stage3_calibrate": {"method":"aruco(marker 12.0mm)","area_cm2":8.07,"note":null},
 "stage4_tissue": {"method":"v2(WB+HSV)","tissue_frac":{"necrosis":0.08,"slough":0.14,"granulation":0.78,"epithelial":0,"other":0}},
 "stage5_severity": {"tool":"PUSH (NPUAP 3.0)","area_subscore":7,"tissue_subscore":2,"exudate_subscore":null,"total_partial_img":9,"total_full":null,"range_full":"0-17(低=癒合)"},
 "disclaimer":"輔助用途、非診斷、需醫師確認;滲液量無法由單張影像判定,需醫師輸入"
}
```
錯誤：503 模組不可用 / 400 缺 image / 500 推論。App 取用：area_cm2、total_partial_img/total_full、tissue_frac、confidence。

## POST /api/v1/segment/escalate（雙軌難例上雲，已實作）
Req image → Resp { mask_png_b64, model, model_version, route:"cloud" }；缺模型→503 graceful。

## POST /api/v1/annotation（待實作）
gt_polygon / tissue_classmap / exudate / care_note / correction_iou / doctor_verified → 飛輪佇列（需去識別+同意）。

## POST /api/v1/consent/withdraw（待實作）
{code} → 對應去識別資料下架、排除後續訓練、稽核留存。

> 註：classify 與 escalate 已在 app.py;annotation 與 consent/withdraw 為契約定義、待後端實作（行為已於雲端原型/規格描述）。
