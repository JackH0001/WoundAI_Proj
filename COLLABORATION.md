# WoundAI_Proj — 協作說明（請先讀）

本 repo 為 WoundAI 之**協作沙箱**：已移除核心 IP（模型權重、訓練／雲端訓練 pipeline、
標註規則方法學、自有資料）與第三方程式碼，供協作者開發 App 與標註平台。

## 重要事項
- ⚠ 本 repo **不含真實模型**。`models/stub/wsm_stub.onnx` 為**假模型**（I/O 同 wsm.onnx：
  input [1,256,256,3] → output [1,256,256,1]，輸出固定假遮罩），僅供建置與 UI 測試。
  真實推論請呼叫官方推論 API（介面見 `openapi/`）。
- 機敏已淨化；**請勿提交**任何密碼／金鑰／真實病患影像。
- 本 repo 為**全新 git 歷史**，不含原專案歷史與 LFS 物件。

## 環境設定
1. 防呆：`pip install pre-commit && pre-commit install`（gitleaks 機敏掃描＋大檔阻擋）。
2. CI：`.github/workflows/gitleaks.yml` 已就緒。
3. 授權與保密：見 `LICENSE` 與 `docs/NDA_CLA_範本.md`（協作者需簽署）。

## 已移除（保留於私有核心，不在此 repo）
模型權重（*.onnx/*.h5/*.pth/*.mlmodel…）、`weights/`、`雲端 AI 模型訓練及分析服務/`（訓練 pipeline）、
`傷口標註規則`（方法學）、第三方（FUSegNet／Deepskin／wound-segmentation／ImageJ）、`test_images/`（資料）。

## 待擁有者人工再確認（可能仍含部分核心邏輯）
- `Backend/Flask/`：含量測／分類後端邏輯——評估是否僅保留 API 介面或移至私有核心。
- 各端 preprocessing 常數（如 OnnxSegmentationModule、OnnxAIModule）：如屬機密可進一步 stub。

> 本說明非法律意見；授權與合規細節請洽專業顧問。
