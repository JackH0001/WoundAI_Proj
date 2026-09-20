# WoundAI P0-4 PR #6/#7 後續覆核：Windows → Mac（2026-09-13）

## 1. 本次交接範圍

本文件取代 2026-09-05 交接對「尚未合併 PR #4」的時點描述，但保留舊文件作歷史證據。
本輪處理 PR #6/#7 合併後發現的測試隔離、CI 等待、GCS lifecycle 驗證與本機 HTTP
驗收缺口；不代表臨床就緒，也不授權建立/鎖定新桶、合併本輪 PR、部署或切換流量。

- Repo：`https://github.com/JackH0001/WoundAI_Proj.git`
- 本輪分支：`codex/pr6-followup-isolation-20260908`
- 基底：`main@b1921239a342f11d1fff73178bb73b653b62d925`
- PR #4 merge：`26ecf5489bb7a1c5bcc725668684a6909ae089fc`
- PR #5 merge：`adcf2063e593e58d782d4ff304e7b30ff881eb8b`
- PR #6 merge：`a994bb8c321d2062b17ef8e48175b5c25b50ffc5`
- PR #7 merge：`b1921239a342f11d1fff73178bb73b653b62d925`

取得本輪分支後，應以遠端實際 head 與該 head 的 checks 為準，不以本文件中的基底 SHA
替代 head 驗證。

## 2. 已修正的缺口

1. 測試 runner 會大小寫不敏感地移除 `WOUNDAI_STORE`、所有 `WOUNDAI_GCS_*` 與
   `WOUNDAI_AUDIT_BUCKET`，並在建立任何 GCS client／ADC 探索之前 fail-closed。
   已快取的真 GCS client 也不能在測試中重用。
2. 每一支 Python 測試有獨立的 runtime 與 flywheel 目錄；`app.py` 的 log、SQLite、
   uploads 與 processed 可由 `WOUNDAI_RUNTIME_DIR` 移出 source tree。
3. 本機 HTTP E2E 只接受程式產生的合成 JPEG、隨機憑證、動態 loopback port 與逐回應
   run-id；不接受任意圖片路徑、DNS host、proxy、redirect 或未證明身分的 server。
4. CI gate 在等待期間會重新查詢所有 workflow，且在作出結論前再讀一次 remote ref；
   結論明示只是當下快照，後續 merge 仍須綁定精確 SHA。
5. GCS lifecycle readback 會比對每個 action/condition 的完整欄位集合。額外 suffix、
   storage class、`numNewerVersions` 等會直接失敗，不會被縮寫後的表面值掩蓋。
6. 部署入口明確拒絕退役稽核桶與大小寫變體；bucket 名稱以大小寫敏感規則驗證。
7. `chain_integrity_ok()` 只回答完整性判斷；寫入端另行要求全鏈為目前版本，文件語意已
   與實際 appendability 分離。

## 3. 雲端現況（2026-09-13 唯讀重查）

- Cloud Run 100% 流量仍在 `woundai-backend-00039-xdk`，環境的 `GIT_COMMIT=505ff2e`。
- 該 revision 仍使用舊稽核桶 `woundai-flywheel-jackh001-audit` 與預設 Compute service
  account；P0-4 candidate 尚未部署。
- `woundai-flywheel-jackh001-audit-epoch-20260905` 的 7 年 retention 仍為 locked，桶內
  有 125 筆由 2026-09-06 測試環境外洩造成的合成稽核紀錄。
- 上述 9/5 epoch 是事故證據：只允許讀取與驗鏈，禁止新增 receipt/audit、禁止部署。
- 新的正式 audit epoch 尚未命名、建立、核准或驗證。

因此不得把「桶已鎖」解讀為 P0-4 已上線，也不得用退役桶進行 App E2E。

## 4. Mac 取得與驗證

```bash
cd "$HOME/dev/WoundAI_Proj"
git status --short
git fetch origin --prune
git switch --create codex/pr6-followup-isolation-20260908 \
  --track origin/codex/pr6-followup-isolation-20260908
git lfs pull
git rev-parse HEAD
git merge-base --is-ancestor b1921239a342f11d1fff73178bb73b653b62d925 HEAD
git diff --check b1921239a342f11d1fff73178bb73b653b62d925..HEAD
```

若本機已有同名分支，改用 `git switch codex/pr6-followup-isolation-20260908` 後
`git pull --ff-only`；不要覆蓋未提交內容。

Python 與合成 HTTP 驗證：

```bash
cd "$HOME/dev/WoundAI_Proj"
PYTHON_BIN="$(command -v python3.13)" || {
  echo 'STOP: 本輪 test lock 以 CPython 3.13 解析；請先安裝 Python 3.13，不要臨時改版本。' >&2
  exit 1
}
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
VERIFY_ROOT="$HOME/WoundAI_validation/pr6-followup-$RUN_STAMP"
VENV_ROOT="$HOME/.cache/woundai/venvs/pr6-followup-$RUN_STAMP"
mkdir -p "$VERIFY_ROOT"
"$PYTHON_BIN" -m venv "$VENV_ROOT"
source "$VENV_ROOT/bin/activate"
python --version 2>&1 | tee "$VERIFY_ROOT/python-version.txt"
python -m pip install -r requirements-windows-test.lock.txt
python -m pip check
python -c 'import cv2, fastapi, flask, httpx, jsonschema, pytest, yaml; print(cv2.__version__)'
python -m pip freeze --all > "$VERIFY_ROOT/pip-freeze.txt"
python -B tools/windows/run_python_tests.py \
  --repo "$PWD" --out "$VERIFY_ROOT/python" --timeout 180
python -B tools/windows/run_backend_http_test.py \
  --out "$VERIFY_ROOT/http" --timeout 300
```

虛擬環境與驗證產物都刻意放在 repo 外；否則 runner 的 before/after source snapshot
會把自己的環境或輸出視為來源樹變動並正確判定失敗。

`requirements-windows-test.lock.txt` 是目前唯一涵蓋 pytest、FastAPI、HTTPX、
jsonschema、PyYAML 與 ArUco 的完整精確版本清單；它在 Windows／CPython 3.13.5
解析，尚不是經 Mac wheel hash 覆核的跨平台 lock。Mac 必須保留上面的 `pip check`、
import probe 與 `pip-freeze.txt`；若安裝需要改任何版本，先停止並回交差異，不得把
自行重解依賴後的綠燈當成同一棵環境的證據。

HTTP 驗證必須顯示 `synthetic_data_only=true`、`storage=local`、
`cloud_storage_variables_inherited=false`、`temporary_runtime_removed=true`。不要在同一 shell
手動 export GCS 變數；runner 雖會移除它們，操作者環境仍應保持清楚。

## 5. iOS build/test

```bash
cd "$HOME/dev/WoundAI_Proj/iOS"
xcodegen generate
xcodebuild -resolvePackageDependencies \
  -project WoundMeasurementApp.xcodeproj -scheme WoundMeasurementApp
xcodebuild build \
  -project WoundMeasurementApp.xcodeproj -scheme WoundMeasurementApp \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO
xcodebuild test \
  -project WoundMeasurementApp.xcodeproj -scheme WoundMeasurementApp \
  -destination 'platform=iOS Simulator,name=iPhone 15,OS=latest' \
  CODE_SIGNING_ALLOWED=NO
xcodebuild build \
  -project WoundMeasurementApp.xcodeproj -scheme WoundLite \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO
```

保存 Xcode/macOS 版本、實際 destination、完整 stdout/stderr 與最終 Git SHA。若 simulator
名稱不存在，以 `xcrun simctl list devices available` 選一台可用裝置。

## 6. 接續順序與禁止事項

1. 本輪分支經 PR checks 與 Mac build/test 通過後，另行決定是否合併。
2. P0-2（JWT 撤銷）與 P0-5（Android 離線事件順序/最後意願）仍須完成。
3. 另行設計並核准新的空白 audit epoch；建立、Bucket Lock、runtime identity 都是不同
   變更，不能沿用 9/5 epoch 的舊授權。
4. 僅在新 epoch、least-privilege identity 與 merged `main` 齊備後，才可建立 no-traffic
   candidate；Mac/App E2E 對準 candidate，而非 production live URL。
5. candidate 驗證不等於切流量。流量切換需新的精確授權與 P0-2/P0-5/臨床前清單證據。

在上述條件完成前，正式服務維持範例／模擬資料限制，禁止放入真人或 PHI 影像。
