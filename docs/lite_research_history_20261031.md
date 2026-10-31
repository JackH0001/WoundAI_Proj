# Lite 研究媒體歷史版本處置：本機合成驗收

日期：2026-10-31。父來源：`ac610dd3591d8dd37cfa7a25559c83968420a5e2`。
這是尚未掛接的私有元件；沒有新增 HTTP、環境啟用選項、排程、IAM 或部署。
未刪除真正研究資料、原始拍攝檔或金鑰。完整帳號刪除仍未完成。

## 解決的缺口

前一版完成研究媒體目前版本的分頁封鎖，但原物件的歷史 generation 仍存在。
新 `ResearchMediaHistory` 只處理已被原帳號關閉流程確認、已完成目前媒體處置的物件。
它先核對凍結的帳號／裝置清冊、原始退休紀錄、同意批次遍歷證據、每個物件的
intent/outcome，以及仍存在的空白封鎖 generation，再選定較舊版本。

缺少或矛盾的證據一律停止；不呼叫會建立退休紀錄的 helper 補造證據，不依字首猜歸屬。
jobs/audit 必須與既有帳號、研究狀態及媒體命名空間分離。

## 操作與失敗恢復

- `advance(person, operation, limit=20, apply=False)`：單一已確認物件；預設唯讀。
  每次至多選 50 個舊版本，active 與 soft 列表各只讀一頁；不清目前封鎖版本。
- `advance_account(person, versions=20, apply=False)`：每次只推進一個清冊階段、
  選一個物件，或處理該物件的一批版本。預設只讀既有 checkpoint，**不是全帳號預覽掃描**。
- 原始 exact generation／大小／CRC 清冊及稽核先保存，才發出限定 generation 的刪除。
  回應遺失時讀回同一 generation，不能改刪最新版；回執遺失仍從原清冊補核對。
- 每次可重建 worker 續接。兩個 worker 以 CAS 參加同一批工作，不另建相互競爭的清冊。
- 完成回執是該輪觀察。`rescan=True` 僅能在完成後明確開始新一輪，例如等待 soft delete
  保留期過後再核對；不自動重啟進行中的掃描，也不把舊觀察改寫成新的永久保證。
- 每次件數有上限，不代表總耗時有硬上限。正式執行器仍需全程截止時間、取消、
  重試預算、讀取成本與告警驗收。

共用 exact-generation 邏輯抽至 `lite_generation_history.py`；原 `ServiceResultHistory`
保留原 API、儲存 key、回執及大小限制。研究媒體允許零位元 marker 與上限 24 MiB 的
原媒體；服務結果仍沿用自己的狀態大小限制。未擴大任何 runtime 打包允許清單。

## 相同合成資料的修正前後比較

同一 probe 複製到父來源和本輪來源執行。使用 3 個同意批次、每批 7 種物件，
每個物件 3 個舊版本；另保留其他帳號物件、原始 capture、額度及同意稽核快照。

| 項目 | 父來源 ac610dd | 本輪本機來源 |
| --- | --- | --- |
| 21 個已確認物件的舊 active versions | 63 → 63 | 63 → 0 |
| 每次版本刪除上限（此 probe） | 沒有歷史處置元件 | 2；66 次續接完成 |
| 目前空白封鎖 marker | 保留 | 保留 |
| 其他帳號、原始 capture、同意／額度稽核 | 不變 | 不變 |
| 晚到的舊寫入 | 拒絕 | 仍拒絕 |
| 啟用 soft delete 的對照 | 舊 active versions 留存 | 63 個 soft versions 仍留存，21 個 slot 明列待處理 |

模型呼叫 0；真實 GCS／Apple／KMS 呼叫 0。probe 中一個刻意無法確認歸屬的 grant
始終列為 unresolved，不因此宣稱全帳號處置完成。

## 測試與重跑

本輪新增 38 項；研究歷史、原服務歷史、前版研究媒體合計 122 項重點回歸。
全套 Lite 與本機 prelaunch 重播的最終數字、日誌及校驗值見本輪覆核包。
測試集合有重疊，不能加總當成獨立測試數；本機重播不是遠端 GitHub CI。

覆蓋：65 個版本分批、13 個已確認 installation、3 個同意批次、全部 7 種物件、
空頁／短頁／循環、原始證據缺失、回覆遺失、稽核或 checkpoint 寫入失敗、兩個 worker
競爭、目前封鎖版本變動、保留鎖／hold、錯誤 metadata、soft inventory 不可用、
explicit rescan、閒置無新增稽核。Google SDK query 測試使用 mock transport，不是實際 GCS。

沿用 `.github/workflows/lite-prelaunch.yml` 的依賴與 PYTHONPATH：

```text
python -m unittest -v test_lite_research_history test_lite_service_history test_lite_research_media_disposition
python tools/lite_research_history_probe.py --output replay.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

## 不代表已完成的項目

1. 完成觀察限於有原始處置證據、目前仍有永久封鎖 marker 的物件；沒有現存 marker／
   原始證據的孤立歷史版本，不會由這個 scanner 擅自發現及刪除，需要獨立盤點與審核。
2. 無法關聯的 installation、soft-deleted versions、備份、保留鎖和其他資料類別仍待處理。
   `account_deleted`、`physical_backups_erased` 始終為 false。
3. 沒有接上公開刪帳回執、原生啟動或正式排程。原一般使用者獨立撤回的全量列舉不在本輪修改。
4. 舊服務歷史元件只有共用核心抽取；未新增其自動啟用或廣泛桶權限。
5. 尚未執行 live GCS／Apple Sandbox／KMS／實機驗收。部署、資源、實際資料清理、
   金鑰銷毀及商店更新都須另行固定方案授權。前一版本 CI 不可充當本輪 CI。

Claude 覆核請優先看：原始證據鏈的唯讀驗證、單一 generation 競爭、歷史掃描範圍限制、
目前封鎖版本保留、soft delete 未誤報永久清除，以及共用抽取是否改變服務歷史語意。
