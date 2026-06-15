# Phase 0 工程骨架（低風險落地）

把「務實執行建議」落成可執行骨架。核心：**可靠核心恆可用；AI 不確定項受 flag＋registry 雙閘控、缺模型 graceful degrade、絕不偽造輸出。**

## 檔案
| 檔案 | 用途 |
|---|---|
| `preprocessing.json` | 前處理單一真實來源(SSOT)；各端(iOS/Android/Windows/Cloud)一律讀此，禁止硬編碼 |
| `preprocess_consistency.py` | SSOT 前處理參考實作＋「兩端位元級一致」測試樣板 |
| `model_registry.json` / `model_registry.py` | 模型清單＋ModelRegistry；缺檔回 None、呼叫端 graceful degrade |
| `feature_flags.json` / `feature_flags.py` | 功能旗標；未達標/無模型之 AI 功能預設 false |
| `eval_harness.py` | 分割評測(Dice/IoU/面積誤差) 純函式＋資料夾批次→CSV |
| `workflow_sim.py` | 端到端工作流模擬＋10 情境完整性檢查（CI 可跑） |
| `models/stub/wsm_stub.onnx` | 假模型（I/O 同 wsm.onnx），供建置/測試，無真實權重 |

## 用法
```bash
pip install numpy onnxruntime pillow
python workflow_sim.py        # 期望輸出 10/10 PASS，可納入 CI
python model_registry.py      # 列出各模型 available/missing
python eval_harness.py <pred_dir> <gt_dir>   # 產 eval_report.csv
```

## 對應建議
- 量測/校正＝可靠核心（幾何，恆可用） → `area_mm2()`、`workflow_sim` 情境 1–2
- 半自動分割＋人工退回 → 情境 3–4（缺模型不崩潰、不偽造）
- 分類雙閘控（flag＋模型） → 情境 5–7（disabled/unavailable，不偽造標籤）
- 規則式嚴重度／治療建議（無需模型、可解釋） → 情境 8–9
- 前處理跨端一致 → 情境 10
