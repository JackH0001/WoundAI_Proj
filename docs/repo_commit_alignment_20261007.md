# 母庫／公開庫 commit 對齊與醫療端協作紀錄

2026-10-07（台北）。本輪產品範圍為機構特殊內測的 **WoundAI 醫療端**；不是 WoundLite 發布。下列遠端狀態由 GitHub 即時唯讀查詢核對，並非沿用舊 remote tracking ref。

最新補驗：母庫已實際跑到 73/73，並另補 CI 閘門，候選前進至 `1a80a2e`。詳見 [母庫自身驗證及 PR 拆分檢查](mother_runtime_validation_20261007.md)；下表保留前階段的 commit 快照。

## 版本位置

| 位置 | commit | 判定 |
|---|---|---|
| 私有 `JackH0001/WoundAI` main | `7880390bc77dc0af06bced7ceae419397362bc63` | 上次收進公開庫 `505ff2e`，遠端未變更 |
| 公開 `JackH0001/WoundAI_Proj` main | `4005f392a5e77159ee44a140b86ca80af8fe0cdc` | 比母庫紀錄的來源基線多 29 commits；兩庫歷史獨立，不能要求 HEAD SHA 相同 |
| PR #16，Draft | `d7e36175020f041bef39b130f811862613902180` | 7 個完成檢查 SUCCESS；不含 10/04–05 的原工作樹修改 |
| PR #17，非 Draft | `dfd9e1020c52b5b385e62feeaad89df49cbb8adb` | 7 個 SUCCESS、emulator androidTest SKIPPED；不代表實機 UI 已驗收 |
| Claude Android 本機 | `03bc72d0e2ba653e9f0087794b6e0312a1a9d825` | 比 PR #17 多 4 commits，含疊圖與 PARITY 更正；本輪未改其目錄 |
| iOS 原工作樹保全 | `a162b1ca18a1294d20e3ef6ef2de633d7f9860df` | 已保全 160 個來源／文件路徑；不是 release 宣告 |
| 機構版產品實作 | `8ed1971614aa6ce0bae69e48c42b3c424f4bfeb7` | iOS MMHPS 特殊 target、Files 匯出、兩端同意檢查 |
| 本機完整整合及本輪測試基準 | `2ed53e40c57e74bc46488f2d3de790ec8e3c16d9` | 已合入公開 main `4005f39`，消除先前漏入 PR #15 的問題；iOS／Android 內容與 `8ed1971` 相同 |
| 私有母庫回收候選 | `2338f599f4fc222e949cb3b3cacd2800c0887b98` | 91 檔對齊公開 main `4005f39`；本機候選，未 push／合 main／部署 |

整合副本：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-integration-20261007`，分支 `codex/institution-medical-align-20261007`。其 `origin` 指向本機 Android repo，**不要直接對 origin push**。本輪只新增 `shared/*` 遠端追蹤 refs，未改其他工作目錄的 remote。

母庫副本：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-mother-alignment-20261007`，分支 `codex/harvest-shared-main-4005f39-20261007`。這是保留 Git tree 的 sparse／partial clone，不下載未變更私有模型與臨床資產，不是完整離線備份。

Mac `/Users/Jack.Hou/Documents/Claude/Projects/WoundAI` 是無 commit 的舊工作資料夾，remote 反而指向 WoundAI_Proj；全形 `ＷoundAI` 目錄亦不是母庫 checkout。不可依資料夾名稱宣稱同步成功，這兩個原目錄均未變更。

## 母庫回收邊界與證據

以已合併公開 main 的 `505ff2e..4005f39` 產生 binary patch。第一次預檢因母庫已有 `.gitleaksignore` 而拒絕；沒有使用 `--reject` 或覆蓋母庫原檔。公開新增的兩條 fingerprint 針對公開庫歷史 commit，母庫並不共享該歷史，因此明確排除。

最終排除 `.github/**`、`_to_delete/**`、`.gitleaksignore`。91 個變更後的 blob ID 與檔案 mode 逐一等於公開 `4005f39`，其他所有 tree entry 與母庫 `7880390` 相同。沒有 LFS 檔變更；母庫原 CI、私有模型、IRB／資料內容與歷史忽略規則未被改寫。

- 候選 tree：`4db3155189993c2ccd3257b25a40f6512655d612`
- patch SHA-256：`e1984d1c656b3a17eb9971fafd1f031e55a6b1893ab364d4fd522e123d197772`
- 證據：repo 外 `woundai-institution-evidence-20261007/mother-harvest-verification.json`。

此回收只跟上**公開已合併基線**，不偷帶尚未發布的機構、Lite 或後端 WIP。母庫候選僅完成 patch／blob 完整性與機密掃描；不能將下面整合副本測試當成母庫完整 runtime 或 Windows 驗證。

## 本輪測試（Mac）

測試綁定 `2ed53e4`，沒有未提交 source diff；測試前後原始碼一致，臨時 runtime 清理成功。

| 驗證 | 結果／範圍 |
|---|---|
| 隔離 Python runner | **103/103 測試檔通過**，97.471 秒；103 是檔案數，不是 assertion 數 |
| 獨立 LocalStore HTTP | **20/20 通過**，包含訓練同意、撤回、禁止跨個案綁定及重新同意；僅合成資料 |
| 部署有效權限檢查 | 15/15；PowerShell 7，非 Windows PowerShell 5.1 |
| 部署隔離／靜態契約 | 48/48 |
| Mobile 版本契約／後端網址 | 各 7/7 |
| verify_logic／parity | 50/50、未宣告落差 0；已登記落差仍需實作，不等於兩端功能相同 |
| Gitleaks 8.30.1 | 官方壓縮檔 SHA-256 核對；整合 `4005f39..2ed53e4` 24 commits，以及母庫候選 1 commit，均 no leaks found（預設＋專案規則） |
| iOS／Android | 沿用 `8ed1971`：醫療 Release 84/84、機構 target 建置成功、Android APK／AAB 建置及 6 庫 16 KB 檢查通過；本輪比對 mobile source 未變更 |

初次 Python runner 101/103；兩支 loopback HTTP 因沙箱 socket.bind 回 Operation not permitted，保留失敗證據。第二次允許本機 loopback，完整 runner 103/103，沒有改測試或程式碼。測試使用隔離 HOME／雲端憑證環境，未碰真實服務或病患資料。

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/`。關鍵檔案：`github-alignment-readback.json`、`aligned-python-loopback/python-summary.json`、`aligned-medical-http/http-summary.json`、`gitleaks-integration.log`、`gitleaks-mother-candidate.log`。

## Claude／Codex 接續順序

1. 以本機整合測試基準 `2ed53e4` 與後續文件 commit 對照，保留母庫與公開庫各自歷史。不要把私有母庫 merge 回公開 repo。
2. Claude 覆核 Android `03bc72d` 後的同意檢查修補，以及 [機構版交接](medical_institution_handoff_20261007.md) 的 UI 差異表。PR #17 尚未包含後四筆本機 commits；更新前需將範圍與母庫回收分開。
3. Windows 端執行完整驗證並綁定確切 source tree。本輪 Mac Python／pwsh 結果不能標成 Windows 全套驗證。
4. 決定公開 PR 的拆分：醫療 mobile／portable parity、Lite、後端分別覆核；不要將整筆保全 WIP 當成已完成 release。完成審查後才更新公開 PR／CI；其後母庫再從 `4005f39` 做下一次增量回收。
5. 母庫候選 `2338f59` 應另行驗證 private runtime／CI／LFS checkout，且核對既有 `.gitleaksignore`；本機 blob 相等不是部署核准。
6. MMHPS20261007 機構雲端仍待測試資料範圍確認、具名資源隔離方案與收件驗收。iOS export `depth_included=false`；Android internalTest 仍用舊後端，均不可宣稱已接入專用桶或完成 3D 訓練資料鏈。

本輪沒有對外發訊息、push、PR merge、Apple／Play 上傳、雲端部署或桶鎖定。
