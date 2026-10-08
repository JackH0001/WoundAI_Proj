# MMH 私有服務建立入口與驗證

2026-10-08。新增 `tools/create_mmh_private_service.py`，讓已覆核方案有獨立的 MMH 執行入口。**本輪沒有建立服務、授予 IAM、建立密文、鎖桶或切流。** 真實 `check` 模式按預期停在部署前條件，不是部署成功。

## 行為與拒絕條件

- `check` 是預設使用方式，只讀雲端 metadata／IAM；不讀密文值。`create` 額外要求輸入方案完整 SHA-256，仍須全部即時查核通過。這個 hash 是防止套錯方案，不代替 Jack 的授權。
- 精確核對既有固定方案：專案／地區、機構、專用 runtime、三桶、四把固定版本密文、來源 main、完整建置證據及 digest、GCS 與七年鎖定。同名服務存在即拒絕。
- 查核 runtime 沒有專案角色；專案上層改變、群組／網域／公開／聯合／未知主體或條件式專案授權都要求人工覆核。三桶的自訂角色權限與實際政策必須等於方案；四密文政策僅允許專用 runtime 的 secretAccessor。
- 重新盤點同專案桶、密文及服務帳號，產生 runtime 的正向／反向權限矩陣：必要 MMH 存取允許、其他桶／密文存取拒絕、安全／稽核物件刪除拒絕、專案 IAM 修改及對列出帳號的八種冒用／憑證路徑拒絕。另重跑舊 Compute 51 項。UNKNOWN、錯誤或回應主體／資源／permission 不符不算證據。
- 稽核桶全桶物件清單必須為空，不只看使用中的 prefix。有效權限查核耗時數分鐘，完成後再讀 metadata、政策與資源清單、再查空桶，才送建立請求。
- 只使用 Cloud Run v2 `services.create`，不使用可能更新同名服務的 replace/deploy，不提供 PATCH、DELETE 或 IAM 寫入。固定服務 ID 的建立 API 競爭時會由伺服器拒絕已有名稱，不轉為更新。[Google create API](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/create)
- 只建立初始私有服務，`invokerIamDisabled=false`，不授予 allUsers。外部一般手機仍不能直接登入；不是已完成外測公開入口。
- 每次建立前須建立全新 journal 目錄。送出前就記錄 `creation_attempted`，回應中斷時標為 outcome unknown，不自動重送。收到 operation 也只代表 submitted，不是完成。`status` 只讀 operation／服務／IAM；先取消舊綠燈，再核對 readiness、generation、image、env、runtime、資源、流量與空服務層 IAM。[Google Service 狀態](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services)
- 即使 private service ready，仍保留 GCS E2E 未通過、手機未發行；不刪除失敗服務或稽核資料來掩蓋問題。

這些檢查不會提供雲端事務鎖：最後查核與建立之間仍可能有外部管理者變更政策。權限矩陣限同專案盤點及具名主體，不能外推為任意跨專案多跳冒用證明。出現未預期設定應停止並調查，不放寬規則來消除紅燈。

## 測試

本輪離線合計 **58 個測試方法通過**：新入口 21、方案 8、metadata preflight 15、IAM verifier 14。包含同名服務、未合併来源、停用密文、額外權限、群組／條件式授權、大小寫 runtime 主體、稽核桶在檢查中變動、錯誤 IAM、POST 逾時不重送，以及讀回失敗先使舊綠燈失效。

另以九種變異移除防護：metadata gate、方案確認、精確資源政策、effective verdict、送出前空桶、私有服務讀回、舊綠燈失效、runtime 主體大小寫檢查、UNKNOWN IAM 拒絕。**9/9 由測試 assertion 捕獲**，沒有將 import／環境錯誤算成捕獲。

```sh
python -B tools/test_create_mmh_private_service.py
python -B tools/test_plan_mmh_runtime.py
python -B tools/test_check_mmh_runtime_preflight.py
python -B tools/test_verify_mmh_effective_iam.py
```

新測試已納入 p0-4-audit CI。這是 Mac Python 驗證；不宣稱 Windows PowerShell 5.1 已重跑。

## 真實唯讀 check

使用既有完整映像的方案與 build ID `85053fb9-be4d-4407-af4a-968acf8edc5e`，新 journal 執行 `check`。13 項 metadata 完整執行，7 項通過，整體 exit 1，`creation_attempted=false`。來源尚在 Draft、稽核未鎖、四把專用密文沒有可用版本證據，因此未進入新 runtime IAM 查核，更未送 Cloud Run POST。不得以離線假 gcloud 的成功案例稱為已通過真實部署或 IAM。

```sh
python -B tools/create_mmh_private_service.py check \
  --plan /path/to/runtime-plan.json \
  --build-id 85053fb9-be4d-4407-af4a-968acf8edc5e \
  --journal /path/to/new-check-directory
```

後續 `create` 要求 `--confirm-plan-sha256`，且只能使用新 journal；上述尚未通過的方案不能直接建立。鎖桶、密文及角色佈建是獨立步驟，本工具不會代做。

## 來源與待完成工作

PR #18 head `cbeafd58505ddaa845772926358be562480de1dc` 仍為 Draft／可合併；CI 9 success、Android emulator 1 skipped。它是保存 iOS／Android／Lite／共用後端的整合 PR，不只是 MMH 專用差異。PR #19 疊在它上面，來源合併須清楚涵蓋兩個 PR，不能以醫療部署為由隱藏 Lite 與共用程式範圍。

合併後須從精確 main 重新建置、驗證來源／digest，再產生新方案。不能將目前 `073732d` 映像改填新的 GIT_COMMIT。其餘工作是獨立密文與限定 IAM 佈建／有效權限驗證、不可逆稽核鎖定的具體授權、私有 GCS 讀写／冷啟動／撤回／RGB-D 驗收，再處理 App 可登入入口與分發。原先三桶及舊服務撤權驗證見 [51/51 報告](mmh_editor_migration_validation_20261008.md)。

稽核自由文字可能含識別資訊的限制仍適用；IRB 尚未送件，本階段僅合成／模擬資料。

本機證據根目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-private-entry-20261008/`，包含 `mutation-results.json`、`live-check/result.json`、`live-check/metadata.json`、`pr18-readback.json`。原始雲端證據不放公開 repo。
