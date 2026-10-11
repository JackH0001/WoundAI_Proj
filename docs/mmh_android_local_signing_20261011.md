# MMH Android 本機上傳簽署交接

## 本輪可執行範圍

既有 upload key 由使用者指定；不新增金鑰、不讀取或產生 keystore.properties、不上傳 Play、不變更 Google OAuth／IAM。此前核對的未簽署 MMH AAB SHA-256 為 `1b59c732c8a3ee7a21f680eef24debb21558438045c5e8b1cdee7dc7146c563e`，來自手機來源整理 `923384cb79c0b0f9c0a9b55a719b270345ac977d` 的同一份建置內容。

工具 `tools/sign_mmh_aab.py` 簽署已核對且雜湊固定的 AAB，不重新建置。發布配置／套件名的證據來自該建置的驗證；工具本身檢查 AAB 結構、ZIP 完整性及固定雜湊，不另外解析 protobuf manifest。不可把另一份任意 AAB 的 hash 當成已完成發布配置核對。

## 操作

執行為本機準備的 `Sign-MMH-Android.command`，在 Terminal 依序輸入既有 keystore 密碼、選擇其上傳金鑰編號，再輸入既有 key 密碼（若相同可留白）。密碼不顯示，請勿貼到對話。

只讀取現有金鑰；密碼只在本機 Python 記憶體及必要的 keytool／jarsigner 子程序環境存在，不放入命令列參數、設定檔或報告。這不保證同一使用者／管理者的程序偵錯工具無法存取記憶體；完成後程序結束。

工具會：

1. 核對指定 SHA-256 與完整 ZIP；拒絕已有簽章、重複 ZIP 項目或缺失必要檔案。
2. 在權限限制的暫存目錄複製固定輸入，簽署副本，保留原始 AAB 與 keystore。
3. 逐一讀取全部 payload，觸發 JDK 簽章驗證；要求每一個 payload 都由選定 upload 憑證簽署。任何改寫、未簽署項目、錯誤或混合簽章均停止。
4. 驗證後輸出新的 AAB 與 verification JSON；權限 0600，不覆寫既有檔案。失敗時無自動重試或 Play 上傳。

不要把 **upload 憑證**當成 **Play App Signing 憑證**：Play 分發版 Google 登入仍須使用 Play 管理中心提供的公開 SHA-1。工具不會以本機 upload 指紋代填 Google OAuth。

## 驗證

14/14 本機測試通過，使用一次性測試金鑰（測後清理），未使用使用者真實 keystore 密碼。涵蓋輸入 hash、AAB 結構、既有簽章拒絕、private-key alias、成功逐項驗證、unsigned／錯誤憑證／新增 unsigned payload／修改內容拒絕、錯誤密碼、避免覆寫、輸出權限與報告不含密碼。

首輪實測發現 jarsigner 會重建檔案並改為 0644；已在驗證與發布前重新設為 0600，重跑通過。另對實際 MMH unsigned AAB 執行 `--check` 通過；沒有執行真實金鑰簽署。CI 定義已加入，但本機分支未推送，尚無遠端 CI 結果。

證據：[本機簽署測試與交接檔案](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/summary.json)。
