# 母庫候選自身驗證與公開 PR 拆分檢查（2026-10-07）

本輪延續醫療端 WoundAI／MMHPS20261007 協作。母庫目前本機候選為 `1a80a2e7caae5d6c2771fd683bd495184feca599`；公開整合產品碼仍是 `2ed53e4`，`9ab5736` 後本輪只增加交接文件。

## 母庫自身執行結果

| 檢查 | 結果 |
|---|---|
| 隔離 Python runner | **73/73 測試檔**，86.786 秒，綁定 `8dd2235`，執行前後 source snapshot 相同 |
| 本機醫療 HTTP 合成情境 | **20/20**；LocalStore、隔離帳號、無真實 GCP 憑證、runtime 已清理 |
| 靜態 mobile 邏輯 | **52/52** |
| parity | **未宣告落差 0**；此母庫仍是公開 main 的平台功能基線，非 MMHPS 新 UI 已回收 |
| 新母庫 CI 邊界測試 | **7/7**；拒絕開啟 LFS、保留 checkout 憑證、額外拉模型或注入雲端密文；端點守門及此測試須接入 push／PR 路徑 |
| 端點守門 pytest | **8/8**，功能測試不可 skip；撤回恢復 RBAC、退役 train 路徑均有實際測試 |
| CI static／URL parity／唯讀 inventory | **12/12、7/7、8/8**；補閘門後重驗通過 |

完整 73 支套件之後，只改動 `.github/workflows/p0-4-audit.yml` 與 `tools/test_mother_ci_boundary.py`，加入並修正端點測試的 CI 呼叫；產品程式、完整套件內的測試檔均不變。以上相關 CI 邊界與端點測試於最後內容再驗過。HTTP 結果綁定更早的 `2338f59`，之後也只有這兩個 CI 檔變更。

## 本次找到並處理的問題

首次母庫套件為 65/73，不能沿用公開整合的 103/103 宣稱母庫通過。

- 五個失敗測試檔依賴 stub：隔離 sparse checkout 刻意排除所有 ONNX，連公開假模型也被排除。核對母庫及公開庫 stub 都是 Git blob `b09ac9228d64079faed3d868a99badddd7ed69e5` 後，只取出 `models/stub/wsm_stub.onnx` 與 `engineering/phase0/models/stub/wsm_stub.onnx`。兩檔原始碼未修改；沒有下載真實模型。
- 三個失敗測試檔依賴 `p0-4-audit.yml`。母庫回收排除公開 CI 是正確邊界，但後續必須另做母庫 CI 整合，不能停在檔案一致。新增母庫專用移植：保留既有 workflow；checkout **lfs:false**，避免私有權重頻寬；contents:read、persist-credentials:false、不使用雲端密文；只測試與 build，不推送映像或部署。
- 新加入的端點守門必須用 **pytest**：直接 `python test_endpoint_guards.py` 只匯入定義，不會執行任何測試。已在 `1a80a2e` 修為 `python -B -m pytest ...`，另裝既有開發 lock 同版 pytest 9.1.1；新增 CI 檢查釘住此呼叫。早先直接 Python 的空白輸出不算驗證證據。

母庫 commit 順序：`2338f59`（91 檔精確回收）→ `8dd2235`（母庫安全 CI）→ `6082bf9`（端點 CI 佈線）→ `1a80a2e`（以 pytest 真正執行）。所有變動仍在本機候選分支；母庫 main 未更動。

## 公開 PR 拆分的實際依賴

已對 `shared/main@4005f39..9ab5736` 的 225 個變更路徑產生具體清單 `public-pr-split-inventory.json`（repo 外證據目錄）。這是**覆核分組，不是可各自獨立建置的 patch**：

- Android 醫療：36 路徑。
- iOS 醫療 UI／共用 Core／醫療測試：31 路徑。
- Lite App／Lite tests：42 路徑。
- 後端與契約測試：67 路徑。
- 共用建置／工具：22 路徑。
- 文件等其餘覆核：27 路徑。

不能單純按 `WoundLite/` 排除就說醫療 PR 已獨立：`BackendClient.swift` 使用 LiteAuthenticatedTransport、LiteCloudQuota、LiteRawDepthPacket、LiteStorageReceipt；`project.yml` 同時更動兩個 target；醫療 XCTest 還共用 `WoundLiteTests/LiteZoomPreviewTests.swift`。共同的檔案保護、影像預覽、相機與修邊亦需一起追蹤。

Claude 可先覆核 Android 與 portable parity 的差異；醫療 iOS PR 要將共用型別與建置設定列為明確依賴，再在切出的獨立樹重新編譯。不能以完整整合樹 build 成功替拆分後版本背書。母庫私有程式、模型或政策沒有反向拷進公開庫。

## 證據位置與尚未驗證

`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/`：

- `mother-runtime-final.json`：最終母庫 SHA／tree、完整套件 SHA、後續差異。
- `mother-python-final/python-summary.json`：73/73 與 source snapshot。
- `mother-http/http-summary.json`：隔離 HTTP 收尾及來源不變。
- `mother-ci-boundary-final2.log`、`mother-endpoint-pytest-final.log`：最後新增閘門。
- `public-pr-split-inventory.json`：逐檔分組及跨組依賴。

本機沒有 Docker daemon，未完成 Linux 鎖定映像 build；不是 Windows PowerShell 5.1／.NET／Windows 全套通過。新 workflow 尚未上 GitHub，不能稱遠端 CI 綠燈。沒有新增機構桶、變更真實服務、推送 repo 或上傳 App；MMHPS 測試資料範圍與機構核准仍待確認。
