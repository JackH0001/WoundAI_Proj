# Claude 覆核交接：Lite 獨立管理入口與部署前方案（2026-10-24）

這次新增 `Backend/LiteReview` 唯讀候選入口，並沒有部署到雲端。原始影像、深度、同意、稽核、MMH／Demo／Lite runtime、IAM、排程與商店皆未變更。PR #35 仍為 draft，不代表真實病患研究可上線。

## 已實作

- 獨立 HTML/BFF 與私有 reader；不匯入醫療 Flask bootstrap、推論模型、醫療帳號或 Lite 上傳 API。容器只 COPY 明列的檔案，使用既有 hash-locked dependency 子集及非 root 執行身分。
- IAP ES256 簽章、issuer、專用 audience、時間及 subject/capability allowlist；reader 再驗證原始人員 JWT 與 portal SA 的 Google ID token。unsigned email、MMH/Demo token、錯誤 service subject/audience 皆不接受。
- Portal 沒有 GCS 權限；reader 介面僅 GET 固定 Lite 媒體／狀態，另向獨立存取稽核桶 create-only。實際雲端 IAM 尚待部署前核對，不能以程式白名單宣稱 IAM 已隔離。
- 以 `(owner, epoch, image)` 及 generation/bytes/SHA-256 綁定收件與修訂。AI 與使用者遮罩分開；使用者仍是 lay，不升格為醫師精標。表面積／投影面積分開，不推定貼紙面積、體積精確度或訓練資格。
- 一頁 50 筆；cursor 綁定 viewer、capabilities、filter、catalogue hash 與期限。detail 可選擇特定修訂／只看原 AI；metadata-only 不回傳 pixels 或 polygons。
- 每次讀取當前同意 pointer、linked reconsent event、epoch fence；存取稽核寫入成功後，再核對全部物件版本與同意。撤回途中、重新同意舊 epoch、sealed/current-version mismatch 或 audit failure 皆阻擋。
- 實際 Google Storage SDK 測試發現：對 reload 過的同一 Blob 下載會選取歷史 generation。已改成 fresh unversioned Blob + `if_generation_match`，測試檢查實際 SDK request URL 沒有 `generation=` 歷史選擇器。
- Cloud Run 平台認證與應用再次驗證使用分開標頭；禁止 redirect、任意來源 URL、資料寫入或刪除路由。CSP、no-store、frame denial、escaped template，無預覽用 GCS signed URL。

## 有意限制，不能略過

1. 首版是受保護、digest-pinned 的有限 catalogue 快照（最多 2,000 筆、24 小時期限），不是全研究資料／即時全量索引。新收件與新修訂需更新快照；HTTP 不掃桶。`build_lite_review_catalogue.py` 只驗證已有輸入，不會盤點 GCS，也不保證完整性。實際 catalogue 與角色檔不得放進公開 Git／Claude ZIP。
2. 詳情顯示的是快照內修訂與 epoch 可否核對，尚無完整清理歷史、備份／軟刪除永久銷毀管理頁。缺少深度或快照過期會明確阻擋／標示，不補造資料。
3. 最後檢查之後才發生的撤回，無法追回已回傳／正在傳送的 bytes。無長效 positive consent cache；這不是原子撤銷 HTTP 傳送的保證。
4. security bucket 使用 hashed state names，IAM 的 GET 無法區分 fence 與 App Attest 類別；API 僅推導同意 key，但 reader 身分的底層權限仍較廣。正式開通需覆核此殘餘邊界與 inherited IAM；不能賦予 BFF 相同權限。
5. 角色檔變更需要更新 protected configuration；Google IAP token 到期前可能仍有效，不保證立即撤銷登入。
6. 實際 IAP／OAuth eligibility、獨立 client scope、owner subject、SA subject、audit retention、catalogue inventory 及兩層 Cloud Run 呼叫都尚待 live acceptance；不改動 MMH 的專案 OAuth 設定。

## 索引與清理方案

`tools/plan_lite_review_rollout.py` 只會在 clean committed source 產生 `execution_authorized=false` 固定來源 JSON，沒有部署或刪除命令。

- **Lite catalogue**：唯讀來源盤點另核准；不遷移或改寫 Lite enrollment／fence／label，也不套用醫療 consent backfill。
- **MMH／Demo medical indexes**：各自盤點來源 generation 與 ownership，排除不明歸屬，核定 manifest，停止且排空 writer，additive backfill 雙向索引，核對來源未變後最後寫 completion marker；只能恢復 purge-aware reader/writer。
- **Medical cleanup**：候選 Cloud Run Job，獨立 SA；Scheduler OAuth 呼叫 Jobs API。規劃每日台灣時間 03:30，task/parallelism=1、retries=0、timeout=600 秒，初次 dry-run；不建立或啟用。CAS／generation 才是競爭控制，不能靠排程保證只執行一次。
- **禁止**將 medical cleanup worker 指到 Lite epoch storage。Lite 永久 fence marker 不可列入定時刪除。正式政策、範圍及固定映像未核准前，apply／scheduler 不執行。
- 告警方案涵蓋非零退出、缺結果、被阻擋 subject、逾期待處理及超過 26 小時未成功；實際通知目的地及送達尚未驗證。

## 本機驗證

| 測試集 | 通過 |
|---|---:|
| 新 Lite portal／JWT／兩層 HTTP／SDK／catalogue／plan | 35 |
| Lite fenced objects／manifest／HTTP | 45 |
| Lite reconsent | 13 |
| Lite service profile | 20 |
| Console capabilities | 4 |
| Medical consent operations | 41 |
| Medical consent rollout | 28 |
| Medical operations plan | 5 |
| **合計** | **191** |

以上為不使用 credentials/network 的合成測試。新增隔離容器 GitHub CI；目前 Mac 沒有 Docker，因此不把本機 Python 測試說成容器建置通過。CI 的 exact commit、結果 URL 與日誌另列在交接包。既有 p0-4-audit 同時執行新增測試；不要求此 UI-only 變更重新實機拍攝。

本機瀏覽器另核對收件清單、台灣時間、分開面積、原 AI／修訂遮罩與版次切換。預覽只使用程式生成的粉紅色影像與幾何遮罩，不含真實紀錄。預覽測試 server 已停止。

## 前次真實 GCS 證據，與本次分開

固定來源 `28c350e`、另授權的 preserving continuation 已完成 12/12 bounded GCS assertions。兩筆原始合成物件 generation/bytes/SHA 不變，原稽核保留；新增路徑 `consent-ops-synthetic/20261024-r2`，temporary TokenCreator 已移除。這次未重新執行。

七日 soft-delete 自然到期仍待；live crash/resume、late replacement、hold／audit failure 的剩餘注入測試亦不是本次新通過項目。不能把離線測試或前次12項成功當成正式全量遷移／清理／IAM驗收。

## Claude 覆核重點與重跑

```sh
python -B engineering/phase2/test_lite_review_portal.py
python -m unittest discover -s engineering/phase2 -p 'test_lite_fenced_*.py'
python -B engineering/phase2/test_lite_reconsent.py
```

請優先覆核：production fenced key／slot derivation 一致性、reconsent event 獨立驗證、Cloud Run headers、最新 generation 限制、快照落後的顯示、可取得權限與 API 功能的差異、醫療 worker 與 Lite scope 分界。保留測試限制；不要推定這次部署完成。

參照：
- [Google IAP signed headers](https://docs.cloud.google.com/iap/docs/signed-headers-howto)
- [Cloud Run direct IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)
- [Cloud Run service-to-service headers](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
- [Cloud Run scheduled Jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)
- `engineering/contracts/lite-review-portal-v1.md`
- `engineering/contracts/medical-consent-operations-acceptance-v1.md`
- `docs/claude_consent_preserving_continuation_20261024.md`
