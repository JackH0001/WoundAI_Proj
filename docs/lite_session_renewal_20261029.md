# Lite：受 App Attest 保護的短效連線續期（2026-10-29）

本輪以 e9875d3 為基準，僅本機候選；不啟用正式服務，不等於 Apple Sandbox 或 App 內刪帳完成。使用者原影像、深度、遮罩、研究同意及已用額度均不更動。

## 修正前後

| 情境 | e9875d3 | 本輪候選 |
| --- | --- | --- |
| 15 分鐘到期、Apple 授權仍有效 | 雲端讀回亦需重新登入 | iOS 先保存續期請求，再以同一安裝的 App Attest 簽署續期，無新 Apple 提示 |
| 續期回應或本機存檔回覆遺失 | 無續期路徑 | 以原 operation／generation 取得同一回執，不再延長、不重扣額度 |
| 登出／新登入／Apple 撤銷在續期中發生 | 無續期競爭 | 舊登入 family 不可恢復；在發送前、CAS 更新前後及 iOS 保存前後核對 |
| Apple refresh 驗證無法確認 | 未實作 | 不自動重送可能已使用的請求，保留憑證及稽核，要求明確重新登入 |
| 本機舊紀錄或 PDF | 不需服務登入 | 維持本機可讀；不以重新登入觸發研究重送 |

## 邊界與流程

1. `AccountIdentityBridge(renewal=True)` 為受信任組合選項，預設 `False`；必須同時有 grant custody 與 Apple account events。耐久 marker 不允許移除注入後默默回到舊驗證方式。尚未接正式 startup。
2. 不向 App 發放 Apple refresh token。App 只保存登入 operation、當前 generation 與續期 operation；每個 HTTP 請求仍須現有 App Attest 綁定安裝與 body。這是服務 session family 的版本輪替，不是聲稱 Apple 每次都輪替 refresh token。
3. 服務連線每次最長 900 秒，剩 60 秒以內或已到期才續期。family 最長為原 challenge 起算 30 天，屆滿需明確重新登入；這是候選安全政策，非 Apple 規範。family 到期也不清照片／遮罩／額度。
4. 每次更新以 expected generation CAS；同一續期 operation 綁定原意圖。重播不延長期限；較新 generation 已成立後，舊請求不可改用新 generation。同帳號不同裝置使用各自 family，帳號與額度仍沿用原映射。
5. 續期不重新建立訂閱、日額度或研究收案。未修改 `ServiceOperations` 的模型／結算／結果語意。
6. Apple 授權以初次授權時刻及最近一次已驗證 refresh 的時刻，最長每 24 小時重新核對一次。短效 session 到期不能超過這個核對邊界。每次服務請求仍核對登出、grant 撤銷、原 Apple 授權時刻的 account-event fence 與安裝歸屬。
7. Apple refresh 固定走 `/auth/token`，無 redirect／proxy／自動 retry。驗證簽章、issuer、audience、subject、時間；refresh 回覆若帶 nonce 必須符合原 nonce，不要求 Apple 必定重帶 nonce。若回覆省略 refresh token，保留原 token；若提供新值，加密更新。
8. 刷新中的耐久 claim 只有一方可發送；其他請求暫時 503。逾 120 秒仍無結果改為明確登入恢復，不猜測成功。Apple invalid_grant、身分不符或回覆未知不恢復服務；保留可供撤銷的憑證。
9. Apple 公鑰快取採原受信任 rotating verifier，refresh 路徑也核對 key snapshot，不直接讀未初始化的基底 verifier 欄位。
10. 登出／撤銷同時進行時，晚到的 refresh 不得覆寫 grant tombstone。已有稽核及原始 `issued_at` 不用新的 ID-token iat 替代。這防止 refresh 繞過帳號事件。
11. `/account/renew` 同時加入後端與 iOS 的 App Attest 路徑規格，仍為 POST + exact fields；沒有放寬未簽署入口。iOS 先 durable journal 再網路，失敗／重啟沿用同一操作；401 明確要求登入與 503 暫時失敗分開。

## 驗收覆蓋

- 新 Python 44 項：session 40、OAuth refresh 4。包含並行同操作／不同操作、跨日、回覆遺失、稽核失敗、過期重播、不同裝置、錯誤 subject／nonce、key refresh、logout／event／revoke 競爭，以及實際 Flask + 合成 App Attest 驗證。
- 新 XCTest 13 項：保存前發送防護、過期連線自動續期、重啟重試同 ID、回執保存失敗、登出競爭、錯帳號回執、family 到期及 HTTP request／recovery 語意。
- `tools/lite_session_renewal_probe.py` 使用實際 Flask guard、合成 App Attest、暫存 SQLite、合成 JPEG 和注入模型。讀回同一舊結果後模型總共 1 次、額度列不變、研究列不變。fixture 在新工作前更新自己的舊式 migration observation；本探針不宣稱驗證正式 durable controller／observer HA。
- 完整 Lite 1,300 項通過；iOS Release 354 通過、0 失敗、1 項既有實機 file-protection 略過。Prelaunch 與全套測試有重疊，不能相加。
- 未修改 Android。正式 Apple、GCS、多實例服務、裝置登入及真實續期仍待後續限定驗收。

## 限制及上市剩餘門檻

尚無 App 內完整刪帳、全帳號 grant inventory／撤銷／資料處置；目前仍不能宣稱 account deletion 完成。加密 vault 的 KMS／Secret 權限、版本保留／備份銷毀、復原演練，以及實際 Apple capability／token response／Sandbox S0–S9 仍待完成。未知 refresh 無法確定 Apple 是否輪替 token，必須保留人工撤銷恢復途徑；不得因此拒絕使用者刪帳。30 天、每日驗證與網路故障恢復政策須經正式組合驗收。

HTTP 授權檢查是動作前的有限時間快照，不是跨 Apple／GCS／quota 的分散式交易；已開始的辨識工作不會因本輪登入續期重做或抹除。正式部署仍須驗證控制器、權限與正在執行工作的停止邊界。

## 重跑

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. python -m unittest -v test_lite_account_sessions test_lite_apple_oauth
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. python tools/lite_session_renewal_probe.py --output session-renewal-probe.json
```

依專案相依環境執行；完整可攜流程見 `.github/workflows/lite-prelaunch.yml`。合成 probe 不會呼叫真實 Apple、GCS 或使用病患資料。

## 官方參照

- [Apple TN3194](https://developer.apple.com/documentation/technotes/tn3194-handling-account-deletions-and-revoking-tokens-for-sign-in-with-apple)：安全保存 Apple 憑證、每日驗證 refresh 以管理服務登入期、撤銷與 App 內刪帳分開核對。
- [Apple Token validation](https://developer.apple.com/documentation/signinwithapplerestapi/generate-and-validate-tokens)：authorization_code／refresh_token 流程；不是把舊 ID token 的 exp 自行延長。
- [本輪前置 grant custody](lite_apple_grant_custody_20261029.md)。
