# 醫療版深度內參驗證修復：2026-10-05

本次承接 [生態系覆核 R1](ecosystem_research_readiness_20261005.md)，修復「原始深度上傳成功，但缺少可用校準資料」的驗證漏洞。只變更 `Backend/Flask/api_flywheel.py` 與既有 `engineering/phase2/test_depth_endpoint.py` 的功能／測試；本文件及上架計畫另記錄狀態。

## 行為

- meta／camera_intrinsics 必須為物件；深度 width/height、校準 ref_width/ref_height 必須為正整數，拒收 boolean。
- fx/fy/cx/cy 必須為有限數字，拒收 boolean、NaN、Infinity、無法轉成有限浮點值的巨整數；fx/fy > 0，主點在宣告的參考網格內。
- 格式必須明確宣告，不再以缺值猜成公尺。仍接受既有 `f32_le_meters`、`raw_f32_m`，仍允許校準參考解析度高於深度圖。
- 無效內參在寫入之前拒收。HTTP 測試確認既有 `.f32`、`.meta.json`、`depth_index.jsonl` 逐位元組不變；拒收的稽核事件可正常增加。
- coverage/min_m/max_m 由伺服器依實際上傳深度重算，覆蓋不可信自報值。
- 未修改 RGB-D 原子提交、角色權限、撤回制度或歷史資料。舊缺校準資料不能因新驗證器而自動變有效；沒有猜值遷移。

目前 iOS `BackendClient.uploadDepth` 已傳格式及參考尺寸，正常案例與較高解析度校準通過。這是本機契約相容證據，尚未以新的後端版本在實機／線上重驗。若其他客戶端省略這些必要欄位，新端點將回 400，需修正客戶端或將舊資料留在隔離層，不能放寬成可訓練。

## 驗證

先加入反例測試、在未修的驗證器上執行，確實 exit 1；再修改驗證器。最終：

| 項目 | 結果 |
|---|---|
| 深度端點（含真實 Flask test client、合成標註與檔案） | 132 項 PASS，rc=0 |
| 深度落盤／取回／反投影既有測試 | 34 項 PASS，rc=0 |
| 組織資料集／組織匯出 | 41／22 項 PASS，rc=0 |
| endpoint_guards | pytest 8/8，無 skipped |
| 驗證器變異測試 | 10/10 被抓到 |
| 限定變更 diff whitespace | 通過 |

變異分別移除維度型別、明確格式、內參 boolean 排除、有限值、正焦距、參考尺寸、主點範圍，以及覆蓋率／最小／最大距離重算。變異於實際驗證器函式的隔離副本進行，未覆寫工作樹或接觸雲端。額外用 byte count 完全相符的 boolean 維度案例，避免只因長度不符而假裝測到型別防護。

測試環境使用既有 review venv，子行程清除正式路由與憑證環境，LocalStore＋合成資料。測試 JWT 短金鑰警告來自既有 fixture，不是正式密文設定，本次未變更其長度或記錄密碼。

## 交付與界線

證據在 repo 外 `woundai-competitive-review-20261003/medical-depth-calibration-20261005/`：

- `before.log`：未修版本對新增測試的失敗。
- 四支 script logs、`guards.log`、`results.json`。
- `check_mutations.py`、`mutations.json`。
- `medical-depth-calibration.patch`：只包含兩個程式／測試檔的差異。
- `validation.json`：來源與 patch SHA-256、基底 HEAD、結果與未完成事項。

這是 Mac 本機候選修復，尚未提交、未 push、未跑 Windows 閘門、未部署。整個工作樹仍有既有其他未提交工作，不能以 `git add -A` 把它們一起提交。正式採用前需與後端權威分支整合並依確切版本通過其驗證／CI；本次未宣稱 owner_guard 或遠端 CI 已綠。

R1 程式漏洞已在候選工作樹修復；R2 的可靠補傳／不可變版本及 R3–R5 的資料對齊、轉接、撤回與備份仍須繼續完成。IRB 尚未送件的狀態不變。
