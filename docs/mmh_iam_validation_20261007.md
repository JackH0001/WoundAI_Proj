# MMH 第二階段：有效 IAM 與可撤銷防護

2026-10-07；承接 PR #19 的 378818e。本階段沒有部署 MMH 服務、建立帳號、給 runtime 存取權、上傳資料或鎖定稽核桶。

## 雲端實際進度

- Cloud Resource Manager API 已啟用並讀回確認。剛啟用時，區域標籤 API 仍回 SERVICE_NOT_ACTIVATED；稍後重新驗證後成功，失敗紀錄保留，未略過檢查。
- 建立 `tagKeys/281482074135552`（woundai-mmhps20261007-protection）、`tagValues/281476794278770`（enabled），只直接綁定三個 MMH 桶。
- 掃描目前同專案桶的直接標籤，確認此值未綁到其他桶；也未綁到專案。不是日後所有跨專案資源的持續監控保證。
- **Deny 政策尚未建立**：Google API 拒絕 iam.denypolicies.create。Owner 的現有角色不足以執行這個動作；不是密碼過期，不需要重新登入。
- 未自行為使用者提高權限。已請 Jack 確認：只對自己的帳號增加限時一小時 roles/iam.denyAdmin，完成本次政策設定後立即移除。此授權與不可逆稽核鎖定分開。

## 可覆核方案

[精確政策 JSON](mmh_bucket_protection_plan_20261007.json) 配合 `tools/protect_mmh_buckets.py`：

- attachment：cloudresourcemanager.googleapis.com/projects/421209514056。
- policy ID：woundai-mmhps20261007-bucket-protection。
- 只對匹配以上 tag ID/value 的資源生效，不以桶名字串前綴推測範圍。
- 拒絕 bucket delete、setIamPolicy、update、createTagBinding、deleteTagBinding；保留 Jack 的管理入口。沒有授予任何新 allow permission。
- 未改動舊服務的 Editor role；專用 runtime 也不能修改桶政策。
- 開始任何後續變更前，要用 v3 重新證明操作者有建立 deny policy 的有效權限。缺少／不確定結果即停止，避免再次先綁 tag 才遇到權限拒絕。
- 現有不同政策不覆蓋；專案、操作者、Owner、桶設定、標籤範圍及讀回都需符合。

這是可撤銷 IAM 政策，不是 WORM 或 retention lock。回復需具備 deny policy 管理權限，先核對 policy ID／內容，再刪除該政策；不要刪桶、刪物件或移除不相關政策。限時角色移除後，未來維護政策需重新取得適當權限。

## 驗證工具與限制

舊 gcloud policy-troubleshoot iam 在本機版本回傳 v1 allow-policy 結果，不能拿來證明新 deny policy 有效。本輪使用 REST v3：

1. 同時檢查 overallAccessState、allowAccessState、denyAccessState。
2. 回應 principal、resource、permission 必須與請求一致；錯誤資源或 UNKNOWN 不算拒絕證據。
3. 包含請求當下時間，供限時角色條件評估。
4. 40 項實測矩陣涵蓋舊 runtime 對三桶的讀／寫／刪除／政策及標籤修改、Owner 控制組、既有醫療桶控制組，以及 MMH runtime 的八項冒用／憑證權限。
5. 憑證只放記憶體 Authorization header。沒有實際刪桶、生成 SA key 或模擬冒用 token。
6. 首輪並行呼叫遇到 429，不能視為完成；改為逐筆間隔七秒。若中途錯誤，報告標為 complete=false，保留已完成項目，並使舊的綠燈報告失效。

這仍是具名主體的直接權限檢查，不是所有服務帳號／群組／跨專案冒用鏈的完整證明。尤其 Editor 角色定義還包含 serviceAccounts.actAs 與 serviceAccountKeys.create；僅保護桶的控制面不足以處理服務帳號冒用，必須另驗／阻斷後才能給 MMH runtime 資料存取權。

## 雲端實測結果

40 項唯讀 v3 查詢完成：**35 符合預期、5 不符，整體 gate 失敗**。

| 不符預期的直接權限 | 數量 | 實測結果 |
|---|---:|---|
| 舊 runtime → 三個 MMH 桶，storage.buckets.delete | 3 | CAN_ACCESS |
| 舊 runtime → MMH runtime，iam.serviceAccounts.actAs | 1 | CAN_ACCESS |
| 舊 runtime → MMH runtime，iam.serviceAccountKeys.create | 1 | CAN_ACCESS |

Owner 三項管理入口及舊醫療桶兩項控制組符合預期；三桶直接物件 get/list/create/delete 均拒絕。這不代表將來授權 MMH runtime 後仍無間接讀取風險；需先阻斷上述服務帳號路徑。提案中的桶 deny JSON **只處理三項刪桶問題，不會解決兩項服務帳號問題**，必須追加具名身分防護與驗收後再給 runtime 權限。

該矩陣執行期間沒有套用 deny policy；所有結果皆為目前尚未防護完成的狀態。後續已改進診斷腳本的失敗報告失效機制與限時條件時間欄位，本機測試通過；沒有把修改後的工具再跑一次說成已放行雲端。

## 本機測試

- 防護方案：13/13。
- v3 證據判定與失敗報告：10/10。
- 第一階段機構／Flask／foundation／bucket IAM：26/26 重跑通過。
- 總計 49 項；六種變異（操作者權限、Owner、標籤範圍、政策讀回、UNKNOWN、回應對象）全部被捕獲。
- 這些不是 40 項雲端隔離驗收通過的替代證據。
- 第一階段 103/103 測試檔結果仍保留；本輪未修改後端應用程式，不宣稱又跑了一次完整後端回歸。

執行方式（使用專案相依已安裝的 Python）：

```sh
python -B tools/test_protect_mmh_buckets.py
python -B tools/test_verify_mmh_effective_iam.py
python -B tools/protect_mmh_buckets.py --report /private/tmp/mmh-plan.json
python -B tools/verify_mmh_effective_iam.py --report /private/tmp/mmh-iam-v3.json
```

`--apply` 需先完成有效權限確認；不以授權問題尚未回覆視為同意。

## 參照

- [Google IAM deny policies](https://docs.cloud.google.com/iam/docs/deny-overview)：deny 與 allow 的關係及 tag 條件。
- [Cloud Storage tags](https://docs.cloud.google.com/storage/docs/tags-and-labels)：桶的直接標籤與區域綁定。
- [Policy Troubleshooter v3](https://docs.cloud.google.com/policy-intelligence/docs/reference/policytroubleshooter/rest/v3/iam/troubleshoot)：回應契約。
- [第一階段建置](mmh_foundation_validation_20261007.md)、[MMH 環境與帳號](mmhps20261007_environment_and_accounts.md)。
- 本機證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/`；mmh-protection-ready-plan.json、mmh-protection-apply-after-api-propagation.log、mmh-stage2-mutations.json、test_*-stage2.log、mmh-iam-v3-matrix.*。
