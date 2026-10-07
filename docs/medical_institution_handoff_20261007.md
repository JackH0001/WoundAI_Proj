# WoundAI 醫療端 MMHPS20261007：Mac／Claude 整合交接

日期：2026-10-07（台北）。範圍只包含醫療端 WoundAI；WoundLite 不是本機構特殊版。

後續 commit／母庫回收／完整 Mac Python 與 HTTP 驗證見 [2026-10-07 兩庫對齊紀錄](repo_commit_alignment_20261007.md)。

## 已保全與整合

- 原 Android：`/Users/Jack.Hou/Developer/WoundAI_Proj`，`claude/android-16kb-rbac-20261005`，`03bc72d`；原工作目錄未修改。
- 原 iOS：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-ios-release-20261001`，`d7e3617` 加未提交修改；原工作目錄未修改。
- 保全 commit `a162b1ca18a1294d20e3ef6ef2de633d7f9860df`：160 個來源／文件路徑。另保全 `:memory:.ses` 於私有 tar，但未納入 Git。
- 整合 commit `aa01e9d9350a4ba809e5335ba09e29a91553a64b`：合併 Android `03bc72d`，保留雙方 PARITY 內容，明確登記 Android 25／iOS 醫療 27。
- 工作副本：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-integration-20261007`，分支 `codex/institution-medical-align-20261007`。
- 新副本由完整 Android 物件庫建立，另外取得 iOS `d7e3617` 歷史；無 alternates，`git fsck --full --no-dangling` 通過。原 iOS clone 的 HEAD 歷史有 697 個缺少的可達物件；不是僅 Linux 路徑誤報，也不代表目前 697 個工作檔已消失。
- 證據與完整歷史 bundle：`../woundai-institution-evidence-20261007/`。這是本機候選保全，不是已合併 main／CI 全綠／可上架聲明。

## 本輪修正

1. 版號測試原本綁死工作樹 22／25，升版後三項測試失敗，部分字串變異成了無效操作。改用固定解析器 fixture，另驗目前工作樹的明確登記：7/7。
2. Android 存檔前的匯出原本早於照護同意檢查。現在先拒絕缺少同意，再進入匯出；相機返回後呼叫既有 `careCodeProvider()` 重新讀取，疊圖協程也重新讀取同意。這是本機檢查，不能宣稱可撤銷已分享的外部副本。
3. iOS 校正框與提示改為綠色，與 Android 的安全檢查語意一致。
4. 增加 `iOS/project.institution.yml`、`WoundAIInstitution` target／scheme、獨立 bundle `com.woundai.app.mmhps20261007`。不變更 WoundLite target 的用途。尚未向 Apple 註冊專用身分或發送 TestFlight。
5. 機構版後端從專用 Info.plist 讀取，現在為空；實際回傳保留 `.invalid` 位址，不沿用已存正式／demo 帳密的網址。後續部署驗收後才填入專用 HTTPS 服務。
6. iOS 機構版存入時間軸成功後，輸出至 `Documents/MMHPS20261007/Exports/`；普通醫療版自動匯出關閉。儲存鎖在非同步同意查詢前啟用，拒絕重複送出。臨床來源另需 `record.save` 與當下有效照護同意；來源不明拒絕。

## 匯出契約

每份副本包含：

- `image.png`：量測使用的方向已正規化影像，**不是原始相機 sensor RGB**。
- `overlay.png`：由同一份已確認 EditRaster 畫出組織與輪廓，校正框及圖外結果文字帶。沒有重跑分類器。
- `mask-tissue.png` ＋ `raster.json`：沿用 EditRasterCodec，保留目前／原始遮罩、組織碼及座標映射。
- `measurement.json`：機構代碼、本機量測 ID、面積、組織比例、PUSH 完整／部分分數、滲液、醫師確認與人工修改狀態。沒有病患姓名或病患 ID 欄位；影像本身仍可能含個資，不能因此稱匿名。
- `sha256.json`：每個資產雜湊。固定排序的 metadata 綁定 revision，完全相同重試沿用；既有檔案不一致則拒絕成功。

只在畫布尺寸與圖像完全吻合、遮罩完整時輸出。副本採作業系統檔案保護及排除備份；使用者主動分享後的外部副本不受 App 控制。

**本包沒有深度**：`depth_included=false`、`cloud_uploaded=false`。既有 RGB-D 上傳仍是另一條流程，不能把結果疊圖當成完整 WoundAI3D 訓練封包。機構版 3D 驗收還需 RGB／depth／confidence／內外參、像素映射、單位、時間戳、模型與來源版本，以及同意與撤回可追溯性。

## 實際功能對照與 Claude 接續順序

| 項目 | iOS 醫療端 | Android 醫療端 | 建議接續 |
|---|---|---|---|
| 結果圖雙指縮放與移動 | 已有 UIScrollView 1–6× | AnalysisPreview 尚無相同操作 | 優先移植；單指仍交給頁面捲動 |
| 時間軸輪廓疊圖與座標不符拒畫 | 已有 | 尚缺對應預覽 | 與上一項共用預覽元件 |
| AF 狀態與拍攝引導 | 有中央框／狀態提示 | 沒有相同狀態提示 | 驗 CameraX 真實 AF 狀態，不能推論預設相機完全不對焦 |
| 修邊 seed 初始計算 | `runSeed()` 背景計算與鎖畫布 | `remember` 建構時仍呼叫 `seedAuto` | 移出主執行緒；量測首次開啟耗時後再宣稱改善 |
| 修邊首次多指／ROI 穩定性 | 已修、既有手勢測試 | 需以相同操作腳本實機重現 | 不以平台契約通過代替手勢驗收 |
| 結果圖組織圖層 | 互動預覽未有；本輪匯出有 | 已有且讀實際 EditRaster | iOS 互動預覽後續補；不混入本次雲端隔離閘門 |
| 相簿／檔案匯出 | 專用 target 寫 App「檔案」 | internalTest 寫 MediaStore 共用相簿 | Android 依本包補 metadata／雜湊與重試去重，保留使用者既定輸出流程 |
| 臨床拍攝導引／重新拍照 | 已有 | 未完成同等驗收 | 小範圍移植並實機確認 |
| 機構雲端隔離 | 專用版尚未填 URL | internalTest 仍連舊正式服務 | 兩端都不得宣稱已接入 MMHPS 專用桶 |

**建議 Claude 不要直接將整個 WIP 推成 release PR**。先從本機保全分支取審閱副本；Android 同意修正與 portable parity 可獨立覆核。iOS／Lite／後端 WIP 保全含大量跨範圍工作，仍需按產品拆分、Windows 驗證及完整機密掃描後決定推送範圍。本輪沒有向 Claude 外部對話自動送出訊息。

## 機構雲端待完成

Jack 指定代碼 MMHPS20261007，桶尚未建立。先前 IRB 狀態是尚未送件；本輪尚未收到更新核准摘要或明確測試資料範圍。

預定共用一個機構專用醫療後端，iOS／Android 都以同一個 API 契約提交；桶權限只授予專用後端身分，手機不能持有 GCS 金鑰。機構代碼必須由伺服器身分／設定綁定，不能相信手機任意傳入的 org。媒體、帳號／安全狀態、稽核用途分開設計；不能複用 WoundLite 的匿名 App Attest／研究捐贈設定來替代醫療 RBAC 與照護同意。

在資料範圍確認後，準備機構專用 service／SA／bucket／密文／IAM 的具名計畫與成本驗證。**不能僅在 App 改桶名**；還要驗證有效 IAM 拒絕其他機構資料、API 跨機構存取拒絕、存檔與修訂冪等、帳號冷啟動留存、RGB-D 完整性、撤回／保留與稽核讀回。正式稽核桶鎖定仍屬不可逆獨立決策。

## 本輪驗證與界線

- iOS 醫療 Release XCTest：84/84 通過，包含 5 項新匯出測試；xcresult `medical-tests-final-verified.xcresult`。
- iOS 機構 target Release simulator build 成功，讀回獨立 bundle、機構代碼、Files 開放設定、空後端 URL。
- Android JDK 17：internalTest APK、Release AAB；最終建置成功；APK 的 6 顆 arm64 原生庫通過 ELF 與 ZIP 對齊檢查，AAB 通過 ELF 檢查（尚未驗 Play 產生的分割 APK）。證據為 `android-final-build2.log`、`android-final-apk-16kb.log`、`android-final-aab-16kb.log`。APK 無發行簽章，沒有安裝或上架。
- Portable URL 7/7、版本契約 7/7、verify_logic 50/50、parity 未宣告落差 0。
- 失敗證據保留：Java 25/KSP 不相容、Release 最初未開 testability、unsigned simulator Keychain -34018、Documents 產物 Finder metadata 阻擋簽章，以及新匯出的 JSON 排序 bug。最終改用 JDK 17、Release testability、/private/tmp 原生簽章測試；沒有關閉安全檢查。
- 尚未證明：機構版實機 Files 操作、兩端相同影像視覺對照、專用機構雲端、完整 RGB-D 收件／撤回、Apple／Play 送審、Windows 全套驗證。沒有宣稱臨床精度或 IRB 核准。
