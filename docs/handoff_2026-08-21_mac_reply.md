# Mac 回覆：臨床前整備交辦評估（2026-08-21）

對 `handoff_2026-08-21_windows_preclinical_to_mac.md` 的評估。
結論：**文件品質高、方向正確，但目前有兩件事讓 Mac 端照著做會卡住**，
另有四項技術風險與門檻缺口需要補。

## 1. 先講好的部分

- 三個 repo 用**發布邊界**（不是資料夾）來區分，harvest 走 patch 而不是
  `rsync --delete`——這是對的，而且是少數人會做對的地方。
- 明列「不得合併」清單（PHI、secrets、SMPL-X、runtime、`_to_delete`）。
- `apply --check` 失敗就停、**不得改用 `--reject` 大量吞入**——正確。
- 「Windows 綠燈只能證明非 Apple 平台與可攜邏輯」——這句自我設限寫得誠實，
  與本專案一路的除錯經驗一致（tuple 未解包、blueprint 註冊順序都是
  跑起來才炸、靜態檢查全過）。

## 2. 阻擋執行的實際狀況（Mac 端已查證）

### 2.1 交辦分支沒有 push 🔴

`git ls-remote` 的實況：origin 只有 `main`、`feat/phase1-guards`、
`fix/backport-from-source-20260707`、`ios-build-fix`。
**`codex/windows-preclinical-readiness-20260821` 不存在**；
`tools/windows/**` 在 `origin/main` 的檔案數是 **0**。

→ 文件第 2 節（`Bootstrap-WindowsTestEnv.ps1` / `Run-WindowsValidation.ps1`）
與第 3 節的 `switch` 指令，Mac 端現在都執行不了。

**但好消息**：本輪的**產品碼已經在 `origin/main`**（`5562ecf`，較 Mac 本機
領先 10 個 commit），而且把 Mac 前一輪交辦的三件事全做完了：
`on_weak` 放寬、lay 修正率、console「民眾版資料」頁籤，另加影像檢視器
與組織訓練集匯出鏈。所以**卡住的只有 Windows validation 工具鏈，不是功能**。

請 Windows 端執行：
```powershell
git push -u origin codex/windows-preclinical-readiness-20260821
```
（`WoundAI` 與 `WoundAI3D` 的交辦分支同理。）

### 2.2 路徑與環境假設不符 🟠

- 文件假設 `$HOME/dev/<repo>`；這台 Mac 是 **`~/Developer/WoundAI_Proj`**。
- **`WoundAI`（母專案）與 `WoundAI3D` 在這台 Mac 上不存在**，尚未 clone。
  第 5 節的 harvest 在 clone 完成前無法開始。
- 第 4 節的測試 destination 寫 `iPhone 15`；本機是 Xcode 26.6，模擬器是
  iPhone 17 系列（iOS 26.x），該行會直接失敗。文件有提醒先跑 `simctl`，
  但預設值建議改成 `platform=iOS Simulator,name=iPhone 17`。

## 3. 技術風險（文件未涵蓋，建議補）

### 3.1 harvest patch 帶不動 LFS 真檔 🔴 高

`git diff --binary` 對 **LFS 追蹤的檔案**輸出的是 *pointer 文字*的 diff，
不是真檔內容。套進母專案後會得到一個指向 **Proj 的 LFS 儲存**的 pointer；
若母專案 LFS remote 取不到該 oid，那個模型檔就**永遠 checkout 不出真檔**
——而且 `git status` 乾淨、CI 可能也過，直到有人要拿它做 iOS release build。

本輪已經有前例：Windows 端曾因為 clone 沒有 LFS 而拿到 131 bytes 指標，
誤判成「檔案變更」。同一個坑在 harvest 會更難發現。

建議：harvest 前先 `diff` 兩個 repo 的 `.gitattributes`；LFS 檔案不靠 patch
帶，改為「Proj 端 `lfs pull` 取真檔 → 母專案 `lfs track` 相同 pattern →
逐檔複製 → `lfs push`」，並在 harvest commit 後逐一驗證檔頭不是
`version https://git-lfs.github.com/spec/v1`。

### 3.2 `':(exclude).github/**'` 會漏掉 iOS CI 的兩處修正 🟠

Proj 的 `ios.yml` 本輪修過兩件事，母專案若也跑 iOS CI 會重蹈同一坑：
1. 新增 **XcodeGen 產生專案**步驟（`.xcodeproj` 未進版控）。
2. **選最新 Xcode**（brew 版 XcodeGen 產出 objectVersion 77，Xcode 15.4 讀不了）。

排除 `.github` 是對的（母專案有自己的工作流程），但這兩段需要**手動 port**。

### 3.3 臨床前門檻缺「上架面」🟠

第 7 節的 8 條門檻全是工程整合面，沒有任何 App Store／TestFlight 條件。
但門檻的目的是「可進入臨床」，而**臨床收案要靠 TestFlight 發佈**，
所以至少這三項要進門檻（詳見 `docs/app_store_submission_plan.md`）：

- [ ] **App Icon 有實際 PNG**——目前 `AppIcon.appiconset` 只有 `Contents.json`，
      **沒有任何圖檔**。這會讓 Archive 失敗，TestFlight 根本上傳不了。
- [ ] `PrivacyInfo.xcprivacy`（2024-05 起強制；UserDefaults 需宣告 CA92.1）。
- [ ] TestFlight 示範帳號（Beta App Review 需要能登入；密碼由 Jack 設定）。

民眾版另有 App Attest 與法規分類問題，不影響醫療版 TestFlight。

### 3.4 jetsam 記憶體終止未列入 🟠

2026-08-19 實測：WoundLite 曾被 iOS 以
`Terminated due to memory issue (code 9)` 終止。臨床收案時 App 拍到一半死掉
＝那一筆資料連同深度側檔一起沒有，而醫師不會重拍第二次。
**建議列為臨床前門檻的必修項**（Mac 端待辦 #40，用 memory gauge 重現）。

## 4. 門檻第 5、6 條：其實已有大量資料，但散在對話裡

文件要求「至少一台 LiDAR 實機完成 sticker、RGB/depth、相機姿態與 phantom
重複量測」「結果包含原始 capture metadata、校準 reference size、獨立驗證值」。

Mac 端 2026-08-18～19 其實已經做完大部分：

| 項目 | 結果 |
|---|---|
| Phantom 印刷樣張（多尺寸） | 正拍投影 vs 貼紙 −2.6%～+5.0% |
| 斜拍（11°～55°） | 投影低估 cosθ；**投影÷cos(傾角) 對真值 ±3%** |
| 表面積雜訊修正驗證 | 平滑後平面樣張 surface≈projection（1.00） |
| 品質閘觸發 | 比值>1.25、攝距<22cm、傾角>25° 均如設計 |
| Lite 端到端五筆 | route 三桶、consent_version、depth stored 全數對帳通過 |

**這些目前只存在於對話與截圖，沒有進 repo。** 建議由 Mac 端整理成
`docs/phantom_validation_2026-08.md`（含每筆 capture metadata 與計算值），
才算滿足第 6 條「可稽核」。這是我下一步可以直接做的事。

## 5. 建議執行順序（Mac）

1. `git pull` 取回 origin/main 的 10 個 commit（Mac 本機領先 0，可 fast-forward）。
2. **請 Windows push 三個交辦分支**（否則 2/3/5 節全部停擺）。
3. clone `WoundAI` 與 `WoundAI3D` 到 `~/Developer/`（統一路徑，不用 `~/dev`）。
4. Mac 端 iOS 驗收（第 4 節），destination 改 iPhone 17。
5. 補 phantom 驗證歸檔（§4）＋ jetsam 修復（§3.4）＋ App Icon／隱私宣告（§3.3）。
6. harvest 最後做，且先解決 LFS 議題（§3.1）。

## 6. 一句話總評

方向與紀律都對，**目前不是「Mac 該不該接手」的問題，是分支還沒 push、
另外兩個 repo 還沒 clone**。功能面反而超前——Windows 已把上一輪交辦
全數完成並進了 main。
