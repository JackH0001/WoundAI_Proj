# 首次初始化清冊與實際資料位置核對 — 2026-11-10

## 修正的問題

原 `84f1763` 的 GCS factory 會核對來源、設定與清冊摘要，且各種帳號／訂閱／額度資料
位置都必須列入清冊。不過，它未確認清冊的 location 摘要就是 native 設定內相應
store 的 bucket／prefix。若操作端把另一個空位置當成目前位置，再一致地重算所有
摘要，factory 仍接受這份設定並建立儲存 adapter。這是初始化準備防護的缺口，
不是已觀察到的雲端用量歸零或資料遺失。

修正後，在任何 state factory／provider 連線前，要求清冊對 `accounts`、`links`、
`quota` 各自包含 native 設定實際位置的摘要。用途和位置必須一起相符；相同用途及
位置不得換一個 id 重複計數。額外已覆核的舊個人服務位置仍可列入，不能取代目前
位置。來源 id、順序不影響此核對。

## 前後驗證

- 修改正式程式前執行三個負向測試，重現查錯位置（分別 accounts／links／quota）、
  交換用途、重複列入位置會被接受；該次測試有六個失敗斷言，沒有成功的雲端操作。
- 修正後五個新增測試通過，包含額外舊位置、不同來源 id／順序仍可正確準備。
- 與 bootstrap core、factory、runner、封裝、IAM、metadata inventory 一起執行，
  **144 項通過，零失敗／略過**；五項已包含其中，不另加總。
- 沿原合成 factory probe 核對三階段與明確失敗續接：模型只執行一次、額度只扣一次，
  重送回執一致，原合成紀錄與稽核保留。不是 Apple 或 GCS 實測。

重跑方式（使用 repo 的 commerce 測試依賴；PYTHONPATH 含 Backend/Flask、tools、
engineering/phase2 及 repo 根目錄，Windows 使用分號）：

```text
python -B -m unittest -v test_lite_fresh_bootstrap_factory test_lite_fresh_bootstrap test_lite_fresh_bootstrap_runner test_lite_bootstrap_package test_lite_bootstrap_iam test_lite_fresh_inventory
python -B tools/lite_fresh_bootstrap_probe.py --factory --output new-factory-probe.json
```

## 對實際部署的影響

這不會產生空清冊、獨立觀測簽章或任何執行授權。必須從已固定的 native 設定取出
三個真實 store 位置，再與受限 metadata 清冊／歷史來源逐一核對；不可手填名義路徑。
舊研究安裝身分與日誌屬不同服務權益，應在部署清冊明列保留及分流理由，不能冒充
新的個人帳號／訂閱／用量，也不能為了通過首次初始化而清空。

缺失桶、403、軟刪除查詢失敗等仍是未知，不能轉成零筆。位置相符也不能證明歷史
來源完整、writer 已封閉或請求已排空，這些仍需獨立證據。本輪不改服務資料格式、
App、IAM、Secret 或雲端資源。

本修正改到 bootstrap 的封裝內容，因此後續執行器需重新建立及核對映像；不能把
先前 `a175a15` 的 bootstrap digest 改標為本版。原映像與原始驗證證據繼續保留。
新提交未推送前，前版綠色 CI 不代表本修正的遠端 CI 已通過。
