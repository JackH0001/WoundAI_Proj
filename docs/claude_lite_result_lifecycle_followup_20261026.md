# Claude 覆核：Lite 服務結果生命週期 — 2026-10-26

接續本機 `dbb47d1a22399ac7c66adbdff08789fe8ded1365`。本輪補齊的是
**獨立服務結果的受限壓縮與回執核對**，不是病患資料清理，也不是正式帳號／UI 上線。
未部署、未推送、未新增雲端權限、未改研究原始資料與稽核、未更新 App／商店。

## 改變及工程證據

之前 24 小時只有讀取期限，結果多邊形仍保存在操作資料內。現在新增
`ServiceResultRetention`：明確指定單一服務操作、來源版本與雜湊，過期後先保存
稽核意圖，再核對／完成 ledger 終態，最後把目前結果資料轉為 v2 終態摘要，保留
原操作 ID、日、影像／模型／結果雜湊及原回執。完成稽核也必須寫入並讀回才算完成。

- 已結算：移除目前結果多邊形，不改原成功次數與回執。
- 結果已保存、結算回覆遺失：用相同結果／worker 結算，再壓縮，不再叫模型。
- 未開始：以 CAS 取消預留；嘗試次數、grant、票據與取消證據保留。
- 正在執行但沒有結果、沒有 quota entry、資料損壞或版本不符：停止，不猜測退款或重跑。
- claim／cancel 真正雙執行緒競爭：僅一方成功；claim 贏時原工作保持。
- 六個同方案執行器同時處理：只保留一個終態及兩份稽核，不產生多次結果。
- intent、cancel、settle、結果 CAS、completion 各階段回覆遺失皆有故障注入；
  同方案明確續接會讀回已完成步驟，不覆寫來源或新增辨識。
- 研究撤回與壓縮交錯：研究狀態仍維持撤回；服務模組沒有研究寫入／刪除介面。
- 壓縮後跨日再 prepare／run／status 維持 410，不回傳 unknown，也不建立新日操作。

## 本輪實跑

| 範圍 | 結果 |
| --- | --- |
| 新增狀態／競爭／故障／資料保留測試 | 38 通過 |
| 新增 GCS adapter 操作與大小探針斷言 | 2 通過 |
| 後端、原生契約及既有撤回／修訂／深度／配額回歸 | 合計 430 通過，包含上述 40 項 |

測試全部為合成資料。SQLite 使用實際持久化及 CAS；GCSStateStore 是實際 adapter
搭配 fake transport，沒有連 GCS。原生 fixture 與三條簽署路徑的契約回歸已包含在
430 項中。這輪沒有改 Swift，不重跑上一輪已通過的 iOS Debug／Release，也不把
它們列為本輪新測試。沒有實機或真實 Apple Sandbox／廣告驗收。

Linux 手動 CI 已加入兩個測試模組及探針；只完成本機語法／執行核對，未推送或
觸發遠端 Actions。完整測試列表與日誌在本機覆核包中。

## 操作與成本意義

實際 adapter／假傳輸探針，從規劃到完成：

| 情境 | 條件寫入 | metadata＋content 讀取 |
| --- | ---: | ---: |
| 已完成結果 | 3 | 20 |
| 尚未開始的預留 | 4 | 24 |
| 同一完成方案再次查回 | 0 | 見 JSON 探針 |

小遮罩例：目前物件 684 → 1,046 bytes，另新增 1,394 bytes 稽核；所以不一定省空間。
256 點合成輪廓例：目前物件 4,214 → 1,046 bytes，仍另有稽核與歷史版本成本。
探針刻意保留並核對舊 GCS generation，證明**壓縮目前結果不代表完整物理刪除**。
本輪未更新金額費率或宣稱收支平衡。實際運算、回應流量、留存版本、廣告填充率及
付費者用量成本仍需日後受限雲端與 Apple／廣告驗收。

## 重要覆核點及未結案

1. v2 reader 需要先於壓縮執行器部署；不可回復到只讀 v1 的版本後，透過重建舊結果或
   清空 quota 解鎖。舊 reader 會封閉失敗；目前未做雲端遷移／回復。
2. plan 固定來源 generation/hash、scope／usage namespace、600 秒期限。它不是權限；
   日後控制端還需核對實體桶／prefix、最小權限、exact plan digest 與整批資源上限。
3. audit 只 create-only；不能因已有 conflicting intent 就覆蓋。存取不明或 lost ack 停止。
4. 終態保留的雜湊／身分仍是可連結 metadata，保留期間與告知尚待正式定案。
5. 未提供 GCS 列舉、歷史版本／soft-delete 清除或排程。這些需要另外的明確授權方案。
6. claimed-but-no-result 與沒有 quota entry 的 mapping 仍阻擋，需執行證據、fence 及
   有界人工核對政策。不能只靠逾時當作沒執行，也不能啟用後讓使用者永遠等待。
7. 正式服務帳號／訂閱恢復、原生 UI 接線、舊額度切換、結果連回歷史與可選研究送件
   尚未完成；免費 2、Plus 總計 10 及研究同意獨立的設定未改。

實作契約：`engineering/contracts/lite-service-result-lifecycle-v1.md`。
重跑：`python -m unittest -v test_lite_service_retention test_lite_service_retention_probe`；
環境及固定依賴見 `.github/workflows/lite-prelaunch.yml`。
覆核包從精確本機 commit 複製並核對雜湊，沒有代傳給 Claude 或寫入公開庫。
