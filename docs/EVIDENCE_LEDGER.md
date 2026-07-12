# 證據帳本（Evidence Ledger）

> 規則：任何前處理／門檻／模型選型決策變更，**必須**先在此新增一列（附資料集、N、指標、日期），並同步更新 `engineering/phase0/test_ssot_golden.py` 的釘值。CI 會擋下未走此流程的 SSOT 改動。背景：wsm 前處理曾三度翻案（每次都只改一處文件），本帳本＋golden 測試就是為了終結此類事故。

| 日期 | 決策 | 證據（資料集/N/指標） | 推翻了誰 | 出處 |
|---|---|---|---|---|
| 2026-06 | wsm 前處理＝[0,1] BGR @224 thr0.5 | 人工 GT n=3，GT-Dice 0.786（vs [-1,1]BGR 0.508 / [0,1]RGB 0.325 / [-1,1]RGB 0.189） | Sprint G 的 [-1,1]BGR@0.30 主張；更早的 [-1,1]RGB 記錄 | preprocessing.json `_fix_2026-06a` |
| 2026-06 | smp＝平台廣域分割（imagenet/RGB/NCHW/256/thr0.3） | 臨床照 n=3：0.737→0.800(TTA)→0.855(×FUSeg 集成)；retrain_bottom 最難子集 0.873 | — | segment.py docstring、model_registry |
| 2026-06 | 行動端主力＝student（蒸餾, thr0.4） | retrain_merged Dice 0.762（>老師 0.698、smp 0.737）；FP16 13.3MB | wsm 端上部署（降為 legacy/判難參考） | model_registry `segmentation.student` |
| 2026-06 | 雲端難例＝A∪U 集成（0.5/0.5, thr0.4） | 未見過臨床照 n=5 Dice 0.924；雙軌路由後 0.900（雲端僅跑 3/5）；回歸守門 ≥0.88 | — | dual_track_routing_spec、model_registry |
| 2026-07-07 | routing_policy edge_model＝student | 對齊 registry deploy 宣告與雙軌 spec | edge=wsm(legacy) | PR #1 |
| 2026-07-07 | DetectROI 紅帶 S 下限 40→100 | 合成傷口測試：膚色 S≈73 全圖誤判；傷口 S≈188／壞死 S≈128 正確分離（12/12 測試過） | S≥40 舊門檻 | PR #1、源頭 windows-client run |
| 2026-07-09 | 後端 classify 自動 escalate（student→A∪U；觸發＝au/student 最大輪廓面積比>1.5 或 IoU<0.5；可用 `escalate=off` 關閉） | v2 貼紙測試圖 n=5：正確**保留 student** Bedsore_01(比1.02,IoU0.87)/Bedsore_02(0.99)/image008(1.34)，正確**escalate** Burn(2.59)/FootUlcer(8.00,IoU0.25)；證實**單看 student 自身訊號不可靠**（紅色 proxy 被膚色/發炎誤導：Bedsore_01 比0.19、image008 0.10 皆假難例；碎片數只中 FootUlcer=4、漏 Burn=2）→ 判難須用「第二意見」 | 「便宜推論調參救難例」的假設（診斷：student 漏 segment 區域機率≈0、降門檻0.4→0.1 覆蓋幾乎不變、面積僅 +0.17/+0.27，救不了） | app.py `classify_wound`（本 session）、test_wounds_aruco_v2 |

**待補證據（未決事項）**：Sprint G-3 決定性評測（統一資料集 AZH∪內部GT 跑 4 前處理組合×2 尺寸，n≥100）；smp「主力」宣稱擴大樣本數（現僅 n=3 臨床照 vs AZH n=166 顯示 fusegnet 較強——疑為領域差異，須驗證）。

**待辦訓練任務（中期，非阻擋）**：
- **偽標籤蒸餾拉升 student 召回**（優先度：中）。動機：n=5 測試圖顯示 student 對低對比/破碎傷口大幅低估（FootUlcer 0.76 vs A∪U 6.07、Burn 3.59 vs 9.30），且**便宜推論調參證實無效**（機率質量不足）；現靠自動 escalate 兜住,但端上離線時仍弱。作法：以現成 **A∪U 集成當老師**,對大批**無 GT** 傷口照產生偽遮罩→重訓 student（soft-label 蒸餾＋一致性正則）。目標 Dice **~0.82–0.84**（現 retrain_merged 0.762；不強求追平 A∪U 0.924）。驗收：未見過臨床照 n≥20 GT-Dice、以及 5 張難例目視遮罩補全。走 EVIDENCE_LEDGER 流程並更新 golden。
