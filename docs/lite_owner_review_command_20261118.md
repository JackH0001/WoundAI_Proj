# 本人首次登入的私有讀回工具

用途是讓操作員確認本人第一次 Apple 登入已留下與當次安裝及操作相符的證據。成功時產生短效本機收據，供後續切換重新核對。設定、帳號、訂閱與額度由各自的流程管理。

## 在本人候選 App 取得兩份核對檔

本人專用的個人服務候選組態需明確設 `WOUNDLITE_OWNER_REVIEW=YES`。一般版及未設此選項的外測候選預設隱藏「本人內測核對」。目前 TestFlight 46 未包含這個入口。

1. 先確認這部手機已有候選**個人服務**的裝置登記，再到帳號頁按「匯出本人核對資料」，儲存在「我的 iPhone」。`WoundAILite-owner-installation.json` 包含格式、服務網址及安裝識別碼的 SHA-256。操作員在受控的私有管道收取這份檔案，以本人獨立核對的安裝摘要固定計畫。匯出只讀取現有登記。若是全新安裝，需先完成獨立核准的裝置登記引導，研究服務的登記不能代用。
2. 經本人同意發起登入，等 App 顯示「本人帳號核對已送出」後，按同一個匯出按鈕。`WoundAILite-owner-request.json` 另含個人服務安裝識別碼及這次登入操作識別碼。這些識別碼用於找回同一份待核對要求。單純匯出或取消檔案選擇都不會重送登入、扣額度或改變研究同意。
3. 將第二份檔案經本人控制的私有管道交給自己的 Mac，避免放入共享雲端硬碟、聊天或 Git。兩份檔案均不含 Apple 登入憑證、照片或研究紀錄。遇到登出、刪帳、安裝變更或本機紀錄不一致時，先核對原狀態。

## 在 Mac 離線組成請求

權限覆核時，先保存候選真正使用的 native 設定原始位元，以及一份獨立固定摘要的 JSON 範本。範本頂層為 `schema`（`lite-owner-readback-template/1`）、`plan`、`project`、`reader`、`operator`。計畫含 `schema`（`lite-owner-readback/1`）、`native_sha256`、`source`、`image`、`project_number`、第一份檔案核對的 `installation_sha256`、`starts`、`until`。來源、映像、雲端專案及讀取身分均取自受審計畫。日期為 UTC epoch 整數秒，窗口最長一小時。

在 Mac 的 0700 私有目錄準備上述範本與第二份匯出檔，輸入檔設為 0600。環境變數填入絕對路徑，輸出使用新的檔名。

```sh
export PYTHONPATH=tools:engineering/phase2:Backend/Flask:.
python tools/lite_owner_review_command.py compose \
  --request "$REVIEW_TEMPLATE" --expected-request "$TEMPLATE_DIGEST" \
  --native-config "$NATIVE_CONFIG" --reference "$PRIVATE_PENDING_EXPORT" \
  --output "$REVIEW_REQUEST"
```

`compose` 以專案的正規 JSON 摘要核對範本，再核對第二份檔案的服務、安裝識別碼、原先固定的摘要及操作編號，成功才寫入 0600 的完整請求。登入前的摘要檔單獨無法組成完整請求。操作員應覆核輸出範圍，固定完整請求摘要，供下一步傳給 `scope` 和 `inspect`。本工具寫出的正規 JSON 可直接用 `shasum -a 256 "$REVIEW_REQUEST"` 計算摘要。正式雲端執行仍需對應授權。

## 完整請求欄位

組成後的操作請求包含下列欄位。

| 欄位 | 來源 |
| --- | --- |
| schema | 固定為 `lite-owner-readback-command/1` |
| plan | `lite-owner-readback/1`，含欄位 `schema`、`native_sha256`、`source`、`image`、`project_number`、`installation_sha256`、`operation`、`starts`、`until`。兩個時間均為 UTC epoch 整數秒，最長一小時 |
| installation | 本人候選「個人服務」當次已核對的安裝識別碼，其 UTF-8 SHA-256 必須符合計畫 |
| project、reader、operator | 原服務的專案 ID、該專案專用讀取帳號、已核准操作員的 Cloud 登入 email |

`operation` 是 App 在該次核對保存的 32 位十六進位操作編號。這些資料只以私有管道交接。保留 Apple 授權碼、ID token、nonce 和整份登入日誌在原有憑證處理流程內。實機端交接仍需另行驗收。

固定 native 設定的 SHA-256 使用原始檔案位元；操作請求的摘要則使用專案 `lite_usage_ledger.digest(request)`。操作員應將獨立覆核紀錄內的請求摘要傳入命令。輸入請求和輸出都使用目前使用者的私有目錄（0700）與檔案（0600），指定全新的絕對輸出路徑。

## 離線列出授權範圍

在 repo 根目錄使用 lite-prelaunch 的 Python 3.12 相依套件。以下引數名稱代表由操作員填入的私有路徑與固定摘要。

```sh
export PYTHONPATH=tools:engineering/phase2:Backend/Flask:.
python tools/lite_owner_review_command.py scope \
  --request "$REVIEW_REQUEST" --native-config "$NATIVE_CONFIG" \
  --expected-request "$REVIEW_REQUEST_DIGEST" --output "$SCOPE_PATH"
```

這個步驟可在核對窗口開始前執行。輸出列出 5 個物件的精確名稱、指定的讀取身分、操作員及起迄時間。正式執行之前應先核對專用身分的有效權限，包括繼承自專案或群組的授權。限定物件的 `storage.objects.get` 同時允許讀取該物件的 metadata 與內容。執行器對登記檔只讀 metadata 確認其存在性。條件與角色在由本人核准的雲端操作中另外套用及收回。[Cloud Storage 的 IAM 條件](https://docs.cloud.google.com/storage/docs/access-control/iam#conditions)

## 經授權後的讀回

```sh
export DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true
python tools/lite_owner_review_command.py inspect \
  --request "$REVIEW_REQUEST" --native-config "$NATIVE_CONFIG" \
  --expected-request "$REVIEW_REQUEST_DIGEST" --output "$RECEIPT_PATH"
```

操作員透過既有 Mac 的 Google Cloud CLI 工作階段，只向 IAM Credentials API 要求一枚專用讀取身分的短效 token。要求期限取窗口剩餘時間與 120 秒的較小值，scope 限唯讀儲存。每次執行最多要求一次；失敗即停止。讀取身分與服務執行身分分開。[Google IAM 短效存取憑證](https://docs.cloud.google.com/iam/docs/reference/credentials/rest/v1/projects.serviceAccounts/generateAccessToken)

正常情況下，兩輪讀取共 10 次 metadata 查詢、8 次指定 generation 的內容查詢。程式限制整次最多 18 次儲存請求，每個回應最多 32 KiB。若出現權限拒絕、版本或安裝異動、已有登記、期限到達，則停止。60 秒是取得一致觀察的期限，等待中的 OS 網路呼叫仍可能在期限後才結束。

收據包含請求與計畫摘要、本人服務帳號摘要、各物件版本、觀察時間、有效期限和讀取次數。收據本身不授予帳號使用權。準備正式切換前，確認收據完整、對應原固定請求且仍有效，再做一次新鮮核對。若本機寫檔中斷，留存原輸出和操作紀錄；重新核准的核對使用新檔名。

## 合成驗證

`python -m unittest -v test_lite_owner_review_command test_lite_owner_review_reader test_lite_owner_review test_lite_native_backend`

測試使用實際候選登入處理流程、Google Storage Python SDK 及合成 HTTP 回應。可核對憑證用途、每次讀取的物件與版本、登記存在即停止、兩次觀察中途變更及舊收據失效。本人真實 Apple 登入、雲端權限及 App Store Sandbox 購買恢復需要各自的驗收證據。
