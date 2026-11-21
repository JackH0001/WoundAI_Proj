# 三項營運材料匯入與中斷核對

本輪補齊「已有本機加密備份，但個人服務還沒有可掛載材料」的操作端。
`tools/lite_runtime_custody.py` 預設 `verify` 只核對原備份、獨立收據與固定方案，
不解密、不連線。`owner-import` 必須另外取得固定方案的授權，並由本人在終端機
輸入復原密語；不接受管線密語，也不把明文寫到磁碟。

本輪只完成程式與合成驗收。沒有匯入真實營運材料、建立 Secret、修改 IAM、啟用服務、
合併、部署或交付 Apple。CI 新增合成測試，但新來源尚未推送，不能沿用舊來源的綠燈。

## 與原備份的對應

| 角色 | 內容 | 預定 Secret | 未來可讀的服務 |
|---|---|---|---|
| identity | 原帳號 HMAC 的 32 bytes | woundai-lite-native-sandbox-identity | 個人服務 |
| native_issuer | 個人服務研究關聯簽章 | woundai-lite-native-sandbox-native-issuer | 個人服務 |
| research_issuer | 研究服務關聯簽章 | woundai-lite-native-sandbox-research-issuer | 研究服務 |

方案綁定原備份與獨立收據雜湊、manifest、scope、工具來源、專案、三個精確位置與當時
metadata/IAM。每個位置必須事先建立、沒有版本、沒有直接 IAM 授權，且是 asia-east1
user-managed replication。此工具沒有建立位置或授權服務讀取的能力。
四把 operator／observer／disposition operator／drain 私鑰不在匯入內容內。
Apple 的兩把 `.p8` 與 KMS 均不在本工具範圍內。

## 執行與失敗

- 本人終端機會顯示本次授權的截止時間與時區。程序在本人完成確認、輸入密語以及本機解密後各核對一次時間。若在這些步驟到期，終端機提示關閉視窗，此次程序尚未建立匯入日誌，也未連線。已開始匯入的中斷仍應保留原日誌，依下列程序核對。
- 新程序先核對全部三個空位置，再依序各新增第一版；提供 CRC32C，核對新增回覆與第一版
  metadata 後立即以 etag 停用，最後再次核對三項。沒有 `access`、`enable`、`destroy`、
  `setIamPolicy` 或任意 URL 呼叫路徑。
- `addVersion` 不具本工具可用的去重鍵；送出前將意圖寫入原子、fsync 的私人日誌，
  過程不自動重送。第二次匯入不能覆寫或沿用該日誌；跨程序檔案鎖防止同時操作。
- 網路回應遺失時，清理只嘗試停用該次嘗試的第一版，不新增第二版。
  即使找到版本且停用成功，沒有原新增成功回覆也不宣稱內容已驗證；CRC32C旗標
  只表示 provider 接受了某次傳入的 checksum，並不能獨立證明丟失回覆中的內容。
- 查不到版本時保持未確認：它可能仍在途中。後續 `reconcile` 使用同一日誌、
  同一方案及新批准的有期限恢復授權；可在原匯入窗口之後核對，但仍禁止新增／啟用。
- 每次檢查都核對專案沒有上層組織、專案 IAM 未變，並重新讀取最多16個角色定義；
  可讀／新增／啟用／停用／銷毀 Secret，或可更改相關政策與自訂角色的權限只能屬於
  明列本人。直接 Secret IAM 必須為空。這不是對並行 IAM 變更的原子鎖；執行窗口仍須
  凍結其他管理操作。若專案新增上層組織，停止並補完整繼承權限方案。
- 每個日誌最多240次 HTTP，前進最多160次，保留80次供停用核對；重啟不歸零。
  每個程序在呼叫邊界檢查15分鐘，單一 HTTP timeout 15秒。這不是 OS 硬性終止器。
  中途斷電、網路中斷、權限改變或期限／預算耗盡可能仍留下未確認的啟用版本，
  程式必須如實回報，不保證所有故障都能自動停用。
- 雲端請求只輸出固定錯誤；日誌只含路徑、角色、雜湊及結果，不記 token、密語或 payload。
  Python 記憶體不能保證實體清零；完成後程序結束，不輸出或落地明文。

## 下一個真正部署方案的必要前置

先完成固定來源 CI 與獨立覆核；由本輪三個不存在位置的 metadata 盤點準備限定建立方案，
建立後讀回實際 etag、IAM、區域與空版本清冊，才能形成可執行的匯入方案，不能用測試
fixture 或任意佔位雜湊替代。原始備份與稽核均保留。

匯入後仍需原生設定、獨立服務身分、最小權限、共用 KMS、初始化與發布、Secret 真實
掛載型態驗收，才可本人實測 Apple 登入／購買／恢復。研究服務更新與版號也需對齊；
匯入工具成功不代表訂閱已可用或完整刪帳已驗收。

## 重跑

```text
PYTHONPATH=tools:Backend/Flask:engineering/phase2 python -m unittest -v test_lite_runtime_custody
```

Google 語意參照：[新增版本](https://docs.cloud.google.com/secret-manager/docs/add-secret-version)、
[etag 併發控制](https://docs.cloud.google.com/secret-manager/docs/etags)、
[SecretVersion / checksum](https://docs.cloud.google.com/secret-manager/docs/reference/rpc/google.cloud.secretmanager.v1)。
