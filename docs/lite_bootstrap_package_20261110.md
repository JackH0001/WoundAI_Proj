# 私有初始化程序封裝 — 2026-11-10

此變更接續 `7123577` 的單次 bootstrap runner。只準備本機程式與 CI，未部署、
初始化雲端、存取研究內容、啟用 Apple 金鑰或交付 App。

## 修正前後

先前 runner 的 factory 為了取得 `ALL_STORES`、`exact`、`text`，會匯入
`lite_native_startup`，連帶載入個人服務／模型啟動模組。repo 中測試可成功，
但尚無獨立封裝或乾淨啟動證據。

本輪將原設定定義原封不動移到 `lite_native_schema`，供個人服務及初始化工具共用；
不複製兩套 schema、不改設定名稱／值／驗證錯誤。`overlaps` 直接取自既有位置模組。
個人服務封裝清單同步加入共用模組。原生啟動與實際設定 fixture 回歸已通過。

新增 `stage_lite_bootstrap_candidate.py`：固定 41 個程式模組及 4 個設定／驗證檔，
合計 45 檔。顯式清單之外的相依項會阻擋封裝，不會自動擴大複製範圍。
雜湊、大小、來源路徑、commit、dirty 狀態記入 manifest；輸出不能覆蓋舊目錄。
要求檔案為一般檔，拒絕 symlink，封裝前後核對來源未變。

沿用 `Backend/LiteDisposition` 已固定雜湊的 requirements，而非新解一次浮動版本。
內含共用核心所需的函式庫與類別定義（包括 Flask），但沒有個人服務 factory、
模型執行器或 HTTP listener。沒有模型權重、照片、`.p8`、ADC 或部署設定。

Dockerfile 採既有固定 Python 基底、非 root、root-owned/readable 程式，
build-time 以 `--network=none` 核對所有檔案與獨立 import。預設 ENTRYPOINT
只跑單次 CLI，缺少完整來源／設定摘要即停止。無 port、排程、服務自動初始化或重試迴圈。

## 驗證

本機 **236 項通過，0 失敗、0 略過**，其中新增封裝測試 **12 項**（已包含，勿加總）。
範圍是封裝、原生啟動／組合、bootstrap／factory／runner、耐久預算、傳輸及附加身分。

- 獨立子程序移除 repo import path，檔案／import 核對通過；沒有載入 App/model startup。
- 真正封裝 CLI 的 `--help` 成功；缺必填參數 exit 2；錯誤輸入 exit 1，只有摘要且不輸出輸入內容。
- 新相依漏入清單、檔案改寫、多餘檔案、symlink、缺檔及覆蓋舊目錄均拒絕。
- 原有合成核心測試仍確認：初始化後重送／服務重啟，模型一次、扣額度一次；
  不明結果須明確續接，既有資料與稽核保留。
- 第一輪因新共用模組漏入 native 清單而失敗，已補正並重跑上述完整套件。
  初稿還把 Flask 函式庫 import 當作 HTTP 服務啟動，已更正檢查邊界；沒有移除實際服務／模型防護。
- workflow YAML、Python AST、diff whitespace 檢查通過。

新增 `bootstrap-image` CI job，建置映像並在無網路、唯讀、移除 capabilities 的條件下
驗證 CLI。**這個 job 尚未在遠端執行，也沒有本輪 Linux 映像 digest。**
前次 `/private/tmp` 的本機 Linux VM 已不存在；獨立 Python 程序測試不能替代
Linux 容器、真實 GCS 或 Apple Sandbox 驗收。本輪未修改 iOS，不宣稱新 iOS 實測。

## 之後的執行順序

1. 固定來源推送獲准後，跑新 CI；必須讀回此來源的容器 job，不能沿用 `3d76b38` 的結果。
2. 產生對應固定映像與設定清單，連同 native／research／key／disposition 的精確版本核對。
3. 私有 job 的 SA 分離於 App runtime。只讀 accounts／links／quota 等 lane；
   限定控制列 CAS 和 create-only 稽核，沒有 Apple、KMS、模型或研究媒體讀寫權。
   有效 IAM 仍須 provider 驗收；程式 wrapper 不取代 IAM。
4. 提出新增資源／IAM／上限／費用／回復的具體授權。單次任務、硬時限、重試 0；
   三步各綁簽署命令及獨立入口關閉／零 in-flight 證明。缺少桶不能當作舊帳號不存在。
5. 寫入不明先保留稽核、停止舊工作並 inspect；再依原方案明確續接，不能清空狀態。
   初始化成功仍不能自行開 App 入口；服務正向驗收與 Apple Sandbox 另有明確範圍。

參照：[執行器契約](lite_bootstrap_runner_20261110.md)、
[完整外測要求](lite_release_requirements_20261110.md)、
`test_lite_bootstrap_package.py`、`test_lite_fresh_bootstrap_runner.py`。
