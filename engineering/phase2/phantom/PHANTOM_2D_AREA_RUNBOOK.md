# 2D 面積 Phantom 驗證 Runbook（WoundAI 2D）

> 目的：拿到校正貼紙＋已知面積標準件＋手機後，照表收數據 → 跑 `phantom_validation.run_from_manifest` → 產 area_err 報告，作為 IRB Phase B 的 pre-clinical evidence。
> 對齊 WoundAI3D 既有 `PHANTOM_VALIDATION_PROTOCOL.md` 嚴謹度，但**只驗 2D 面積**（不含 LiDAR 深度／色彩 ΔE）。
> 誠實前提：2D 量測精度尚未實機驗證，本 runbook 是把「宣稱」變「數據」的第一步。

## 0. 前置 checklist

硬體
- [ ] 固定一支高階手機（建議 iPhone Pro；2D 面積不強制 LiDAR），整批用同一支同機型。
- [ ] 拍攝距離治具／三腳架（固定 20cm、30cm）、不反光霧面襯板。
- [ ] CRI≥90、5000K 光源 ＋ lux meter（500–1000 lux、無直射光）。
- [ ] 數位卡尺。

校正貼紙（已有設計）
- [ ] 20mm 方形（棋盤）＋20mm 圓形；**caliper 實測**真實尺寸並記錄（填入 `sticker_mm`），每張有 serial。

2D 面積標準件（phantom 本體）
- [ ] 已知面積平面形狀：圓／方／仿傷口不規則輪廓，跨量程（建議 1、2、4、8、16 cm²）。
- [ ] 精密印製／雷射切割；**真實面積以掃描器或 caliper 驗證並附公差**（＝ ground truth，填 `true_cm2`）。
- [ ] 建議印仿組織色（紅／黃／黑）以同場驗 Track B 分類。
- [ ] 每件 serial＋QC 報告；到貨抽驗 10% 尺寸。

## 1. 量測環境
- 標準件＋校正貼紙**同平面、平鋪於襯板、同框入鏡**（貼紙不可在不同平面，否則透視校正失準）。
- 固定光源與距離；每次記錄 lux。

## 2. 拍攝矩陣（建議）
每件 × 角度(0°、30°) × 距離(20、30cm) × 操作者(2–3 人) × 重複 → 例如 30 件 × 6 次 = n≈180。
- 最小起步：1 張貼紙＋5 件（1/2/4/8/16 cm²）× 0°/30° = 10 張，先出第一份基準。

## 3. 每張要產生的檔案
1. 原始影像 `images/<name>.jpg`
2. 傷口/形狀 **GT 遮罩** `masks/<name>.png`（白=前景；精密形狀本身即 GT，或人工標註）
3. 在 `manifest.csv` 補一列（見 `manifest_template.csv`）：
   - 必填：`name, image_path, mask_path, true_cm2`
   - 校正：`sticker_x0,sticker_y0,sticker_x1,sticker_y1`（框選貼紙四角的外接框）＋實測 `sticker_mm`
   - 中繼：`distance_cm, angle_deg, operator, device, lux, notes`

命名規範建議：`phantomSerial_形狀_面積_角度_距離_operator`，例 `ph001_round_2cm_30deg_25cm_A`。

## 4. 執行分析
```bash
cd engineering/phase2
python -c "import phantom_validation as pv, json; \
print(json.dumps(pv.run_from_manifest('phantom/data/manifest.csv', out_csv='phantom/data/area_err_report.csv')['summary'], ensure_ascii=False))"
```
產出：`area_err_report.csv`（逐張 measured_cm2 / area_err% / method＋中繼）＋摘要（n、mean/max area_err%）。

## 5. 通過判定（先定門檻）
- 例：校正後 **mean area_err% < X%**、max < Y%（X/Y 由臨床顧問與法規共同設定）。
- 對照組：可另跑未校正（無貼紙）對比，量化「校正帶來的精度提升」。
- 報告納入 IRB Phase B pre-clinical evidence 與 investor deck。

## 6. 注意
- 貼紙四角框選（assisted）為目前最可靠；自動棋盤偵測為輔。
- area_err 指標定義與 `eval_harness` 一致；透視校正由 `geometry.py` 處理。
- 不偽造：缺校正貼紙的影像，分析器回 `measured=None`、不計入面積誤差。
