# 私有初始化身分：權限提案與失敗收回演練

`tools/lite_bootstrap_iam.py` 是離線提案工具，沒有 credential discovery、provider 呼叫、
`setIamPolicy`、部署或刪除資料的能力。它補上 bootstrap runner 的權限準備，
不把程式內的 `RestrictedStore` 誤當成真實 IAM 隔離，也不取代新的執行授權。

## 由相同設定產生範圍

- 同一份 hash-pinned runner／native config 先走 runner 的完整 preflight；
  另核對操作者明列的桶集合，不接受漏列、額外桶或 customer runtime 身分代用。
- 18 個核心狀態位置使用 READ／CREATE／MUTABLE 定義，加上兩個 provider budget 位置，共 20 個。
  讀取只有 GET；稽核只有 GET+CREATE；可變狀態為 GET+CREATE+DELETE。
- 每個物件條件帶有完整 bucket／prefix 與末尾斜線，不包含相近 prefix。
  Bucket metadata GET 分開授予精確桶，不授予 LIST。
- 每個 binding 使用相同 provider budget 時窗，最長一小時；政策版本 3。
  自訂角色僅四類，既存同名角色若權限較多、停用／刪除、無法讀回則停止；
  只有明確 404 才列為待建立，沒有自動更改既存角色。
- 無 Secret、KMS、模型、IAM 管理、服務呼叫、研究影像或清理權限。

GCS 覆寫既有物件需要 CREATE 與 DELETE；IAM 本身不能限制 DELETE 只用於 CAS。
LIST 是桶層級能力，不能靠 resource.name 把列舉權限限制成 prefix。這兩項限制必須
出現在正式授權及有效權限驗收中。[GCS 權限定義](https://docs.cloud.google.com/storage/docs/access-control/iam-permissions)、
[GCS IAM 條件](https://docs.cloud.google.com/storage/docs/access-control/iam)。

## 回復是移除本次差異，不是整份舊政策回寫

`add_delta` 僅準備加法式 policy body，保留原 etag 與全部原欄位，回傳獨立複本的新增清單。
若該身分已有不相同的 grant，或桶有 public grant，先停止供覆核。
未來實際執行器必須以讀回 etag 作 compare-and-set；本工具不送出 body。

`remove_delta` 必須取得原新增清單的 hash、正確桶、最新完整 policy 及最新 etag。
只移除仍逐欄相符的本次新增項目，保留後來其他人的更新與原有稽核設定。
新增項目的內容或標題被改動時停止，不憑名稱猜測移除；已經移除則為 no-op。
原本就存在的相同 binding 不算本次新增，不能在回復時刪掉。
成功回傳也仍標示 `effective_access_removed=false`，因為 project／group／其他層級
可能另有授權。條件限制不會覆蓋無條件 grant；到期後 binding 也不會自動從政策消失。
[Google 條件式政策管理](https://docs.cloud.google.com/iam/docs/managing-conditional-role-bindings)。

## 驗證與未完成事項

`test_lite_bootstrap_iam` 驗證路徑／時間／角色邊界、未知讀回、etag 競爭、回應遺失後收回、
保留他人變更與不重複移除。`lite_bootstrap_iam_probe.py` 用合成設定逐桶演練；
不載入真實帳號、金鑰或研究資料，不聲稱等同 Google CEL／IAM 實測。

第一輪測試發現提案 policy 與回復 receipt 共用 mutable binding 參照；
已改為獨立複本，並新增修改 policy 後 receipt 不變的回歸測試。
這是本輪新工具在尚未部署前修正的缺陷，不是既有雲端事故。

正式執行前仍須：取得營運材料與設定、確認新來源 CI、固定映像、角色與 bucket metadata
讀回、Google CEL lint／有效權限正負向測試、上層 IAM／群組盤點、獨立寫入封閉觀測，
以及含費用／時限／失敗回復的部署授權。請勿將本工具的合成時間窗或公鑰當上線值。
