# WoundAILite 獨立後台：A7 結果與 B1／B2 驗收準備

2026-10-25。這次只新增離線方案、測試與文件；應用程式邏輯、既有研究資料、
MMH／Demo／Lite App、排程及商店版本均未變更。下面明確分開已實測與尚待授權。

## 已完成的雲端事實

原 A7 單次方案 `156fc356…` 已於台灣時間 14:14 完成，不可重跑：

- 固定應用來源 `065c3289819f905cd7581680a42e3c686678c23a`。
- 沿用映像 `sha256:23a38262ac509da954bc3b313ab8ffb34fade9fb39770d6c2ad7dc91b7f80d07`。
- 11 項負向 HTTP、16 項實際身分桶權限與 86 項最後唯讀核對全通過。
- 3 個臨時授權收回；保留原定 5 個設定／服務呼叫授權，建置身分停用。
- 兩個服務均要求 IAM，沒有公開 invoker、沒有研究桶權限，角色與資料目錄空白。
- 沒有重建映像、新增服務修訂、讀寫研究物件或執行清理。

既有覆核包 `WoundAI-Claude-lite-A7-isolation-065c328-20261025.zip` 的 SHA-256：
`964c96d9526b27666f40c03640d4206b3aaf1dc13cac5067ed7b036c317b580a`。
包內 117 項 manifest 與 41 個來源檔核對通過，80 項操作工具測試可重播。
這些數字是 A7 證據，不能當成 B1 人員登入或 B2 正向資料檢視通過。

## 14:23 的新增唯讀盤點

- 專案沒有 organization parent，Lite 沒有自訂 OAuth 或 IAP 人員授權。
- MMH 的自訂 OAuth 位在其自己的服務資源，專案及 `iap_web` 層沒有自訂 OAuth。
- 獨立檢視稽核桶 `woundai-lite-review-audit-421209514056` 尚不存在。
- 操作者經 Google OpenID userinfo 回覆的已驗證 email 符合指定 owner；numeric subject
  只存本機受保護檔。**尚未取得實際人員 IAP 驗收結果**，不能把 userinfo 當 IAP 通過。
- 這輪沒有雲端修改、研究物件讀取或原始資料匯出。

Console 後續唯讀核對：專案 audience 是 External／Testing，指定 owner 已在測試
名單，尚無 Lite 專用 client。共用同意畫面名稱為 `WoundAI MMHPS20261007`；建立
另一個 client 不會讓這個專案共用名稱自動獨立。B1 可作 owner 內部驗收，但正式
對外人員入口的品牌隔離仍需另案決定，不得默默改名影響 MMH。稽核專用 create
角色 `liteReviewAuditCreate` 查詢為 NOT_FOUND；方案列為新建，執行前必須重查。

## B1：先驗收僅 owner 可登入的空目錄

`tools/plan_lite_review_human.py` 是離線審閱文件產生器，不是部署執行器。
讀入 A7 完成收據及 24 小時內唯讀邊界證據，固定應用映像不變，提出：

1. 一個專用 web OAuth client，只設定 Lite portal 的 IAP resource。
   先在 Console 唯讀確認既有 branding／audience 能供 owner 使用；若需要改專案
   branding、測試者清單或 MMH client，停止並另外規劃，不能自動修改。
2. owner 只拿 `lite.review.metadata`，應用與 IAP 都限定同一位已核實 owner。
   Reader 再驗一次原始 signed IAP assertion 與 portal SA token。
3. 一個獨立 access-audit 桶，UBLA、禁止公開、7 日 soft delete、不設定清理規則或
   retention lock。Reader 只可 create，無讀取、覆寫、刪除；應用用隨機檔名與
   `if_generation_match=0`。這不是 WORM 或物理永久刪除保證。
4. 僅新增三個既有 Secret 的版本（兩個設定與空目錄），沿用 cursor key；以數字版本
   固定，最多兩個設定修訂。空目錄 timestamp 在執行時固定並保留，24 小時後拒絕。
5. **完全不授予 Lite／MMH／Demo 來源桶 get/list/write/delete**；連空目錄也必須
   成功寫入獨立檢視稽核後才回應。存取稽核是唯一新增資料，不是研究影像。

本機新測試使用真正 ES256 簽章、兩層 Flask、generation-aware 稽核桶與來源存取
tripwire，檢查空目錄、稽核失敗、假冒 owner、媒體能力、猜測 record ID 及 reader 重驗。
它們無法證明 Google 實際 IAP 登入、跨服務 header 或真實 GCS 權限；雲端需另驗。
本輪本機結果：既有 portal 40、新增 empty-human 6、離線 plan 12，共 58 項通過；
來源 CI 結果另附實際 head 的檢查收據，不以先前 head 的成功代替。

失敗／完成／等候使用者時先封閉人員入口、還原空角色設定，再移除本次新增授權。
保存 OAuth client、Secret 版本與所有稽核，不刪桶、不覆寫舊證據。若還原的舊目錄
已超過 24 小時，可能回 503；這是封閉失敗處理，不宣稱頁面仍可用。
不能以「曾經授權 A」取代 B1 新授權。新執行器與回復演練、Console preflight、
來源 CI 全部完成後才提出可執行的固定方案；目前計畫 `deployable=false`。

## B2：限定合成資料的正向驗收

B1 通過後才整理新的有限 fixture manifest：隨機合成 owner、legacy／new epoch、
影像、AI 與兩筆 lay 修訂、RGB-D 收據、各檔 generation／bytes／SHA-256；每個 state
key 由 production derivation 算出。禁止拿真實 owner 或既有標註充當 fixture。
Reader 只取這些確切 object names 的 GET，portal 仍沒有 GCS 權限。

驗收矩陣：同筆各修訂分開、原影像／深度不重建；撤回前後／view 中途撤回；新 epoch
不復活舊 epoch；source generation 改變、audit 失敗與未知 state 拒絕；其他 owner、
MMH／Demo 及 App Attest state 拒絕。來源寫入及 fixture 狀態操作需要另外列明的
新授權，不能偷用檢視身分完成。雲端故障注入不包含在 B1。

## C／D：與 Lite 檢視分開的正式上線門檻

| 範圍 | 先完成 | 後續另行授權 | 禁止混用 |
|---|---|---|---|
| C 醫療索引 | 每環境有限 manifest、generation／hash、未知 owner 處理、舊新 writer 停寫排空 | additive backfill、雙向索引讀回，完成標記最後寫入 | 不跑在 Lite epoch 或合成 smoke 原始桶 |
| D 清理 | 核准 retention、C 完成、dry-run、故障／hold／audit 失敗驗收、自然 soft expiry 證據 | 指定版本刪除，再另行啟用排程 | 不刪 Lite 永久 fence、稽核或未授權媒體 |

過去合成 GCS 驗收只證明當時列明的項目；自然 7 日到期、live crash/resume、late
replacement、hold/audit failure 尚未全數完成。不得把本次空目錄測試算成清理驗收。
建議排程仍僅為 `30 3 * * *`（台灣時間）、單一 task、零自動重試的提案；沒有建立或啟用。

## Claude 覆核重點與參照

- 核對新計畫沒有把 A7 已使用的授權改成可重跑，也沒有要求新 Cloud Build。
- 核對 B1 零來源存取與獨立 audit；新測試不宣稱已跑真實雲端。
- 核對私有 numeric subject、OAuth secret、完整 IAM baseline 沒進 Git／覆核包。
- 對照原 `engineering/contracts/lite-review-portal-v1.md` 及
  `engineering/contracts/medical-consent-operations-acceptance-v1.md`。

官方文件（2026-10-25 查核）：
[IAP service-scoped custom OAuth](https://docs.cloud.google.com/iap/docs/custom-oauth-configuration)、
[Cloud Run direct IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)、
[signed IAP headers](https://docs.cloud.google.com/iap/docs/signed-headers-howto)、
[GCS IAM conditions](https://docs.cloud.google.com/storage/docs/access-control/iam)。
不採 Console 一鍵產生專案層 OAuth 的捷徑，因為本案要求保留 MMH 既有設定。
