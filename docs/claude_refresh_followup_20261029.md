# Claude 4d09307 覆核接續：refresh、讀取成本與待辦邊界

本輪基準為本機 `98517e1`（刪帳受理候選），它的父來源為已公開的 `4d09307`。本輪新增修正只在本機；`98517e1` 的推送授權不包含這些新檔案。

## 修正前後

| 項目 | 修正前 | 本輪結果／限制 |
|---|---|---|
| 每日 refresh 一次網路逾時 | 未知結果成為永久 uncertain；下次要求明確 Apple 登入 | 僅 timeout、connection、DNS 類別，在同一耐久 CAS claim 內最多 3 次嘗試，間隔 1、2 秒；成功後每天不重複驗證。一般格式錯誤、TLS驗證失敗、invalid_grant不重試。 |
| authorization code | 單次交換，失去回應不可重用 | 保持不變；沒有把 refresh 的可重試性套到 code。 |
| 授權證據讀取 | 每次 resolve+grant 19 次 SQLite load | 預設仍19次；明確啟用、暖快取時17次。只快取通過雜湊核對的兩份不變稽核證據，最多64筆／行程。active/paused、帳號及額度仍即時核對。 |
| 金鑰刪除 | 移除目前ciphertext不清舊副本 | 合成反例證實舊備份仍可解密。已寫保管／復原／銷毀作業規格，真正每帳號KMS、金鑰清冊及清理仍未實作。 |
| 原生刪帳 | 4d09307沒有入口 | 本機98517e1已有預設關閉的受理／確認／進度與訂閱引導；不是完整資料刪除。首次受理前登入過期復原仍待辦。 |

刷新重試的邊界：成功驗證最多每日一次；網路失敗可能已到達Apple，因此不能聲稱Apple每日只收到一次HTTP。三次用盡、程序在外部呼叫時死亡、未知回覆或回應憑證無法持久保存，仍不能自動重建登入或重置額度。每次派送前寫入不含憑證的稽核；並行請求無法各啟動一組重試；撤銷／關閉會在重試前阻擋，遲到的合法憑證仍依舊流程保管以供撤銷。

Apple 官方區分authorization code交換與refresh驗證，並建議每日最多一次refresh驗證；本輪選擇的1、2秒退避與3次上限是工程政策，不是Apple保證。真實Sandbox仍未驗收。來源：[Token validation](https://developer.apple.com/documentation/signinwithapplerestapi/generate-and-validate-tokens)、[TN3194](https://developer.apple.com/documentation/technotes/tn3194-handling-account-deletions-and-revoking-tokens-for-sign-in-with-apple)。

## Claude 原探針如何解讀

原始 `test_claude_probe_4d0.py` 在目前來源上原封不動重跑，確認19次讀取及原始refresh問題。它用通用 `GrantUncertain` 代表網路逾時；本輪刻意不把所有未知錯誤都當可重送。因此修正後這個「無細分類的未知結果」反例仍會要求明確復原，**不可把原探針列為全部問題已修**。新增 `test_lite_apple_refresh_retry.py` 與 `tools/lite_refresh_review_probe.py` 從真正provider的transport邊界注入TimeoutError，驗證逾時一次後成功，兩次HTTP嘗試、一個每日成功驗證，原帳號及額度不變。

`tools/lite_refresh_review_probe.py` 以同一個合成fixture交錯跑100次基準／暖快取。p95只代表本機SQLite與Python計數包裝，不能換算雲端p95、流量或營運節省。完整登入／辨識還有其他讀取。17次不是目標上限，也沒有快取其餘可變狀態來追求更漂亮的數字。

快取預設關閉，正式啟用前需確認稽核物件是以內容雜湊固定、無人可覆寫，並驗收暫停跨instance立即生效及不同key/manifest隔離；已快取的歷史證據不因稽核暫時讀不到而失效，但新世代仍需新證據。控制平面、帳號關閉、撤銷及身分金鑰狀態不適用這個快取。

## 醫療原探針與未結案項

已在既有交接目錄找到 `claude_ba9_probes_20261022.zip`，完整保留原檔，逐一讀過後重跑其中三支合成HTTP探針（6個案例）。它們主要輸出觀察，不是6項問題修好：

- ba970b1-N1仍重現：同意事件讀取失敗被回報400「已撤回」，恢復後同一請求200。
- ba970b1-N2仍重現：同意JSON損壞時撤回500，沒有新增意圖稽核。
- L1仍重現：A撤回、B撤回、再送A回409 superseded。
- N4清理回執去重及L2等待期限沒有包含在這份zip的可執行探針內。L2現有Swift雖已有保存縮短期限的程式，仍不以看到程式就宣稱原探針驗收完成。

這些項目保留真實病患研究部署阻擋標記。下一個獨立變更應先做N1/N2/L1的HTTP契約與雙平台outbox驗收，再核對N4晚到物件和L2重啟保存；本輪未修改醫療後端。

## 可重跑驗證

- Python：`python -m unittest -v test_lite_apple_refresh_retry test_lite_release_proof_cache`（17＋13項新增案例）。
- 全套：`python -m unittest discover -s engineering/phase2 -p 'test_lite_*.py' -v`；依 `.github/workflows/lite-prelaunch.yml` 重跑，數量彼此重疊。
- `python tools/lite_refresh_review_probe.py` 輸出合成讀取次數／延遲、原帳號額度未變及舊密文備份反例。
- 在Mac本機逐步執行 `.github/workflows/lite-commerce-lab.yml` 的Debug與Release命令；結果：126及115項通過，零失敗／略過。這是本機StoreKit配置，不是真實Apple Sandbox，也不是手動觸發遠端workflow。
- 完整金鑰規格：`docs/lite_key_custody_runbook_20261029.md`。

本輪不合併、不部署、不更新商店、不刪除原始資料／稽核；新來源推送須另行固定範圍授權。
