# WoundAILite 首次初始化單次執行器

這是部署準備的本機實作，未執行雲端寫入、部署、Apple 購買、資料清理或送審。
既有 `FreshBootstrapExecutor` 與 GCS factory 現在由 `tools/lite_fresh_bootstrap_runner.py`
組合成單次私有程序；不需要匯入合成 fixture 或在服務啟動時偷建資料。

## 實際能力

- `inspect`：只讀原初始化狀態，不建立預算、簽章或新控制列。
- `execute`：執行一個已簽署的 subscription、quota 或 release 步驟。
  每個步驟都有獨立的命令摘要、原狀態及核定來源。不能一次自動跑三步。
- 訂閱、額度、release 都用原核心 API；初始化不寫使用者帳號、購買關聯或用量列。
- 雲端工作身分由 attached metadata 核對，不能使用 App runtime 身分、Mac ADC 或自行產生憑證。
- 控制面及工作面皆使用既有有上限 transport。執行前須成功取得耐久配額；不明結果不退還或重用配額。
- 每次都核對 bucket project、區域、公開存取防護、版本／保留設定及 metageneration。
  這不取代實際 IAM 驗收或外部 writer fence。
- 不取得 Apple／KMS／模型憑證，不簽署 observer、不改 IAM、流量或入口。

輸入為三份公開設定：runner（schema `lite-bootstrap-runner/1`）、已固定的 native config、
單次 request（schema `lite-bootstrap-request/1`）；三份均要獨立 SHA-256。
runner 同時綁定工具 source/image、native config、原方案、明列來源／writer／儲存位置、
bucket 世代及 `ProviderBudget` 上限。預算的 control/audit 不得與任何 App 或 bootstrap lane 重疊。
App runtime 與 bootstrap 身分必須不同。沒有自動建立資源、補齊缺項或擴大權限的路徑。

執行形狀（只是介面，不是已授權雲端命令）：

```text
python tools/lite_fresh_bootstrap_runner.py
  --config /absolute/runner.json --config-sha256 <reviewed hash>
  --native /absolute/native.json --native-sha256 <reviewed hash>
  --request /absolute/request.json --request-sha256 <reviewed hash>
  --source <runner commit> --image <runner image digest>
```

正式程序需設定 `DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true`，並由 supervisor
設定單一工作、硬性時限及停止舊工作程序。單一 HTTP 呼叫可能在 OS 中等待；transport
在邊界檢查時限，不能宣稱單靠 Python 可提供硬性終止。CLI 只輸出摘要或去識別化錯誤類型，
不輸出憑證、provider 原始錯誤內容或完整 traceback。

## 失敗及續接

1. 保留同一 plan、namespace、原 UTC 日、控制列及稽核。
2. 不確定的寫入先用新、唯讀 inspect 要求查核；不能清空控制列重來。
3. 舊工作確已停止後，獨立觀測者重新確認入口關閉及零 in-flight。
4. 以新簽署的 `bootstrap-recover` 綁原狀態及該觀測；沿用已啟用的核心結果，不重跑啟用。
5. 若預算、日期或時限不允許，停止並另作續接審查。不能在命令中任意補額度或換 namespace。
6. 重送已完成且仍有效的命令只回原結果；過期命令由 CLI 拒絕，可用 inspect 讀歷史。
   這不改變底層核心能讀回歷史回執的能力。

## 本機驗收及限制

`test_lite_fresh_bootstrap_runner` 使用真實訂閱／額度／release 核心、合成 Apple/KMS
fixture 與 SQLite 儲存，封鎖 socket 連線。覆蓋三步啟用、純讀取、回應遺失、明確恢復、
預算用盡／不明、簽章與來源不符、storage alias、入口未關閉，以及重開服務後同一張
合成影像仍只辨識一次、扣額度一次。不是實際 GCS、Apple Sandbox 或實機購買驗收。

既有 GCS factory、metadata identity、bounded HTTP／SDK transport 的獨立測試仍必須一併通過。
CI 已增加新 runner 測試，但本機變更尚未推送時不能宣稱新的遠端 CI 通過。

11/10 本機結果：新增 runner **20 項通過**；連同原 bootstrap/factory、provider budget、
bounded transport 與 metadata bootstrap 共 **138 項通過、零失敗**。20 已包含於 138，
不可相加。未修改 iOS，未重跑或宣稱新一輪 iOS／實機驗收。

## 部署前剩餘工作

必須把實際 native／research／disposition 及本 runner 的固定映像、設定、IAM、成本和
回復條件整理為同一可執行方案，再請新增部署授權。目前不是可直接送審的服務。
需提供完整清冊、真實獨立觀測與受限簽署程序；「新桶不存在」不等於可忽略舊帳號／用量。
不能因 soft delete 停用就推定沒有舊副本：Google 明載已軟刪除的資料仍依原期限保留。
參照：[停用 soft delete](https://docs.cloud.google.com/storage/docs/disable-soft-delete)。

本人 Apple 登入／購買恢復／同帳號額度、外測分離保管、金鑰退休、公開隱私政策與模型
授權仍需各自證據。本輪不會默默開放研究、啟用廣告或切換模型。
