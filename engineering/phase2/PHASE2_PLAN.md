# Phase 2 規劃：真實分割模型接線 + 分類缺口務實補法

> 前置（已驗證現況）：分割最佳模型 UNet-256 保留集 Dice 0.7732/IoU 0.6787，但面積誤差 37%、分布外 Dice 0.5556；**分類三模型（wound_type/severity/tissue）檔案全缺**。
> 原則延續 Phase 0/1：**缺模型不偽造輸出**、AI 預設 feature-flag OFF、可靠幾何/規則先行、模型權重私有（協作 repo 只放 stub + 契約）。

## Track A — 半自動分割模型接線（把真實模型接進資料飛輪）

| # | 工作 | 驗收 |
|---|---|---|
| A1 | ModelRegistry 接真實 seg 模型（wsm.onnx / FUSegNet512），feature-flag 控制、缺檔 graceful degrade（骨架已備） | 有模型→啟用；缺檔→回退可靠幾何，CI 不破 |
| A2 | 推論初稿取代修邊 UI 目前的 stub 方塊；前處理走 SSOT（[-1,1] BGR thr，已驗證跨端一致） | 真實影像在 UI 跑出 AI 初稿 |
| A3 | 邊緣／雲端推論路徑分離，輸出含信心值與「需醫師確認」標記 | 兩路徑皆可、輸出帶 confidence |
| A4 | 端到端閉環：影像→初稿→醫師修邊→annotation record→再訓練佇列；eval_harness 量修邊前後 IoU | 產出 per-image report，correction_iou 落地 |

## Track B — 分類模型缺口務實補法（目前 0 個分類模型）

| 階段 | 作法 | 風險控管 |
|---|---|---|
| B1 短期 | 唯一啟用路徑＝規則式可解釋嚴重度/分類（clinical_rules scorecard 已備）；AI 分類 flag OFF | 永不宣稱黑箱分類能力 |
| B2 資料優先 | Phase 1 資料引擎在修邊同時蒐集「類型/組織」標註，累積至門檻量 | 標註品管、去識別化 |
| B3 過渡 | 以分割輸出之幾何＋組織比例特徵 + 規則 → 粗分類（可解釋），標示輔助＋信心＋需醫師確認 | 缺資料時回「待醫師判讀」 |
| B4 後期 | 資料足夠後訓練輕量分類模型（工具鏈非中國廠牌），ModelRegistry graceful degrade 接入 | 缺模型→回 B1/B3，不偽造 |

## 共通驗收門檻
- CI 7 步守門全綠；新增模組附單元測試。
- eval_harness 對接真實模型後產出 per-image 報告；面積誤差為主要追蹤指標。
- 合規：核心技術/關鍵零組件/工具鏈非中國大陸廠牌；模型權重不進協作 repo。

## 建議起手
A1 + A2（讓修邊 UI 吃真實初稿）槓桿最高——直接啟動資料飛輪、把「宣稱」變「實測」。B 線以 B1 維持安全上線、B2 同步累積資料。
