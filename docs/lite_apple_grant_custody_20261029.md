# Apple 授權碼、憑證保護與撤銷基礎：Claude 覆核參照

2026-10-29；基準 `32735ab121770c87c7b473a06dba402a1c8fbabf`。
這是預設關閉的候選整合，沒有修改正式啟動組合、Apple 設定、雲端或商店。
目前還不是完整帳號刪除，也沒有解決 15 分鐘服務 session 到期。

## 修正前後

| 情境 | 原本 | 本輪候選 |
|---|---|---|
| 原生 Apple 回應 | 只取 identity token | 伺服器 challenge 明確要求時，另取單次 authorization code；舊服務維持原欄位 |
| Code 送出 | 沒有交換流程 | 先保存受保護的本機登入意圖，再透過 App Attest 簽署請求送到原服務 |
| Apple token 交換 | 無 | 固定 `/auth/token`、原生 client ID、ES256 client secret；固定 HTTPS、限制回覆大小與時間，不接受轉址／代理設定、不自動重試 |
| 長效憑證 | 無可撤銷 token | AES-256-GCM vault，AAD 綁 namespace、裝置／登入操作、原始 identity token、主體雜湊及原始簽發時間 |
| 交換回覆遺失 | 無處理 | 已存 vault 可讀回；未知是否消耗的 code 不重送，回明確 recovery reason，需使用者按重新登入取得新操作 |
| 撤銷 | 只有 Apple 帳號事件處理 | 新增受信任程式可用的 revoke primitive；先寫耐久撤銷意圖封鎖該 grant，成功才移除目前 ciphertext |
| 本機單次 code | 無 | pending 時存在既有 protected journal；成功或明確 recovery 後清除，目前不新增 Keychain 長效秘密 |

授權碼交換只有取得 create-only claim 的程序可以執行；同一 code 的不同登入
操作被拒。同時確認、重啟、回覆遺失不增加 Apple 交換次數。claim 提交後、
真正 POST 前若程序崩潰，仍保守當作不確定，不猜測重用 code。這是取捨，
不能宣稱外部 Apple 呼叫具備跨程序的 exactly-once 完成保證。

回覆的 Apple ID token 必須另經簽章、issuer、audience、nonce、subject 與
時間檢查。主體不符的回覆不能啟用服務；其可解析憑證仍加密保留供後續撤銷。
未取得或未能持久保存 token 的未知交換，需要新的明確登入與後續人工撤銷
處置；不能回報「所有 Apple 授權都已撤銷」。

## 可重跑測試

以 workflow 相同的依賴及 `PYTHONPATH=tools:engineering/phase2:Backend/Flask:.`：

```sh
python -m unittest -v test_lite_apple_oauth test_lite_apple_grants
python tools/lite_apple_grant_probe.py --output /tmp/apple-grant-probe.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

- 新增 Python **49 項**：真實合成 RSA／EC 簽章、AES-GCM、SQLite CAS、固定
  HTTP transport、App Attest 簽署 route；涵蓋並行、已提交後回覆遺失、尚未
  提交即失敗、subject／nonce 不符、撤銷競爭、金鑰輪替、篡改及舊版打包。
- 完整 Lite **1,256 項通過**；prelaunch **1,224 項／20 個驗證步驟通過**。
  兩個集合有重疊，不可相加；上述49項包含在兩者內。
- iOS Release **341 通過、0失敗、1項既有略過**（模擬器不能驗證裝置檔案
  保護類別）。本輪新增8項，驗證先存再送、回覆遺失、僅明確重登才換操作、
  成功清單次code、登出勝過晚到回覆、舊服務不送新欄位與錯誤來源拒絕。
- 合成整合結果：exchange **1 次**；第一次 revoke 模擬失敗，第二次成功；
  成功後重試回同一回執、不再發出 revoke。quota 逐筆不變，沒有呼叫模型。
  真實 App Attest HTTP fixture 另核對研究 privacy store 逐筆不變。

起初完整測試有5項因 sandbox 不允許 localhost socket 而失敗；放行本機
loopback 後完整通過。第二次 workflow 重播曾因拒絕覆寫舊 economics 報告而
停止；保留舊產物，改用新目錄後完成。這些失敗日誌保留，不算通過案例。

## 安全邊界與尚待完成

1. **帳號生命週期未結案**：本輪沒有服務 refresh family、長效續期、每日
   Apple refresh validation、App 刪帳 UI、全帳號多 grant 枚舉／撤銷或資料
   清理執行器。`revoke()` 是受信任內部 primitive，沒有對外新刪帳端點。
2. **研究與訂閱分開**：不清照片、深度、遮罩、研究 consent、帳本或交易。
   revoke 回執不是研究撤回、訂閱取消或帳號資料全刪除證明。
3. **未知結果**：外部交換後、vault 寫入前故障可能留下無法取得 token 的
   授權。保留未知稽核、服務保持關閉；未來刪帳程序仍須處理資料刪除並引導
   Apple 手動撤銷，不得因缺 token 拒絕刪帳。
4. **儲存與金鑰**：vault 與 audit 分開注入。程式驗證同一記憶體／SQLite
   store 不能兩用；正式 GCS bucket/prefix、IAM、保留／版本政策須另驗收。
   只移除目前 ciphertext **不代表舊版本、備份或記憶體已銷毀**。沒有執行
   GCS delete。正式 key custody、獨立 KMS／Secret、備份／復原、輪替與
   撤銷前的 token 庫存流程仍待作業方案，不能拿 SQLite 當 Cloud Run 儲存。
5. **金鑰隔離**：32-byte HMAC identity key 不可兼作 AES key；舊 key-id 換成
   不同秘密會被拒；新 keyring 可讀舊 ciphertext，但不能未經遷移刪除舊 key。
   實作注入金鑰，不讀取開發者電腦的真實憑證。
6. **相容性**：舊伺服器／challenge 的 JSON 不增加欄位；啟用新 grant authority
   後不能移除注入繞回原路徑。原生頁仍是候選 lab，不啟用公開購買或登入。
   較舊 App 遇新 required challenge 可能需要更新；正式切換需最新版本門檻。
7. **未實測**：真實 Apple capability/profile、登入、exchange/revoke、Sandbox
   S0–S9、實機鎖定時的 journal 保護均待驗收。Android沒有本輪新增功能。

## 覆核重點

- `lite_apple_oauth.py`：固定端點、form encoding、client secret、回覆上限、
  不重試一次性交換；錯誤文字不可包含外部 error_description／token。
- `lite_apple_grants.py`：code claim／加密 vault／revoke intent 的寫入順序，
  CAS 回覆遺失、金鑰缺失／錯誤時 fail closed；不能把 grant receipt 當帳號刪除。
- `lite_account_identity.py`：只在明確注入時要求code，保留原 nonce／session
  範圍與App Attest；移除authority後仍拒絕；不靠 Apple subject/email 前端值登入。
- `LiteAccountIdentity.swift`：protected journal先於網路、成功後去除單次code、
  恢復不自動發新登入操作、晚到回覆不能重啟已登出服務。

## 一手參照

- [Apple Token validation](https://developer.apple.com/documentation/SigninwithAppleRESTAPI/Generate-and-validate-tokens?changes=_7)：
  code 一次性、五分鐘有效；native 未提供 redirect URI 時不另外加入；token
  exchange 與 refresh 的回傳欄位不同。本輪只實作 authorization-code exchange。
- [Apple Token revocation](https://developer.apple.com/documentation/signinwithapplerestapi/revoke-tokens?changes=l_1)：
  使用 refresh/access token 撤銷；已失效 token 也回200空本文，故撤銷可在
  回覆遺失時重試。本輪不把 HTTP400 視為成功。
- [Apple TN3194](https://developer.apple.com/documentation/technotes/tn3194-handling-account-deletions-and-revoking-tokens-for-sign-in-with-apple)：
  安全保存 token，缺少 token 仍須完成帳號刪除處置；refresh validation 與
  App 刪帳、Apple 撤銷、訂閱取消應分開驗證。

本輪原始資料與稽核不變，不宣稱醫療精確度、法規豁免、已部署或通過商店審查。
