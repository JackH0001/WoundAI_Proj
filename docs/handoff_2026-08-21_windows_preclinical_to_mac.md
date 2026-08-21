# Windows 臨床前整備 → Mac 合併交辦（2026-08-21）

本文件是本輪跨機交辦的入口。目標不是只讓程式「能編譯」，而是讓 Mac 接手者能以可重現的方式取得三個 repo、辨認各自責任、完成合併、跑完 Apple 平台驗證，並留下可稽核的結果。

## 1. 三個 repo 的角色與本輪分支

| repo | 角色 | Windows 本輪基線 / 分支 | Mac 處置 |
|---|---|---|---|
| `WoundAI_Proj` | 公開協作與 Android/iOS/backend/Windows 主工作面 | 基線 `5562ecf`；本輪 `codex/windows-preclinical-readiness-20260821` | 先驗證此分支，再合併回 Proj `main` |
| `WoundAI` | 私有母專案、受控 IP/臨床文件與正式整合面 | 舊 harvest `WoundAI_Proj@e1b2587`；交辦分支 `codex/mother-harvest-handoff-20260821` | 以 patch harvest 更新；不可直接把兩個 repo 根目錄覆蓋 |
| `WoundAI3D` | 3D/LiDAR/人體模型獨立研發線 | `fix/p0-depth-scale-sticker-v2`，基線 `a3f12d9` | 先做 Swift/iOS 型別與測試驗證，再單獨合併；不可混入母專案 harvest patch |

這三個 repo 是不同發布邊界。`WoundAI_Proj → WoundAI` 是受控 harvest；`WoundAI3D` 是獨立套件，不應用資料夾複製的方式塞進另外兩個 repo。

## 2. Windows 已建立的測試環境

入口：

```powershell
cd C:\dev\WoundAI_Proj
.\tools\windows\Bootstrap-WindowsTestEnv.ps1
.\tools\windows\Run-WindowsValidation.ps1 -WoundAI3DRoot C:\dev\WoundAI3D
```

環境採 project-local 安裝，不污染系統 Python/.NET/Node：

Python 預設使用 `requirements-windows-test.lock.txt` 的 2026-08-21 已驗證版本；只有在刻意做依賴升級評估時才使用 `-RefreshPythonDependencies`，升級後必須重產 lock 並跑完整矩陣。

| 元件 | 已驗證版本 / 位置 | 驗證範圍 |
|---|---|---|
| Python | 3.13.5，`.venv-windows` | 48 個離線工程測試、後端 HTTP、parity、WoundAI3D portable phantom |
| OpenCV/ONNX Runtime | venv 內，含 `cv2.aruco` | 校正、影像、推論契約 |
| .NET SDK | 8.0.424，`.tools/windows/dotnet` | solution restore/build、12 個 xUnit |
| Node | 20.19.5，`.tools/windows/node` | 前端/JS 工具前置環境 |
| Java | Temurin 17.0.16 | Android Gradle JVM |
| Android SDK/ADB | 使用本機 SDK；ADB 1.0.41 | Android JVM tests；實機另跑 `adb devices -l` |
| Git LFS | 3.5.1 | 模型指標/真檔檢核 |

`Run-WindowsValidation.ps1` 會把每次結果寫到 `artifacts/windows-test/<timestamp>/`；該目錄、venv、工具鏈與 build 產物均已加入 `.gitignore`。後端 HTTP 測試使用獨立 runtime/flywheel，測試完停止自己的 Flask process，不會寫入正式 flywheel。

本輪同時修正：

- Unicode temp path 下的 OpenCV 寫檔測試。
- 後端 HTTP 測試改用 physician token，符合新的 RBAC，不再讓 admin 冒充臨床背書者。
- parity 將 iOS-only LiDAR/Lite API 明確列為設計差異；未宣告落差仍必須為 0。
- NuGet 設定、套件快取與 AppData 隔離，避免使用者層設定讓可重現 build 失敗。
- `.NET bin/obj`、Windows toolchain 與 validation artifacts 不進版控。

最終完整執行：`artifacts/windows-test/20260821-115619/windows-summary.json`，`quick=false`、9/9 stages passed、`failures=[]`。其中 Python 48/48、隔離 HTTP 全鏈、parity 0 個未宣告落差、static logic 52/52、.NET build 0 error + xUnit 12/12、Android `testDebugUnitTest`、WoundAI3D portable phantom 均通過。此 artifact 留在 Windows 本機且不進 git；Mac 可依本文件重跑。Android 目前唯一非阻擋訊息是 Gradle 8.13 的 deprecated-feature 警告，需在升級 Gradle 9 前另案清除。

## 3. Mac 取得與驗證順序

三個 repo 都必須先保持乾淨；不要用 OneDrive/iCloud 搬 `.git`。

```bash
for repo in WoundAI_Proj WoundAI WoundAI3D; do
  git -C "$HOME/dev/$repo" status --short
  git -C "$HOME/dev/$repo" fetch --all --prune
done
```

若 Windows 分支尚未 push，先由 Windows repo 執行相對應的 `git push -u origin <branch>`。Mac 端接著：

```bash
git -C "$HOME/dev/WoundAI_Proj" switch codex/windows-preclinical-readiness-20260821
git -C "$HOME/dev/WoundAI" switch codex/mother-harvest-handoff-20260821
git -C "$HOME/dev/WoundAI3D" switch fix/p0-depth-scale-sticker-v2

git -C "$HOME/dev/WoundAI_Proj" lfs pull
git -C "$HOME/dev/WoundAI3D" lfs pull
```

任何 `.onnx`、`.mlpackage`、`.bin` 若內容以 `version https://git-lfs.github.com/spec/v1` 開頭，代表仍是 LFS pointer，不可進行 iOS release build。

## 4. WoundAI_Proj 的 Mac 驗收

前置：Xcode、Command Line Tools、Homebrew、Python 3、Git LFS、XcodeGen。

```bash
xcode-select -p
xcodebuild -version
python3 --version
git lfs version
brew install xcodegen              # 已安裝可略過

cd "$HOME/dev/WoundAI_Proj"
python3 tools/parity_check.py
python3 tools/owner_guard.py

cd iOS
xcodegen generate
xcodebuild -resolvePackageDependencies \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp
xcodebuild build \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO
xcodebuild test \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -destination 'platform=iOS Simulator,name=iPhone 15,OS=latest' \
  CODE_SIGNING_ALLOWED=NO
```

`iOS/project.yml` 是 SSOT；不要直接修生成的 `.xcodeproj`。若本機 simulator 名稱不同，先跑 `xcrun simctl list devices available` 再替換 destination。

## 5. 母專案 harvest：可重現、可回退的做法

母專案目前的已知 Proj 基線是 `e1b2587`，到本輪 Windows 起點 `5562ecf` 相差 104 commits、343 files。差異含 `_to_delete` 大量隔離檔，不可直接 `rsync --delete` 或複製 repo 根目錄。

在 Mac 建立 harvest patch，排除公共隔離區與母專案工作流程：

```bash
PROJ="$HOME/dev/WoundAI_Proj"
MOTHER="$HOME/dev/WoundAI"

git -C "$PROJ" status --short
git -C "$MOTHER" status --short
git -C "$MOTHER" switch -c codex/harvest-proj-20260821

git -C "$PROJ" diff --binary e1b2587..codex/windows-preclinical-readiness-20260821 \
  -- . ':(exclude)_to_delete/**' ':(exclude).github/**' \
  > /tmp/woundai-proj-20260821.patch

git -C "$MOTHER" apply --check /tmp/woundai-proj-20260821.patch
git -C "$MOTHER" apply /tmp/woundai-proj-20260821.patch
```

如果 `apply --check` 失敗，停止，不得改用 `--reject` 大量吞入。逐檔處理母專案在 `e1b2587` 之後的私有修正，並保留：

- 母專案專有模型、憑證、IRB/法規文件與 deployment secrets。
- `9c42a9a` 的 CI/LFS 頻寬修正。
- 母專案 `.github` 工作流程（上面的 patch 已排除）。
- 任何未去識別臨床資料；不得從 Proj/public repo 或 `/tmp` 意外加入。

套用後在母專案跑：

```bash
python3 tools/parity_check.py
python3 tools/owner_guard.py
git diff --check
git status --short
```

再依第 4 節跑 iOS build/test。審查 `git diff --stat` 與每一個 LFS 變更後才 commit。harvest commit message 必須記錄 Proj 分支與最終 SHA，方便下次從新基線增量同步。

## 6. 不得合併的內容

- `.env*`、token、憑證、keystore、Apple signing material。
- `flywheel/` runtime、SQLite、log、病患影像、DICOM、原始深度檔。
- `.venv-windows/`、`.tools/`、`artifacts/windows-test/`、`bin/obj`、Gradle/Xcode DerivedData。
- SMPL-X 原始資產或 `smplx_template.bin`；其 license 不允許隨 repo 轉散。
- `_to_delete` 只是公共 repo 的歷史隔離區，不是母專案要採收的產品碼。

## 7. 臨床前完成門檻

只有全部勾選才可把狀態從「工程整合」升為「臨床前候選」：

- [ ] Windows validation summary `passed=true`，所有 stage 0 failure。
- [ ] Proj parity 未宣告落差為 0；所有設計差異均有理由與 owner。
- [ ] Mac iOS simulator build/test 完成，輸出 log 隨 handoff 保存。
- [ ] WoundAI3D 的 Core tests、iOS build/tests 與本輪新增 P0 tests 通過。
- [ ] 至少一台支援 LiDAR 的實機完成 sticker、RGB/depth、相機姿態與 phantom 重複量測。
- [ ] 結果包含原始 capture metadata、校準 reference size、KPI-04 獨立驗證值與拒絕原因。
- [ ] 無 PHI、credentials、未授權模型/SMPL-X 資產進入任何 commit。
- [ ] 母專案 harvest commit 記錄 Proj 最終 SHA，並可從上一個 harvest 基線重放。

Windows 綠燈只能證明非 Apple 平台與可攜邏輯；Swift/ARKit/CoreML 的最終關閉責任仍在 Mac + LiDAR 實機。
