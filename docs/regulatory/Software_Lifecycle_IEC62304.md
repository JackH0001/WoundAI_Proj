# WoundAI 2D — 軟體生命週期與追溯（IEC 62304 模板,鏡像 WoundAI3D Z7/Z8）
> 草案。建立 需求→設計→實作→驗證 之追溯矩陣。

## 軟體安全分級（IEC 62304）
初判 **Class B**(可能造成非嚴重傷害;誤量測→延誤但非直接致命),待風險檔確認。

## 追溯矩陣（節錄,待補全）
| 需求 | 設計/模組 | 實作 | 驗證證據 |
|---|---|---|---|
| 前處理跨端一致 | SSOT preprocessing.json + codegen | `gen_preprocessing_constants.py` | `preprocess_consistency.py` CI PASS;三端產生檔一致 |
| 面積量測準確 | ArUco 面積比例法 | `aruco_calibrate.measure_area_cm2_ratio` | phantom n=30 ~3–5%;`ArUco面積實測.csv` |
| 分割品質 | smp/集成 | `segment.py` | 臨床 n=3 Dice 0.80;集成 0.855 |
| 缺模型不偽造 | ModelRegistry graceful degrade | `model_registry.py` | report 缺檔回 MISSING |
| 分割指標可重現 | eval_harness | `eval_harness.py` | seg_metrics 單元測試入 CI |

## 待補（M3–M4）
臨床前驗證報告、組態管理/版本、變更管制、SOUP(第三方:onnxruntime/OpenCV/smp)清單與授權、網路安全。
