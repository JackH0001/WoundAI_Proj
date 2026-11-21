# 本人首次安裝的個人服務裝置登記

這是供本人內測的新安裝步驟。個人服務從 App Attest 收到並驗證證明後，才會指派安裝識別碼。本人首次登入的核對方案需要這個安裝識別碼的摘要。為了讓新安裝取得它，先建立一個短時段，只接受這支手機已準備好的公開金鑰識別碼。

## 手機操作與交接

1. 本人候選組態設 WOUNDLITE_OWNER_REVIEW=YES。進入設定的個人服務頁，若已確認裝置登記，直接使用既有安裝進入[首次本人登入核對](lite_owner_review_command_20261118.md)。
2. 若尚未登記，按「準備本人裝置登記識別碼」。App 在鑰匙圈保存相同的 App Attest 登記進度，以小寫十六進位顯示 32 位元組的公開金鑰識別碼。可透過「分享裝置登記識別碼」交給自己的 Mac。這步只準備金鑰，不向候選服務送出登記、照片或 Apple 登入資料。識別碼只用於私下核對預期的安裝。
3. 操作員依手機提供的識別碼固定後端設定與期限。待核准的登記時段就緒後，回手機按「核對裝置登記（不傳照片）」。收到回覆後，帳號頁才可匯出安裝摘要。公開金鑰識別碼、伺服器配發的安裝識別碼、登入操作編號各有不同用途。
4. 若回覆遺失，保留 App 和鑰匙圈。已保存的證明可向原服務重送以找回同一份登記。如遇未完成證明而需換金鑰的情況，重新準備識別碼並核對新方案。過期的登記窗口需由操作員另行處理。

## 固定候選修訂

使用原個人服務的網址與同一份經核對的 native 設定原始檔。限制於尚未開放既有使用者流量的個人候選服務。研究服務的登記、撤回與手機紀錄繼續使用原流程。一般外測不採用這個限定本人登記模式。

啟動入口沿用 lite_native_startup:application()。LITE_INTERNAL_OWNER_MODE 設為 owner-enrollment。LITE_NATIVE_CONFIG、LITE_NATIVE_CONFIG_SHA256、LITE_NATIVE_SOURCE 與 LITE_NATIVE_IMAGE_SHA256 都必須對應已覆核的設定、提交及映像。額外提供 LITE_OWNER_ENROLLMENT_PLAN（新計畫的絕對路徑）與 LITE_OWNER_ENROLLMENT_PLAN_SHA256（原始位元的 SHA-256）。

計畫僅接受以下欄位。

| 欄位 | 內容 |
| --- | --- |
| schema | lite-owner-enrollment/1 |
| native_sha256 | 原個人候選設定的 SHA-256 |
| key_id | 手機提供的 64 位小寫十六進位識別碼 |
| service_account | 本次登記專用的服務身分，與之後的完整個人服務身分分開 |
| starts、until | UTC epoch 整數秒，同一 UTC 日，最長 15 分鐘，結束時間不含在內 |

同時提供私人金鑰目錄、帳號綁定、研究關聯或啟用研究拍攝時，服務拒絕啟動。正式執行僅掛載這兩份已固定的設定檔。部署應逐項讀回所有掛載與環境設定；驗證完整啟動後才切換流量。這個版本只註冊登記挑戰與登記確認兩條業務路由。登記成本上限為每分鐘 6 次、同一 UTC 日 12 次符合金鑰的登記請求，兩個路由共用同一份耐久計數。這是本人登記工具的限制，與免費或 Plus 的辨識額度無關。

## 權限與恢復

在 asia-east1 以指定身分讀回專案、區域、儲存桶設定及 metageneration。沿用 woundai-lite-native-state-<project_number>；只使用兩個可推導的物件。第一個是 native.stores.security.prefix 加上 /<key_id>.json。第二個在前述 security 的同一層 owner-enrollment-budget/ 下，檔名是 UTF-8 字串 woundlite/global-request-budget/1 的 SHA-256 加 .json。同一日重新啟動會沿用預算。正常登記只在原 security 物件從待確認推進為已登記。

正式授權方案應逐一列出上述物件的完整名稱、有限期的物件讀取／建立／覆寫權與讀取桶設定權，另行查核繼承權限。GCS 取代同名物件需要允許覆寫的權限，服務採用 generation 條件。時段內已送出的 GCS 操作可能在期限後才傳回，期限邊界也應由實際 IAM 條件及雲端讀回驗收。專用身分無需研究桶、訂閱、KMS 或 Apple 私鑰權限。每分鐘及每日上限不涵蓋外圍基礎設施收取的網路費用。

成功後先關閉登記流量、收回專用權限，保留登記與稽核，再以手機匯出的安裝摘要規劃限時 owner-review。失敗時封閉登記入口、保留既有登記進度與原始資料。查明結果後才依原金鑰與原證明續接。本機合成測試涵蓋了回覆遺失、重啟、到期、新安裝轉入本人核對。雲端授權、真實 Apple 裝置登記、實際計費及 TestFlight 交付仍需獨立驗收。

## 在 Mac 準備登記計畫與權限清冊

`tools/lite_owner_enrollment_command.py` 可以將本人手機的分享文字轉為後端可直接讀取的設定。這項作業可在登記時段開始前進行。需要已核對簽章的本人專用 IPA、原始 native 設定、本人分享的純文字識別碼及經覆核的小型 JSON 範本。工具可在離線環境執行。識別碼請保存在私人目錄，避免寄到一般信箱或貼入對話。

範本的 `schema` 為 `lite-owner-enrollment-template/1`。各欄位來自下列證據。

| 欄位 | 來源及格式 |
| --- | --- |
| `native_sha256`、`source`、`image` | 設定原始位元的 SHA-256、後端 40 位提交、私有映像 64 位 SHA-256（移除 `sha256:` 前綴） |
| `ipa_sha256`、`app_id` | 已核對簽章的本人 IPA 雜湊與完整 application-identifier，例如團隊代碼加上 `.com.woundai.lite` |
| `key_id_sha256` | 在手機與 Mac 私下核對一致的 64 位十六進位裝置識別碼後，將其解碼為 32 個原始位元組，再取 SHA-256 |
| `service_account`、`starts`、`until` | 登記專用服務身分、同一 UTC 日的起迄整數秒，最長 900 秒 |

將範本原始檔的 SHA-256 留在獨立覆核紀錄。工具會讀取既有 IPA 的內容並比對該檔雜湊、本人開關、內測限定、裝置平台、服務網址與組建。簽署與團隊的信任來源是先前對該固定 IPA 的發行驗證。範本、識別碼及新輸出放在本人所有的 0700 目錄，輸入文字檔權限為 0600。分享文字可含一個 LF 或 CRLF 結尾。

以下在 repo 根目錄使用 zsh。變數代表您已核對的檔案絕對路徑，`PLAN` 與 `SCOPE` 各使用新的輸出名稱。

```zsh
export PYTHONPATH=tools:engineering/phase2:Backend/Flask:.
inputs=(--native-config "$NATIVE_CONFIG" --template "$TEMPLATE" --template-sha256 "$TEMPLATE_DIGEST"
        --ipa "$OWNER_IPA" --reference "$PRIVATE_DEVICE_TEXT")
python tools/lite_owner_enrollment_command.py prepare "${inputs[@]}" --output "$PLAN"
python tools/lite_owner_enrollment_command.py scope "${inputs[@]}" --plan "$PLAN" --output "$SCOPE"
```

保留 `prepare` 的原始輸出供掛載，使用該檔 SHA-256 填入 `LITE_OWNER_ENROLLMENT_PLAN_SHA256`。`scope` 會重建設定，逐位元比對已存的計畫，再寫出桶名、metageneration、兩個物件全名、兩條 HTTP 路由、角色所需權限及起迄時間。物件的 DELETE 權限用於 GCS 的條件覆寫。到期後可用既有計畫重新產生清冊，核對須收回的物件與期限；清冊會標示 expired，並保留原截止時間。後續操作須將物件權限精確限於這兩個名稱及期限，並分別查核有效 IAM。確認送件回覆遺失時應保留原安裝與登記紀錄。外部測試版使用一般登記流程。
