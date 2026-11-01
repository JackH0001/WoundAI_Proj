# Lite：全帳號授權清冊與刪除中防護（2026-10-29）

基準 `8e36672`。本輪是預設關閉的後端候選，**不是完整刪帳完成**。
未增加公開刪帳 API／App 按鈕，未啟用正式服務、移轉或清理真實資料。

## 修正前後

| 情境 | 基準 | 本輪 |
| --- | --- | --- |
| 同帳號多裝置／多次登入 | 只有單次登入 grant，不能證明清冊完整 | 每個新 grant 在 Apple exchange 前加入同帳號耐久清冊 |
| 刪除意圖與新授權競爭 | 沒有跨裝置帳號關閉狀態 | 清冊根節點 CAS 串行化新增與關閉；先關閉者禁止新增，先加入者保留在凍結清冊 |
| 帳號關閉後 | 只有單裝置 logout | 登入、續期、服務結果讀取、新辨識與購買 context 核對同一帳號狀態 |
| grant 更新與撤銷同時發生 | 可先撤銷舊 token，再丟失晚到的新 token | refresh 進行中不宣告撤銷完成；晚回覆加密保存，驗證後才可撤銷最新憑證 |
| refresh 回覆身分不符 | 保留舊憑證，但新回覆沒有獨立保管 | 新回覆先加密保存；驗證失敗不授權，也不能只撤銷舊 token 就宣告完成 |
| 逾 100 筆 grant | 無全帳號列舉 | 合成 126 筆、每頁最多 100 筆，原順序及集合一致；不受先前訂閱批次 100 人限制 |
| 舊 vault 開啟新模式 | 未定義 | 明確拒絕無清冊舊 vault，不能把目前登入當成全部 grant；需另做受限遷移 |

## 實作邊界與資料結構

`AccountLifecycle` 使用每個已驗證 Apple identity 一個 CAS 根節點、不可變 linked
grant node、membership 收據與 enrollment anchor。每個物件仍遵守現有16 KiB
儲存限制；每帳號最多10,000筆是維護界線，達上限停下，不丟棄舊授權或偷偷重設。
一般服務授權只讀該帳號根節點及該 grant 收據，不掃描全庫。正常新增只需固定
次數讀取；遇到同時新增或已提交但回應遺失時，才從已保存 anchor 核對此帳號
新加入的節點。失敗的 CAS 可能留下不可達準備節點；它不是已授權 grant，
未做清理，必須納入後續資料處置清冊。

`AccountIdentityBridge(lifecycle=...)` 明確組合相同 `AppleGrantAuthority` 與
`ServiceAccounts`。durable markers 防止目前程式的其他實例移除元件後繞過防護。
不主張這能約束部署前的舊版二進位；正式切換仍須停止舊 writer 並核對 IAM。

舊 vault 可能有「Apple exchange 成功但登入未完成」的 grant，不能只查看
human-account 表判定沒有舊資料。因此已有 vault config 而沒有清冊 marker
時，候選建構器拒絕啟用。此限制不能靠換空桶、換 namespace／identity key
或刪除 config 規避；正式啟用前須盤點所有既有 vault、索引及 writer，提出
有上限、可回讀的遷移方案。本輪只用全新合成 vault 測試。

`begin_account_closure` 目前是受信任協調器 primitive：首次要求有效服務身分；
之後同一 requester／operation 可重讀同一意圖，不需恢復正常服務登入。
原 App Attest 驗證仍是未來 HTTP 入口必要條件；本輪沒有新增可呼叫的刪帳入口。
回執固定標示 `account_closing`、`account_deleted=false`、
`data_disposition=not_started`、`apple_revocation=not_completed`。
不能把它改寫成「已刪除」，或把停止服務等同於 Apple 取消訂閱。

研究撤回路徑保持獨立；關閉帳號不替使用者重新同意研究、不重設已用額度、
不刪除本機照片／遮罩／PDF。此階段的保留是為後續可稽核處置，並非永久保留
所有個資的政策。身份雜湊及安裝識別仍可能可連結，不宣稱匿名。

## Apple refresh／revoke 競爭

1. 先寫撤銷意圖，再檢查 validation 狀態。`validating`／`uncertain` 不宣告完成。
2. 更新持有者取得回覆後，先將完整回覆加密保存到 vault 的獨立 output object，
   再驗證 identity。output 不是授權；沒有原始 token 出現在一般 audit。
3. 同時關閉或撤銷時，正常驗證的晚回覆仍保留供後續撤銷，但不恢復服務。
4. 未發出外部 refresh 就被關閉時，將該 claim 標為拒絕，避免無故卡住原 token
   的撤銷。已發出但回應不明／新身分不符時保持待處理，不自動重送 refresh。
5. 無法確認的 token 與 vault 寫入失敗仍需要獨立恢復程序；**不能因此阻止
   帳號資料刪除**。兩個階段在完整協調器中必須分開；本輪尚未實作資料刪除。
6. 獨立 output／目前 ciphertext／歷史版本／備份都屬後續憑證處置範圍；只
   清除目前 grant envelope 不能宣稱全部憑證銷毀。

## 競爭與剩餘限制

服務辨識在 claim 後、模型回傳後及結果讀取時核對關閉狀態。合成測試中，
在 inference 內觸發關閉後沒有保存或交付新結果，重試不再執行模型。
但關閉與最後一次結果 CAS 之間仍不是跨物件交易：可能留下不可讀的晚寫入；
完整清理必須搭配工作清冊、writer fencing、進行中工作排空及再次掃描指定
範圍。不能以本輪測試宣稱所有已在執行中的服務寫入都已被原子取消。

仍需完成：

- App 內清楚的刪帳說明／確認／進度；即使 Apple grant 已撤銷或失效，也要有
  適當身分核對後的刪帳路徑，不能要求重新同意研究或購買。
- 完整資料處置目錄：安裝／身分／訂閱關聯、服務工作與結果、研究 epoch、
  credential output、索引、稽核必要保留資料、所有版本與備份。
- 有上限且可恢復的多階段清理執行器、全部工作階段關閉與晚寫入核對；本輪
  不把合成單 grant revoke 當成全帳號清理。
- 刪帳不等於 Apple 取消訂閱；需管理訂閱引導及立即刪帳選項。
- 正式金鑰保管、舊 vault inventory 遷移、真實 Apple/Sandbox、GCS 與實機驗收。

## 驗證與重跑

新增48項合成測試：跨裝置、舊登入、126筆清冊、CAS競爭、回應遺失、索引損壞
隔離、舊元件防護、實際Flask/App Attest路徑、獨立研究撤回、晚到refresh與
加密保管。既有一項測試更新為較嚴格的「先等更新結果再撤銷」，不是略過失敗。

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. python -m unittest -v test_lite_account_lifecycle test_lite_account_sessions
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. python tools/lite_account_closure_probe.py --output closure.json
```

Windows `PYTHONPATH` 分隔符改為分號。完整套件仍依
`.github/workflows/lite-prelaunch.yml`。測試用生成的簽章、合成 JPEG、暫存 SQLite，
不呼叫真實 Apple／GCS。完整本機結果由隨 commit 的外部覆核包提供；新來源
尚未推送前不能沿用父來源的綠色 CI 當成本輪遠端驗收。

## 官方依據

Apple要求支援帳號建立的App提供App內啟動刪帳，只停用不足；應說明處理時間、
依法保留資料與訂閱處理，Sign in with Apple需相應撤銷token。本候選僅處理
其中的授權清冊與關閉競爭，未達完整規範。
[Apple 5.1.1(v)（2026-10-29核對）](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion)

### 同探針前後比較

`tools/lite_refresh_revocation_race_probe.py` 已對隔離匯出的 `8e36672` 與本輪
候選執行相同競爭：refresh 發出後先要求 revoke，再回傳一個新的 refresh token。
基準回報已撤銷，但只撤銷舊 token、新 token 未保留；候選先回報待處理，保存
晚回覆，再撤銷新 token。兩者皆未恢復登入，證明修正的是憑證處理完整性，
不是把既有登入阻擋誤報為新的修正成果。這是合成 token rotation，並非聲稱
Apple 每次 refresh 必然輪替 token；真實 Apple 行為仍須 Sandbox 驗證。
