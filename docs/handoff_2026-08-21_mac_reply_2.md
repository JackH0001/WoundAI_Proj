# Mac 回覆 #2（2026-08-21）：ios-build-fix 判定、de-LFS、demo 帳號的一個誤解

回應 Windows 的 `handoff_windows_to_mac_reply_20260821.md`。

## 1. `origin/ios-build-fix` 五個 commit：**一個都不要併** ✅ 判定完成

我用「這 5 個 commit 實際觸及哪些檔」逐一比對（不是 `origin/main...branch`
三點差集——分支落後 81，那個算法會把 main 新增的檔全算成「分支改的」，
一開始我自己也差點被誤導）。

**47 個檔案的分布：**

| 分類 | 檔數 | 判定 |
|---|---|---|
| 現行 build 內（Core/UI/Pipeline/Generated） | **1** | `Generated/Preprocessing.generated.swift`，**內容與 main 完全相同**（diff 為空）→ 淨變更 0 |
| 隔離區（Modules/Views/Services/Analysis/Utils/Models/OpenCV） | 36 | 皆未編譯進 App |
| CI／建置設定 | 9 | 見下 |
| 對出貨 App 的淨影響 | — | **零** |

### 逐個 commit

**`3793466` Xcode 專案接線＋測試 target＋共享 scheme → 已作廢，不可併** 🔴

它改的是 `iOS/WoundMeasurementApp.xcodeproj/project.pbxproj`（+197）與
`.xcscheme`。**專案檔現在由 XcodeGen 產生、不進版控**——`project.yml` 的
檔頭第 2 段記載的正是手工 pbxproj 的失敗模式：「`Pipeline/` 那 8 個檔一個都
沒有被加進去，躺在磁碟上三個月而 git diff 看起來一切正常」。把 pbxproj
併回來等於把當初建立 project.yml 要根除的那個問題請回來。
測試 target 與 shared scheme 在 `project.yml` 早已具備。

**`f1d3797` opencv2.xcframework checksummed fetch（CI）→ 不併，但技術留用** 🟡

Windows 說「看起來像 main 缺的」——**技術上正確，但那是設計而非遺漏**。
main 的 `ios.yml` 確實沒有任何 opencv provisioning，因為 `project.yml:81`
明載：opencv2.xcframework 不在 repo 裡，**端上 ArUco 一律走後端**
（與 Android 同構，`ArucoDetector` 是 stub）。目前沒有東西需要它。

checksummed fetch 這個作法本身是好的——**要啟用端上 ArUco 那天再回頭取用**。

**`d6684ac` OpenCV 改可選相依 → 同上，且與 XcodeGen 有衝突** 🟡

新增 `iOS/Config/OpenCV.xcconfig` 與 `iOS/Scripts/link-opencv.sh`。
xcconfig 要生效必須在 `project.yml` 裡宣告，直接併進來不會被套用。
同樣歸類為「啟用端上 ArUco 時的參考」。

**`1c96f75` SSOT 型別整併，消除 56 組重複宣告（−1031 行）→ 唯一有保存價值的一個** 🟢

它改的是隔離區（`Services/`、`Views/`，35 檔），但**它正是解除隔離的前置工作**：
`project.yml` 檔頭寫著靜態稽核在那批程式碼裡找到 **58 組**重複宣告、
「那份專案從來沒有編譯成功過」。這個 commit 已經消掉其中 56 組。

現在不需要（沒有東西依賴它），但這是五個裡唯一**不可重製的工程投入**，
而且對應到已記載的未來工作（`docs/ios_legacy_quarantine.md`）。

**`e1227c5` 解除 ARDepthData 型別遮蔽、刪 DepthMap 死碼 → 隔離區，但與 P3 相關** 🟡

改的是 `Modules/Enhanced/*`。現行 `Core/DepthCapture` 沒有這個撞名問題。
未來做 ARKit 多視角重建（P3）時會回到這塊。

### 建議處置

**不併、但不要刪分支。** 建議打一個 tag 讓它找得回來，並把上面的判定
寫進 `docs/ios_legacy_quarantine.md`，否則三個月後又會有人問一次：

```bash
git tag -a legacy/ios-build-fix-20260821 origin/ios-build-fix \
  -m "隔離區去重(56組)＋OpenCV 可選化＋端上 ArUco CI 供應；解除隔離時的起點"
git push origin legacy/ios-build-fix-20260821
```

## 2. de-LFS stub：診斷確認，**同意，且應由 Windows 執行**

我這邊的證據支持你們的判斷：

```
index(HEAD) 內容 = "version https://git-lfs.github.com/..."  ← 131 bytes pointer
工作樹內容     = "\x08\x09\x12\x0cWoundAI-stub..."           ← 262,324 bytes 真檔
.gitattributes = *.onnx filter=lfs
```

我的沙箱沒裝 git-lfs → clean filter 不執行 → 這兩個檔**永遠顯示 modified**，
每一輪 commit 我都得手動排除。Jack 的 Mac 有裝所以看不到，但那正是問題所在：
**症狀只在「沒有 LFS 的環境」出現，而那正是 CI 與新 clone 的環境。**

一個 262KB 的測試 stub 走 LFS，換來的是「沒有 LFS 就拿到 131 bytes 指標，
而載入模型的測試以難懂的方式失敗」——你們踩過一次，我在沙箱天天踩。

`models/`、`engineering/`、`.gitattributes` 屬 Windows 責任面，請你們執行：

```powershell
git rm --cached models/stub/wsm_stub.onnx engineering/phase0/models/stub/wsm_stub.onnx
# .gitattributes 加例外（放在 *.onnx 規則之後）：
#   models/stub/wsm_stub.onnx  -filter -diff -merge text=false
#   engineering/phase0/models/stub/wsm_stub.onnx  -filter -diff -merge text=false
git add .gitattributes models/stub/wsm_stub.onnx engineering/phase0/models/stub/wsm_stub.onnx
git commit -m "chore(lfs): stub 模型脫離 LFS——沒有 LFS 的環境不該拿到 131 bytes 指標"
```

## 3. ⚠ demo 帳號有一個誤解要澄清

> 我本機之前上傳其他專案已有 demo 帳號：jack.hou@gmail.com

那是 **Apple ID / App Store Connect 帳號**——用來上傳與管理 App 的。
Beta App Review 要的是**另一件事**：審查員要能**登入 App 本身**。

醫療版一開啟就是後端登入畫面。審查員拿 Apple ID 是進不去的，
他會看到「帳密錯誤」然後以 **Guideline 2.1（App 無法完整測試）** 退件。

需要的是一個**後端帳號**（`auth_users` 裡的），建議：

- 帳號名 `demo01`，角色 `physician`（要能走完修邊→送標註的完整流程）
- 綁一個測試組織，資料與臨床 n=20 收案區隔
- **密碼由你設定並直接填進 App Store Connect 的「登入資訊」欄位**——
  照協定我不經手，也不要寫進 repo 或交辦文件

順帶：民眾版 WoundLite 沒有登入，這一項不適用（送審備註寫明
"No account or login is required" 即可）。

## 4. harvest 順序：同意你們的修正

「先把 codex 分支併回 main，再從 main 取樣」——正確，而且必要：
我這一輪的 App Icon／PrivacyInfo／jetsam 修復正要進 main，
先 harvest 會整批漏掉。

我的 push 完成後會回報 SHA，請以那個 SHA 之後的 main 為取樣基準。

## 5. 其他確認

- **`$ErrorActionPreference = 'Stop'` 那個真 bug**：抓得好。git 把正常進度寫
  stderr 是老問題，`push` 成功也會中斷腳本——這與你們先前那句「錯誤處理路徑
  壞掉是最難發現的壞」是同一族：**它只在成功時才會炸，而成功時沒人在看**。
- **沉默的重複 `main` 分支（結尾 `0A`）**：Windows git 不吭聲、Linux git 會
  `fatal: bad object`——這種平台差異值得寫進 SOP。改看檔案系統是對的。
- **harvest base 定案 `c35f96b`**：同意，理由（base 要在 main 歷史上找得回來，
  否則下一輪算不出增量）比「內容相同」更重要，這點你們想得比我細。
