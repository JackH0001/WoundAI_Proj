# 偽標籤蒸餾 Runbook — 拉升 student 召回

> **要解的問題**：student 對低對比／破碎傷口大幅低估——FootUlcer 0.76 vs A∪U 6.07 cm²、
> Burn 3.59 vs 9.30（EVIDENCE_LEDGER 2026-07-09）。而且**便宜推論調參已證實無效**：
> 降門檻 0.4→0.1 覆蓋幾乎不變，代表機率質量根本不在那裡，只能重訓。
>
> **目標**：retrain_merged Dice **0.762 → 0.82–0.84**（不強求追平老師 A∪U 0.924）。
> **不需要病人同意**——用的是 archive 既有的無 GT 傷口照。

## 為什麼是偽標籤，不是「多收資料再標」

現在缺的不是標註人力，是**分布覆蓋**：student 是在 retrain_merged（117 張、幾乎全足部小潰瘍）
上蒸餾出來的，身體／大面積／燒傷都算分布外。A∪U 老師在這些情境仍接近 GT，
所以「讓老師去教學生它沒看過的樣子」是最便宜的補法。

## 流程

```
① 挑無 GT 影像來源      →  ② 老師產偽標籤 + 品質把關  →  ③ 目視抽查
                                                            ↓
⑥ 走 ledger 換模型  ←  ⑤ 未見過 holdout 驗證  ←  ④ 混合重訓（防遺忘）
```

### ① 挑來源（最重要的一步，也是目前的瓶頸）

**必須是「沒有 GT、模型沒看過」的傷口照。** 有 GT 的影像產偽標籤沒有意義——
student 早就從真值學過了，老師只是複述一遍。工具會自動排除有 `labels/` 姊妹目錄的影像。

**不要**：
- **已標註訓練集**（`retrain_merged` / `retrain_bottom` / `retrain_2` / `retrain_merged_paired`）
  —— 2026-07-28 實掃 `批次驗證工具\**\image` 共 375 張，**全部都有 GT**，一張都不合格。
  其中 `retrain_merged` 就是 student 自己的訓練集，`retrain_bottom` 是評測用的最難子集（用了會洩漏）
- `test_wounds_aruco_v2` 那 5 張 —— escalate 路由的驗收基準，用了就是**考卷當講義**
- `傷口面積計測標準_Aruco_V2`（Sim01–05）—— 印刷幾何色塊，不具傷口材質
- 任何已在 holdout 或 golden 測試集裡的影像

**要去哪裡找**（依可行性排序）：

| 來源 | 量級 | 備註 |
|---|---|---|
| 臨床收案照（`clinical_pilot_20_SOP.md`） | 20 起跳，持續累積 | 最有價值：真實分布＋日後還會有真 GT |
| 公開資料集**未標註**部分（FUSC / AZH 等） | 數百 | 授權須確認（見 `woundai-smp-provenance`） |
| 歷史拍攝檔（尚未進標註流程的原始照） | ? | 先確認去識別與同意狀態 |

> 目前 archive 裡**沒有現成的無 GT 池**。這條路實質上被「先收臨床照」擋住——
> 也就是說偽標籤蒸餾的前置條件就是 20 張臨床收案，兩件事不是並行而是有先後。

```powershell
$PY = "C:\Users\jack_\AppData\Local\Programs\Python\Python310\python.exe"
$env:WOUNDAI_ARCHIVE = "C:\dev\WoundAI_weights_archive"
cd C:\dev\WoundAI_Proj\engineering\phase2

# ① 先看 --src 命中哪些目錄、各有幾張（找不到檔案時第一個跑這個）
& $PY distill_pseudo_gen.py --src "$env:WOUNDAI_ARCHIVE\批次驗證工具\**\image" --list-dirs

# ② 再看實際會收哪些檔，確認沒混進驗收基準
& $PY distill_pseudo_gen.py --src "$env:WOUNDAI_ARCHIVE\批次驗證工具\**\image" `
      --exclude labels --exclude aruco_v2 --exclude 標準 --dry-run
```

`--src` 三種寫法都行：目錄、glob 命中目錄（如 `**\image`）、glob 命中檔案。
影像埋在更深層時加 `--recursive`（預設關，免得把 `labels/` 一起吸進來）。
掃到 0 張時先用 `--list-dirs` 確認路徑，再看是不是需要 `--recursive`。

### ② 產偽標籤 + 品質把關

```powershell
& $PY distill_pseudo_gen.py --src "<來源目錄>" --exclude labels `
      --out "$env:WOUNDAI_ARCHIVE\批次驗證工具\pseudo_AU" --montage
```

把關全部是**免 GT 的不確定性訊號**（`gate_metrics`，回歸測試 `test_pseudo_gate.py`）：

| 訊號 | 門檻 | 擋掉什麼 |
|---|---|---|
| TTA 一致性（原圖 vs 水平翻轉 IoU） | ≥ 0.70 | 老師換個方向就給不同答案＝這張它其實不會（**最強訊號**） |
| 遮罩內平均機率 | ≥ 0.60 | 老師自己沒把握 |
| 邊界模糊比例（prob∈[0.3,0.7] 佔遮罩） | ≤ 0.50 | 糊成一團，沒真的分出來 |
| 面積比 | 0.5% – 60% | 雜訊點／失控吃掉整張圖 |
| 連通元件數 | ≤ 3 | 碎成一堆（通常是誤判紅色皮膚或發炎） |

**判讀通過率**：
- ≥ 60%：正常，往下走
- 40–60%：可用，但看一下退回原因是不是集中在某一類影像
- **< 40%：停**。老師在這批資料上也撐不住，硬用只會把錯誤蒸餾進學生 → 換來源，或先擴老師

### ③ 目視抽查（不可略過）

打開 `pseudo_AU/montage_accepted.png`。GT 品質的問題 Dice 看不出來、眼睛看得出來
（這專案已經吃過虧：retrain_merged 的人工 GT 本身就有錯漏）。
只要看到「遮罩明顯框到皮膚／漏掉大半傷口」的比例超過 1–2 成，回頭調門檻或換來源。

### ④ 混合重訓

```powershell
& $PY distill_pseudo_train.py --pseudo "$env:WOUNDAI_ARCHIVE\批次驗證工具\pseudo_AU" --check   # 先驗佈局
& $PY distill_pseudo_train.py --pseudo "$env:WOUNDAI_ARCHIVE\批次驗證工具\pseudo_AU"
```

- 每個 epoch **都混入有 GT 集**（`--pseudo-ratio 0.5`）→ 防災難性遺忘
- 偽標籤只計 KD 損失、權重 `--pseudo-weight 0.5` → 它沒有真值背書
- **驗證集只用有 GT 的 holdout**，偽標籤不參與評分（否則等於自己給自己打分）
- 早停 20 epoch

調參順序（只有在結果不好時才動）：先 `--pseudo-ratio`（0.3 / 0.5 / 0.7），再 `--pseudo-weight`。
ratio 太高會被老師的錯誤帶走，症狀是 val_dice 早期就停住甚至下滑。

### ⑤ 驗證（決定要不要換）

```powershell
$env:STUDENT_ONNX = "student_pseudo.onnx"
& $PY distill_eval.py        # 對比 現役student / smp / wsm / A∪U老師
```

`distill_pseudo_train.py` 印的 val_dice 是**分布內**（retrain_merged holdout），偏樂觀。
真正的判準是：

1. `distill_eval.py` 上 **> 現役 student 0.762**
2. **未見過的臨床照 ≥ 20 張**算 GT-Dice（20 張臨床收案跑完就有了，見 `clinical_pilot_20_SOP.md`）
3. 目視 5 張難例（FootUlcer / Burn 那類）遮罩是否真的補全——這才是當初要解的問題
4. escalate 觸發率不應暴增（student 變強 → 觸發率應該下降）

**任一項沒過就不要換。**

### ⑥ 換模型（走治理流程）

1. `EVIDENCE_LEDGER` 新增一列：日期／決策／證據（資料集・N・指標）／推翻了誰／出處
2. 更新 `engineering/phase0/test_ssot_golden.py` 的釘值
3. `model_registry` 版本與 sha256、`routing_policy` edge_model
4. FP16 量化 → 四端換檔（Android assets / iOS mlmodel / Windows / Backend models）
5. 回歸：`test_ssot_golden.py`、`test_escalate_routing.py`、`test_dual_track_integration.py`

## 誠實邊界

- 偽標籤**上限就是老師**。A∪U 錯的地方，學生只會學得更像 A∪U 的錯。
  要突破老師需要真 GT——那條路是 20 張臨床照＋飛輪持續累積。
- 通過把關 ≠ 正確，只代表老師「穩定且有把握」。系統性偏差（例如老師一貫低估邊緣）
  會原封不動傳給學生，而且**沒有任何免-GT 訊號抓得到**。
- 本流程改善的是**召回**（少漏），不保證面積精度。面積精度由 ArUco 尺度鏈決定，與模型解耦。

## 相關檔案

| 檔案 | 用途 |
|---|---|
| `engineering/phase2/distill_pseudo_gen.py` | 老師產偽標籤 + 品質把關 + 目視抽查 |
| `engineering/phase2/distill_pseudo_train.py` | 混合重訓（有 GT ∪ 偽標籤） |
| `engineering/phase2/test_pseudo_gate.py` | 把關規則回歸（8 項） |
| `engineering/phase2/distill_teacher_gen.py` | 有 GT 集的老師軟標籤（前置） |
| `engineering/phase2/distill_eval.py` | 對比評測 |
| `engineering/phase2/distill_export.py` | .pt → ONNX |
