# WoundSegLite 地端分割模型規格（保留槽）

狀態：**槽位已鑄好，等模型成熟上機**（2026-08-18 規劃）。
本目錄目前刻意只有這份規格；`.mlmodel` 檔一放進來、跑一次 `xcodegen generate`
即編譯進 WoundLite bundle，`LiteLocalSeg.available` 轉真，
輪廓來源優先序（地端 → 雲端 → 手動）自動啟用地端路徑。

## 檔名約定

`WoundSegLite.mlmodel`（或 `.mlpackage`）——`LiteLocalSeg.available` 查的是
編譯產物 `WoundSegLite.mlmodelc`，改名要同步改該處。

## 模型 I/O 契約（建議值，訓練端可調後回寫本檔）

- 輸入：`image` — 512×512 RGB（縮放置中、ImageNet 正規化與否由訓練端決定並註記）
- 輸出：`mask` — 512×512 float 機率圖（單通道，傷口=1）
- 後處理（App 端已寫好走法，見 `LiteLocalSeg.segment`）：
  閾值 0.5 → 二值遮罩 → `MaskTrace.traceAllBoundaries`（minPx 64）
  → `MaskTrace.rdp(eps: 1.5)` → 座標映回原影像空間
  → `centerWound` 取畫面中央單一傷口 → 使用者可再手動微調

## 訓練資料來源

醫療版訓練標註（醫師修邊 GT）＋ Lite 研究同意上傳（`docs/lite_backend_contract.md`）。
與後端 seg 模型同族裔、蒸餾/量化到行動端體積（目標 <30MB）。

## 驗收門檻（上機前）

- Phantom 印刷樣張：與雲端 seg 的 IoU ≥ 0.90
- 臨床照（醫師 GT）：mIoU ≥ 0.85
- iPhone 12 Pro 推論 < 1 秒
