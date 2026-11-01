# Lite 帳號刪除受理與授權撤銷協調（2026-10-29）

此候選接續 `4d093076190667536ec2e09195f6c9e0c20f4951`，只做本機合成驗收。預設啟動不載入新後端協調器，也不顯示新的原生帳號頁；不能當作完整刪帳、法規符合或可上架的證明。原始研究影像、遮罩、深度與稽核未變更。

## 變動與前後差異

| 面向 | 父來源 | 本候選 |
|---|---|---|
| 帳號關閉 | 可信內部 primitive + 清冊 | 明確注入才提供 App Attest POST request/status |
| Apple 撤銷工作 | 呼叫單一 grant primitive | 凍結清冊分批遍歷、120 秒租約、CAS 續接與逐筆稽核 |
| 原生流程 | 沒有刪帳入口 | 可注入的帳號頁、二次確認、要求先寫耐久 journal 再送 |
| 重開 App／遺失回覆 | 無原生刪帳 checkpoint | 沿用原 operation；已受理後只查進度，不新增要求 |
| 一般服務 | 後端 closing 阻擋 | 本機 intent 一寫入即阻擋登入／續期／一般服務，跨重啟保持 |
| 完成含義 | account_deleted=false | 仍 false；Apple 撤銷、帳號資料清理、訂閱取消分開顯示 |

## 路由及授權邊界

- `POST /api/v1/lite/account/deletion/request`：嚴格 JSON `anon_id, operation, confirmation=delete_account`；第一次需有效的同一服務帳號登入。
- `POST /api/v1/lite/account/deletion/status`：嚴格 JSON `anon_id, operation`；只允許原提出裝置與原 operation，帳號已 closing 或原 session 過期後仍可核對。
- 兩者必須是同一既有 App Attest installation，用 `withdraw` challenge 及獨立隱私 request budget；一般 POST 不能借用它，無簽章或其他 owner 被拒。研究同意不是刪帳路徑的前提，也不會因刪帳被自動恢復。
- `deletion=None` 為預設，只有 trusted coordinator、相同 identity bridge／lifecycle／grant authority 的精確注入才掛載。舊 Lite 映像不打包此模組，隔離啟動與打包測試仍通過。
- 不提供公開 worker API、排程、自動重試、部署或清理命令。

## 續接與競爭

工作以 scope/person 綁定的耐久 checkpoint 保存不可變 plan（原 requester、operation、時間、inventory hash/head/count），不能由用戶端指定 cursor 或 grant。每批 1–100 筆；一次 grant 失敗仍處理同批其他 grant。

拿不到 claim 或無法確認 claim 寫入時不呼叫 Apple。每筆呼叫前及保存進度前核對租約；晚到的舊 worker 不可覆寫新 checkpoint。provider 成功而 audit／checkpoint 回覆遺失時，先前 grant 的耐久 revoked 狀態可讓下一輪續接不重做已確認撤銷。Apple 外部回覆不明時，重試 revoke 仍可能發生，**沒有聲稱外部 API 恰好一次**；不會重換 authorization code、不跑模型、不再扣辨識額度。

`revocation_scan` 區分掃描輪次；pending 重掃可能把本輪 examined/revoked 計數歸零，不代表授權恢復。逐次嘗試與完成掃描回執保留；只有全部 grant 確認才能顯示 Apple 撤銷 confirmed，帳號資料刪除依然 pending。

## 原生狀態與畫面

`LiteAccountManagementView(identity:service:)` 強制共用 identity 的受保護 journal。入口需明確注入 `LiteRootView(accountManagement:)`，一般啟動維持 nil。UI 顯示服務帳號登入、刪帳二次確認、原進度核對及 Apple 訂閱管理連結。

- `requestConfirmed()` 是唯一初次受理動作；onAppear 只讀本機，不啟動刪帳、不自動登入。
- 先保存 context/original operation，再 fence 一般服務，最後傳送；寫入失敗不傳送。
- 回執只接受本版 pending contract，嚴格核對 owner、operation、inventory、時間、scan／計數單調性。不能把未實作的 complete receipt 當成成功。
- 已受理的 status 核對不需要恢復普通 session；原照片、研究設定、訂閱及額度不被修改。

## 明列尚未完成，禁止直接啟用

1. **首次送達前登入過期／先被撤銷**：目前會回 401；原生保留原 intent、停止一般服務、顯示需要身分核對，不會一直自動重送。刪帳專用 reauthentication 尚未實作，不能用一般登入解除 intent、換身分或清掉紀錄。下一階段需同一 account + operation 綁定、獨立短效 challenge；登入成功只推進原刪帳，不能重新啟用服務。已受理的回覆遺失／session 過期續接已驗證，不能與未受理前過期混為一談。
2. **完整資料處置**：尚未建立服務結果、Apple 憑證歷史版本、帳號關聯及研究資料適用處置的完整清冊／執行器。撤銷 token 不等於刪除伺服器資料；本機紀錄不自動刪除。
3. **晚寫入**：closing 與最後結果 CAS 仍非跨物件原子交易；資料清理需要 writer fence、排空、重查與版本核對，不能僅刪目前物件或單靠 grant 清冊。
4. **研究與訂閱**：研究撤回保持獨立；Apple 訂閱需取消，UI 不宣稱自動取消。完整刪帳必須有研究資料的處置說明與必要保留依據，不能只保留資料卻宣稱已刪除。
5. **正式執行**：真實 Apple Sandbox、原生登入組裝、GCS provider／版本清理、舊 vault 遷移、金鑰保管及刪帳專用身分復原未驗收。外部部署、清理及商店更新仍需另行方案與授權。

## 驗證方式

以真實 Flask 路由、合成 App Attest 簽章、真實 SQLite CAS 及替身 Apple provider 驗證；沒有真實 Apple／GCS 網路呼叫。`test_lite_account_deletion.py` 含 34 項：受理與重播、跨裝置／operation 拒絕、首次過期拒絕、126 筆分批、雙 worker、租約逾時、回覆遺失、稽核寫入失敗、corrupt fail-closed、研究撤回獨立、withdraw budget、一般 challenge 不得進刪帳通道等。

`tools/lite_account_deletion_probe.py` 先成功辨識一筆合成資料，再受理刪帳、注入 audit outage、續接與讀回。預期模型 1 次、已確認 Apple revoke 1 次、原服務結果／額度／研究／registry 保持相同，仍 account_deleted=false。

原生 XCTest 包含 journal 先存後送、同 op 重試、未受理／已受理分流、重開 App、明確 401、錯誤回執拒絕、intent 阻擋跨實例／晚續期、App Attest 通道對照，以及候選畫面截圖。模擬器通過不能代替真實裝置檔案保護或 Apple Sandbox 驗收。

參照：
- [Apple 5.1.1(v)：App 內刪除帳號](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion)（2026-10-29 核對）：只停用不足；可要求身分再核對；應說明資料保留與訂閱取消。這是設計依據，非此候選已符合全部要求的宣稱。
- [Draft PR #36](https://github.com/JackH0001/WoundAI_Proj/pull/36)（新候選尚未推送，遠端父來源 CI 不代表本輪 CI）。
