# 私有 KMS 操作：傳輸層不得自行重送

本輪針對真實 provider 接線前的缺口：`KeyLifecycleDispatch` 只呼叫一次 `session.request()`，
並不能保證底層只送出一次 HTTP 要求。Google `AuthorizedSession` 的預設行為是在收到 401 後
更新憑證並重送原要求；Requests 的 adapter 也可被另行設定為自動重試。

目前候選尚未部署此操作器，所以這是部署前發現的接線風險，**不是已觀察到真實 KMS 被重複操作**。

## 修正

新增 `PinnedKeyLifecycle.with_access_token()`，內部擁有 `SingleAttemptKMSTransport`：

- 明確傳入短效 access token 與到期時間；不尋找 ADC、鑰匙圈、環境憑證或私鑰，也不呼叫 token refresh。
  要求執行當下剩餘時間大於20秒、最多1小時。這只是呼叫端提供的期限防護，不是 token 簽章或身分驗證。
- 固定 `https://cloudkms.googleapis.com/v1/` 及單一舊金鑰版本；只接受讀取、停用、排程銷毀及取消排程。
  不接受其他目的地、新版本、enable、import、create、encrypt 或 decrypt。
- 使用原生 `requests.Session`，明確將連線、讀取、狀態、重新導向及其他重試全部設為0。
  不使用會在401後遞迴重送的 `AuthorizedSession`。401／503／斷線都回到原派送紀錄，不在傳輸層補送。
- 關閉環境代理與 netrc，保持 TLS 驗證，拒絕 redirect、外加驗證處理器、hook、cookie、代理或改動的重試設定。
  到期或設定不符時，HTTP要求尚未送出便停止。
- `close()` 關閉連線並移除物件持有的 token 參照。這不代表 Python 記憶體、安全備份或供應者紀錄已抹除。

既有可注入 session 的建構子仍供合成 provider 使用；它是受信任的內部測試接點，不是網路權限邊界。
未來真實執行器必須由新 factory 建立傳輸，不能自行換成預設 `AuthorizedSession`。
本輪沒有新增公開入口、啟動接線、憑證載入器、IAM 或排程。

## 驗證方式與前後差異

21項新增測試以實際 Requests／urllib3 執行。HTTP伺服器只監聽127.0.0.1隨機埠；
測試將固定 HTTPS 要求的 adapter 路由到該本機埠，並阻擋其他 socket 目的地。
TLS參數與正式URL固定由測試核對，但**真實Google TLS、IAM、權杖取得及Cloud Run網路尚未驗收**。

| 情境 | 修正前／修正後 |
| --- | --- |
| 同一操作第一次回401、下一次可成功 | Google預設連線送2次並refresh 1次；新傳輸只送1次並回報未確認 |
| 操作已生效、socket回應中斷 | 保留uncertain；重啟及重播不增加要求，獨立排空簽章後唯讀核對 |
| 503、307重新導向或無法連線 | 不自動重送、不將憑證送到新網址 |
| 認領操作後 token 失效 | 不送HTTP變更，仍保留原認領與稽核，依原續接流程核對 |
| 設定被改為重試、加hook／代理或關TLS驗證 | 送出前拒絕 |
| 停用、排程、取消 | 各只送一個明確變更要求，取消回DISABLED |

另將上一輪的 `SharedKeyRetirement` 真正接到新傳輸與本機HTTP伺服器：舊備份先被阻擋，
補齊復原副本後才停用；斷線重播不再派送，再經獨立核對接續排程／取消。
原物件版本、grant、稽核及額度保持；存活帳號仍可讀。

完整 Lite 2,796項通過，依賴版本見固定提交覆核包的日誌、`integrated-probe.json`。
指定套件90項包含新21項及既有派送33／退休36項，不與完整Lite總數相加。
整合探針有兩個獨立合成場景，其變更次數分開記錄，不當作同一次生產操作。

```sh
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B -m unittest -v test_lite_key_lifecycle_transport test_lite_key_lifecycle_dispatch test_lite_shared_key_retirement
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B tools/lite_key_lifecycle_transport_probe.py --output wire-probe.json
```

## 仍需真實外測驗收

短效權杖須由核定身分在固定方案外取得，不把401當成自動換身分的理由。
只派送一次不能證明操作是否生效；CAS認領、獨立排空、唯讀核對及原始稽核仍不可省略。
完整來源清冊、存活帳號災復、有效IAM與唯一操作權限、Google實際回應及刪帳承諾均未完成。
本輪沒有銷毀真實金鑰、修改研究資料或更新TestFlight。

## 官方參照

- [Google AuthorizedSession](https://google-auth.readthedocs.io/en/latest/reference/google.auth.transport.requests.html)：預設401更新憑證與重送的參數。
- [Google request實作](https://google-auth.readthedocs.io/en/latest/_modules/google/auth/transport/requests.html)：refresh後再次呼叫原要求的流程。
- [Requests HTTPAdapter](https://requests.readthedocs.io/en/latest/_modules/requests/adapters/)：重試設定及TLS驗證傳遞。
