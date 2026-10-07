# WoundLite 研究封存、成本與循環保留計畫

更新：2026-10-05（Asia/Taipei）。Jack 已授權低成本處理、定期下载與制定清理排程。本文件區分已執行與待完成的技術條件；不是新的研究同意書，也不延長原同意允許的用途或保存期間。

## 已執行及證據

- 專案固定 `woundai-jackh001`。只讀舊桶 `woundai-flywheel-jackh001` 的 Lite media、index、labels；沒有讀取臨床病患前綴。
- 盤點 147 個現行物件、16.7994 MiB。31 份影像 metadata 中，16 筆符合本輪同意／撤回篩選，15 筆排除；ledger 有 2 個已撤回安裝代碼。非空 consent_version 只代表可封存待審，並非已證實同意內容足以研究利用。
- 本機內容定址封存：16 筆、64 個邏輯資產引用、50 個去重檔案，共 16,339,555 bytes（15.5826 MiB）。本輪新增資產下載 16,382,077 bytes；傳输數不含另外讀取的 metadata 與 ledger，因此不能當成全部網路流量。
- 50 個本機檔案 SHA-256 全數讀回通過。重跑 64 個引用均可沿用，新增資產下載 0；metadata／撤回 ledger 仍重新讀取。隨後 `--reconcile-only` 成功。
- 16 組深度與信心 PNG 均能解碼：11 組 768×576、5 組 576×768。深度為 16-bit、信心為 8-bit；metadata 有 camera_intrinsics、depth_format、depth_scale。這不是 raw RGB-D 完整性或座標／尺度正確性的證明。確認完整原始 RGB-D：0 筆。
- 自動測試 **14/14**：重跑、同意排除、撤回、共用檔案、來源缺失、generation 改變、下載競態、損毀、路徑、未知 ledger action、部分清單失敗及磁碟不足。
- 檔案：`tools/lite_research_archive.py`、`tools/test_lite_research_archive.py`。
- 證據在 repo 外 `../woundai-competitive-review-20261003/lite-research-retention-20261005/validation.json` 與 `tests.log`。資料本身不進 Git，不貼在協作訊息或公開網站。

## 本機保存與撤回

封存位置：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-research-vault`。已核對 FileVault 開啟；資料夾 0700、資料檔 0600。未另啟用雲端同步或分享；作業系統／使用者既有備份是否涵蓋此資料夾需另外盤點，不能宣称所有備份均已排除。這仍是假名資料，不是不可逆匿名。

- 以物件 generation 固定讀取、CRC32C 傳輸核對、SHA-256 本機核對；讀取失敗不改用歷史版本。
- 全部 records 維持 `training_admission=quarantined`、`training_release_allowed=false`。本工具不提供「放行訓練」開關。
- 每次先讀取同意與撤回，再下載，完成前重讀 inventory 和 ledger；若來源中途改變則停止發布本輪 catalog。
- 已撤回、同意不再成立、雲端 metadata 消失的資料，於成功核對後移除本機記錄與未再引用檔案；共用內容只有在無其他有效引用時移除。
- 每日核對是最終一致，**不是即時同步撤回**。Mac 睡眠、離線或憑證失效可能延遲；失敗時既有副本仍為隔離狀態，不得研究放行。未來每次研究匯出都必須重新核對，不可只相信昨日摘要。
- 刪檔不等於 SSD 實體覆寫；若再複製到外接碟、Time Machine 或第三方雲端，必須納入撤回清單。此次沒有另外建立第二份獨立備份。
- 保留至少 30 GiB 可用空間；盤點上限 10,000 物件、24 MiB/物件、5 GiB/整體來源範圍。目前這些是小規模試行上限；超限停止並提示，**不會靜默漏抓**。這不是可無限擴張的資料倉庫。

## 排程與日期

已建立此對話的 heartbeat，ID `woundlite`，ACTIVE；台北時間每天 03:00。依賴 Mac、Codex 執行環境及 GCP 登入可用，並非保證準時的雲端排程服務；没有新增 Cloud Scheduler／常駐運算資源。Codex 自動化仍使用帳號用量。

| 時間 | 工作 | 執行狀態 |
|---|---|---|
| 2026-10-05 | 首批下載、重跑與撤回核對 | 已完成 |
| 每天 03:00，下一排程 2026-10-05 | 增量封存並核對撤回；只下載新增或改版資產 | 2026-10-05 03:02 首跑成功；新增資產下載 0 |
| 每週一 03:00 | 額外核對本機全量內容雜湊 | 排程已建立 |
| 每月 5 日；下次月度覆核 2026-11-05 | 檢查容量／費用、30 天以上雲端候選、365 天以上本機資料之保留必要性 | 只產生候選，不自動刪研究來源 |
| 2026-11-04 00:49／00:51 起 | 本次建置 source／log 滿 30 天後清理 | **GCS lifecycle 已設定並讀回**；非保證完成時刻 |
| 每筆本機資料保存滿 365 天 | 覆核研究必要性與原同意期限 | 待審期限，不是自動延長同意或到期必刪；原同意更短則優先 |

## 已上線的雲端清理規則

僅作用於專用桶 `woundai-lite-build-421209514056` 的這兩個物件：

- `source/lite-16abf4b8a7da3251-20261005.tar.gz`（76,251,335 bytes）
- `log-bf8aef7b-2992-4932-9026-d833b4928155.txt`（47,016 bytes）

兩份均已與本機 SHA-256 核對；source 也符合既有來源 manifest。規則同時限定完整物件名稱 prefix/suffix、`createdBefore=2026-10-05`（UTC）與 `age=30`。更新以 bucket metageneration 作並行變更檢查，更新後讀回一致。該桶無 versioning、soft delete 為 0；本次沒有立即刪除物件。證據：`cloud-build-20261005/retention/`。未設定全桶通用刪除，也不影響未來建置。

GCS lifecycle 非同步執行，11/4 是符合條件的起日，不是保證刪完日期。[Google lifecycle 文件](https://docs.cloud.google.com/storage/docs/lifecycle)

## 研究來源循環保留的落地順序

目前雲端仍保留原始來源，這是沒有第二份獨立備份時的恢復來源。下載完成後直接刪除，會把唯一可靠的雲端版本移除，也可能讓 App 後續修訂失敗。本輪找到 4 筆至少 30 天未改動的候選，**沒有刪除**。

下一階段採「至少 30 天線上熱資料，較舊且已驗證封存者才可轉離線」設計；正式生效前須完成：

1. 每筆 capture 具備封存收據與所有必要檔案、雜湊、同意版本、來源品質及最終修訂。可撤回的一般使用者圈選不能當醫師 GT。
2. 第二份受控備份與可復原驗證；禁止只剩這台筆電一份。第二份位置目前待 Jack 指定。
3. 具備 capture retirement 狀態，讓 App 能分辨已封存與上傳失敗；後續改遮罩／資料更新仍有明確處理。**現有工具會將雲端 metadata 消失視為不可留存，所以尚不能與「備份後刪來源」串在一起使用**；需要先實作可驗證的封存狀態來源。
4. 來源、封存副本、第二份備份及未來訓練匯出都有撤回索引。新 Lite 桶的零位元組封鎖標記不可依日期清除，否則遲到寫入可能復活。
5. 舊共享桶有 versioning／7 天 soft delete，且包含不同資料種類；不能使用全桶 Delete age。新 Lite fenced 桶的既有政策目前也不允許一般 lifecycle。兩者都需要由應用程式按 capture 狀態清理，不能套同一條桶規則。

以上是實作缺口，不是再次要求使用者授權一般低成本清理。醫療、稽核紀元與法定／研究保存責任資料不在本次循環刪除範圍。

## 成本判斷與預算

当前 16.7994 MiB，以 US$0.02/GiB-month 假設計算，現行物件純儲存約 **US$0.00033/月**；不含歷史版本、soft-delete、請求及流量，也不是全專案帳單。現階段直接保留 Standard 並做增量副本，比增加跨雲自動化或冷層搬移更簡單。

沿用預算報告的 1,000 DAU、每天一次、每次 5 MiB 情境：每月新增 146.48 GiB，下載一次以 US$0.15/GiB 混合規劃價約 **US$21.97/月**。同樣資料在 Standard 每多存一個月約 US$2.93。故下載本機主要解決研究可用性與備援；**不是立即省下所有費用**。外網下載價依目的地／級距不同，不能當成固定報價。[官方 GCS 價目](https://cloud.google.com/storage/pricing)

| 情境（1,000 DAU，每人每天一次） | 既有基準估算 | 加入一次增量外網下載預留 |
|---|---:|---:|
| 第 1 月，持續保存來源 | US$21.76/月 | 約 US$43.73/月 |
| 第 12 月，持續保存來源 | US$53.99/月 | 約 US$75.96/月 |
| 未來穩態僅保留 30 天來源 | 尚未啟用 | 約 US$46/月起，另加備份成本 |

上述最後一列由基準第 1 月平均半月儲存調整成 30 天來源（加約 US$1.46），再加下載費，約 US$45.20；向上取整 US$46。不含增長 ledger／版本、封存服務、外接硬碟、付費支援、稅、3D 訓練及既有醫療/demo服務。不能把它視為上限。資料若無第二份備份而繼續存在 GCS，第 12 月應使用約 US$75.96 的欄位。另保留較大影像／較慢推論壓力情境，見既有預算 PDF。

這台 Mac 約剩 61 GB，無法承接上述每月 146 GiB 規模；應在小規模上架前指定受控外接儲存或研究運算所在地。若訓練仍在同區 GCP，可只下載必要驗證子集，把完整研究資料留在同區受控儲存，避免大量來回搬運。

Standard 沒有最低保存天數；Nearline/Coldline/Archive 分別有 30/90/365 天最低計費與讀取費。可隨時撤回或常修改的資料，不宜只看每 GB 低價就移往 Archive。[官方價目與提前刪除說明](https://cloud.google.com/storage/pricing)

運算成本以新 Lite 服務 `min-instances=0`、CPU 推論、不開 GPU、不每日重建映像、不因備份重跑模型為方向；**目前尚未部署新 Lite 服務**。醫療 demo 的 LocalStore 與審查連續性仍需處理，這次未為省待命費而改它的執行個體設定。

## 執行方式與剩餘驗收

```sh
cd /Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-ios-release-20261001
python3 -B tools/test_lite_research_archive.py
/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-review/venv/bin/python -B tools/lite_research_archive.py --vault /Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-research-vault
```

只做撤回核對可加 `--reconcile-only`。這個模式不下載新影像，來源改版會標記 `source_unchanged=false` 等下一次封存；不代表新版已驗證。

仍待：第二份受控備份、正式研究保存政策、capture retirement、新 Lite raw RGB-D／Apple App Attest／GCS／實機端到端驗收。此工具是小規模舊 Lite 資料封存，不等於公開研究平台已完成。

## 2026-10-05 03:02 首次排程驗收

腳本與測試 SHA-256 與前次驗證相同，14/14 通過；GCP 增量封存成功，16 筆／50 檔不變，64 個資產引用沿用、新增資產下載 0、本機移除 0。週一全量 SHA-256 50/50 通過。月度覆核：至少 30 天未改動的雲端候選 4 筆、本機保存滿 365 天檔案 0、淨新增容量 0；可用空間 56.84 GiB，高於 30 GiB 保留門檻。全部維持隔離；未刪除任何雲端物件。證據：`lite-research-retention-20261005/heartbeat-0301/validation.json`。
