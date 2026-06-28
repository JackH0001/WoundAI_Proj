# 再訓練 / 資料飛輪 SOP（可執行,含節點驗證）

> 目的:雲端 A∪U 隨醫師標註增多,穩定變準變可靠;student 由更強老師重新蒸餾。
> 對應現有:AnnotationFlywheelService、correction_iou、/api/train、/api/model/retrain、model_registry、eval_harness。

## 0. 角色與資料流
擷取 → AI 初稿(A∪U)→ **醫師修邊(=GT)** → 標註庫 → 再訓練佇列(correction_iou 排序)→ 週期微調 a_unet/unet++ → holdout 評測 → 過門檻才版本升級+部署 → student 重新蒸餾。

## 1. 觸發條件(任一即啟動)
- **量**:新增「醫師驗證 GT」≥ 100 張(或每月一次,取先到)。
- **質/缺口**:某類別(足部/身體/燒傷…)holdout Dice < 目標,或新型態樣本累積 ≥ 30。
- **線上訊號**:雙軌「上雲比例」連續上升(代表端上跟不上分布漂移)→ 觸發。
> 節點驗證 V1:觸發時記錄「新增 GT 數、類別分佈、觸發原因」於 retrain log。

## 2. 資料混合比例(防災難性遺忘)
- **基底**:保留全部歷史資料。
- **新批 + 弱類別過採樣**:對新型態/低 Dice 類別過採樣 2–3×,使其在每 epoch 佔比 ≥ 30%。
- **GT 品質閘**:先過 GT 稽核(空GT/碎塊/疑誤標 → 人工複查或剔除;見 `GT稽核_*` 腳本)。
> 節點驗證 V2:印出訓練集類別分佈 + GT 稽核通過率(可疑張 < 10%)。

## 3. 訓練與評測門檻
- 微調 a_unet、unet++(各自),組 A∪U 機率融合。
- **固定 holdout(未見過、乾淨 GT)** 評 Dice/IoU + 逐類別。
- **升級門檻(全部滿足才部署)**:
  - 整體 Dice ≥ 前一版 − 0.005(不退步)。
  - **無任一類別** Dice 退步 > 0.02(防顧此失彼)。
  - 目標弱類別 Dice 較前版 **+0.03 以上**(有進步才值得換)。
> 節點驗證 V3:輸出新舊版逐類別對照表 + 是否過門檻(eval_harness)。

## 4. 版本治理
- 每版寫入 `model_registry`:version、sha256、來源、訓練資料快照 id、holdout 指標、日期。
- 變更紀錄(changelog)+ 可一鍵 rollback 到前版。
- 醫療變更管制:重大變更需記錄於風險檔(ISO 14971)與軟體生命週期(IEC 62304)。
> 節點驗證 V4:registry 出現新版條目且 sha 對得上部署檔;rollback 演練成功。

## 5. 回歸 CI(防退步,自動把關)
- 固定「回歸測試集」(小批、涵蓋各類別、版控)。
- CI 步驟:載入候選模型 → 跑 seg_metrics → **若整體或任一類別 Dice 較基準掉 > 門檻 → CI 紅、阻擋部署**。
- 另含:前處理一致性(`preprocess_consistency.py`)、ONNX I/O、缺模型 graceful degrade。
> 節點驗證 V5:CI 對「故意退步模型」會擋下(注入測試);對合格模型放行。

## 6. student 重新蒸餾(端上同步升級)
- 老師升級後,用 `distill_teacher_gen.py`(新老師)重產軟標籤 → `distill_train.py` 重訓 student → `distill_eval.py` 驗證 → 量化 + 轉 CoreML/TFLite。
- **student 訓練資料須含「上雲難例」型態**(大面積/身體/燒傷),才會縮小端上弱點。
> 節點驗證 V6:新 student 在 holdout(含難類)Dice 較前版升;上雲比例下降。

## 7. 節奏與誠實邊界
- 節奏:觸發式 + 每月定期;每次走 V1–V6 全節點驗證。
- 誠實:GT 必醫師驗證(非純自我標註);改善以「未見過 holdout」為準;送件數字標註資料規模與版本。
