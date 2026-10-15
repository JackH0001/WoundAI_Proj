# phantom 狀態顯示覆核（2026-10-16）

## 結論
主控台把 classify_queue 的相容狀態碼 trainable 翻譯為「可訓練」。該碼代表通過有效版本、同意、撤回、影像與保存收據等佇列檢查，不代表通過指定研究用途、組織修訂與品質篩選。phantom 或低品質標註出現此碼，不證明模型已使用它訓練。

## 匯出邊界
- /api/v1/dataset/manifest 預設 source=clinical，另有品質與組織修訂篩選；phantom、sample 預設不納入。
- 明確指定 source=phantom 或 source=sample 可選取該來源。
- export_flywheel_dataset.py 未指定 --source 時預設所有來源；export_tissue_dataset.py 的離線 fetch_local 亦可不限來源。不能宣稱測試資料已被所有工具全面排除。
- 本輪未執行訓練、下載資料集或修改雲端紀錄。若要禁止特定來源進模型，仍須統一各匯出／訓練入口的用途規則；這是另項行為變更，並非本次顯示修正。

## 本機修正
API 中文標籤改為「有效標註（待篩選）」；保留 trainable 相容碼。主控台取消綠色訓練資格暗示、增加 phantom／sample 用途說明，Dashboard 同步修正。
不改 MMH 接受來源、不動原始資料、排除標記、同意或實際匯出條件。符合既有 MMH 特案不新增來源分級限制的決定。

## 本輪驗證
- test_tissue_dataset.py：51/51，包括新增 10 項來源與品質／狀態分離測試。
- test_retract_and_records.py：46/46，撤回、排除、帳號與範圍限制回歸通過。
- test_console_js.py：JavaScript 解析通過。
- git diff --check 通過。

狀態：本機修正，尚未建置部署；線上目前仍可能顯示舊文案。部署新示範入口所沿用的既有映像不包含此修正。
