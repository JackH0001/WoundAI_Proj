# MMH 全模組整合與研究資料契約覆核

2026-10-13。本輪完成本機來源整合、採集時間及深度回執補強；**尚未全面完成研究採集驗收、示範環境部署或商店更新**。新版示範採獨立 WoundAI 帳號，不要求 Google；既有 MMH 保持 IAP＋WoundAI。資料完整性是發布關卡，詳細要求與未完成項目見 [研究資料驗收矩陣](research_dataset_acceptance_matrix_20261013.md)。

## 已核對來源

| 模組 | 本輪確認 |
|---|---|
| 公開庫 main | GitHub 唯讀核對 `85775e8613b8a4680f8df2d0eca2e292dd383fd9` |
| 公開庫手機 PR #24 | OPEN／Draft，head `57c6d0bfc296364b47999d713a11e828f14673af`；不包含本輪最新本機修正 |
| 手機／隔離基底 | 本機 `8983449`，包含 `4fc41f1` Google 恢復、`6aef9a0` UI、`3e1b0e6` 資料隔離、`20feaf0` 加密設定隔離 |
| 後端／管理／延遲基底 | 合入 `eade0de80ac4f9af5d4ce091213fb9dc47fe5c76`，含 main 已發布管理／CAS／延遲修正及新 auth/session |
| 稽核狀態 UI | 套用 `94795b137a5514ac5dc7d994b5a4ac088715e77a`，依實際 retention 顯示保護狀態 |
| 本機整合提交 | `c5b1d122843356b6fa8bb4bf88405b2eea347326`；分支 `codex/mmh-full-alignment-20261013` |
| 母庫本機 checkout | `woundai-mother-alignment-20261007` 仍為 `18668ae`，有既有未追蹤副本；本輪未覆蓋、清理或聲稱母庫已同步 |

手機分支與 main 的管理、帳號版本、稽核效能原本不齊全，直接複製手機工作目錄作完整後端會漏功能。本輪先整合再測試。合併僅兩处衝突：gitleaks 規則與其測試；採用既有 `28f8de3` 整合版本，保留兩組「精確路徑 AND 精確假值」例外，不擴大豁免。

## 本輪新增修正

1. **採集時間**：在共用相機 RGB-D 回呼建立固定 UUID／壁鐘時間；醫療 sidecar 升為 v3，跨保存、重修與讀回沿用。明示 `photo_callback_wall_clock`，不是感測器曝光時間。缺來源保持 unknown；舊 sidecar 的 captured_at 不改寫、不提升其語義。
2. **Lite 前後端契約**：有新採集來源時使用 `lite.rgbd/2`，帶 capture identity／captured_at／來源；舊 v1 持續解析且仍標時間 unavailable。格式／日期／UUID 缺漏或錯誤拒絕。原始 Float32、RGB與metadata雜湊、App Attest／保存回讀不變。pose仍unavailable，registration仍not_verified，training admission仍not_evaluated。
3. **醫療深度收據**：過去手機只核對短 raw hash 與 image_id。現在嚴格核對型別、完整 raw hash、request metadata hash、確切 annotation revision、canonical metadata／payload 聯合 hash、深度修訂與 receipt ID；後端增加對應欄位。舊後端缺欄位會保留待傳，不能配對新手機直接宣告成功。
4. **本機回執**：保存 annotation receipt、depth receipt、payload及metadata hash；若索引保存失敗，不把工作列為完成。後端歷史資產保持不可覆寫，metadata真正變更仍是新修訂，重試保持既有內容關聯。

`request_meta_sha256` 識別本次實際傳送的 metadata JSON bytes，會因鍵序變化而不同；`meta_sha256` 識別伺服器驗證後的 canonical metadata，`payload_sha256` 綁 canonical metadata 與 raw bytes。內容相同而鍵序不同的重送可維持相同 depth revision；不可用 request hash 取代 canonical revision。

## 本輪驗證結果

| 項目 | 結果 |
|---|---|
| iOS MMH 模擬器回歸 | 111/111 通過，含採集時間固定、實際 multipart、收據欄位錯配拒絕、索引重開與影像隔離 |
| WoundLite 模擬器回歸 | 151 通過、1 skipped、0 failures；跳過實機檔案 protection class，模擬器無該屬性 |
| Android MMH 單元測試 | 97/97，0 skipped；強制 rerun，29 tasks executed |
| Android validation APK／internal Kotlin | 建置通過；未更新平板或 Play |
| 後端資料／登入／權限回歸 | 24 組皆通過：258 個 unittest 案例及4支逐項檢查腳本；不把異質腳本項目數混算成unit數 |
| 管理服務 | 121/121 |
| 管理頁 Node 測試 | 9/9 |
| 靜態邏輯 | 50/50 |
| 平台契約 parity | 通過；仍保留已登記的設備深度與版本差異，不表示兩端硬體功能相同 |
| 機密掃描 | 本次變更檔案0命中；全工作樹3個既有命中，Git正規化後皆未異動，不能聲稱全庫0命中 |

管理測試首次使用不適合套件相對匯入的 discover 命令，7個模組匯入失敗；改用完整 `Backend.AccessManager.test_*` 模組路徑後121项通過，未為此改產品程式。初次檔案掃描以絕對根路徑執行造成精確路徑例外失配，改在掃描根目錄執行後變更檔案0命中；失敗log保留。原Windows檔案CRLF與blob比較先誤判異動，已用Git正規化diff更正。

owner_guard 對4個 Backend／engineering 檔案仍回報 windows-only；本輪依使用者「所有模組及前後端統一對齊、確認完整研究資料」授權做本機跨堆疊修正，保留原規則、不假報通過。這輪沒有修改或執行部署PowerShell，也沒有聲稱Windows實測。上游合入的一處檔尾空白由diff-check指出並清除。

證據：[summary.json](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/full-alignment-20261013/summary.json)。同目錄保留每組測試log、Android XML、遮蔽掃描與初輪失敗。Xcode xcresult 路徑記於各log；數字以最終單輪為準，未累加重跑次數。

## 尚未完成與發布限制

- 新免Google示範環境、全App環境context、雲端資源與對外入口尚未部署；本機模式隔離元件不等於前後端接線已完成。
- 最終傷口二值mask、原始模型候選／artifact指紋、配準驗證等詳見驗收矩陣；不能把目前通過的保存測試當「可直接用於3D訓練」證明。
- 尚未在實機量到這批登入重用／雲端延遲改善，也未驗收新版完整採集→雲端讀回→WoundAI3D匯入。
- 所有來源保存在本機；沒有推送、GitHub合併、雲端建置／部署、商店發布、舊服務停用或雲端資料封存／刪除。
- 後端與手機需成對發布：新session端點、新醫療深度收據與Lite v2均要先在候選環境驗收。既有固定SHA來源推送提案不涵蓋本輪新增提交。
