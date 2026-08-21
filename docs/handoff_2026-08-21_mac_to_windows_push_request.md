# 交辦 Windows（2026-08-21）：三個 repo 的分支推送 ＋ 臨床前門檻補充

## 1. 唯一的硬阻擋：交辦分支沒有 push 🔴

`handoff_2026-08-21_windows_preclinical_to_mac.md` 第 2、3、5 節的所有指令，
Mac 端目前**一行都跑不了**。實查（2026-08-21）：

```
origin 上只有：main / feat/phase1-guards /
              fix/backport-from-source-20260707 / ios-build-fix
origin/main 內 tools/windows/** 的檔案數：0
```

請在 Windows 端執行：

```powershell
cd C:\dev\WoundAI_Proj
git push -u origin codex/windows-preclinical-readiness-20260821

cd C:\dev\WoundAI
git push -u origin codex/mother-harvest-handoff-20260821

cd C:\dev\WoundAI3D
git push -u origin fix/p0-depth-scale-sticker-v2
```

推完請回報三個分支的 SHA，Mac 端會照第 3–4 節驗收並回報 log。

⚠ 另外請確認：`WoundAI` 與 `WoundAI3D` 的 **remote URL 與存取權限**
（Mac 端這兩個 repo 尚未 clone，需要知道要 clone 什麼）。

## 2. Mac 端本輪已完成（不需 Windows 動作，僅供對帳）

| 項目 | 狀態 | 檔案 |
|---|---|---|
| App Icon ×2（1024×1024，RGB 無 alpha） | ✅ | `tools/make_app_icons.py`（圖示 SSOT，可重生） |
| Lite 資產目錄分家 | ✅ | `iOS/WoundLite/Assets.xcassets/` |
| `PrivacyInfo.xcprivacy` ×2 | ✅ | 兩個 target 各一，已掛 resources |
| Release 設定體檢 | ✅ | 後端網址正確、🔧 診斷行確認只在 `#if DEBUG` |
| jetsam 記憶體修正 | ✅ | Lite 縮圖／詳情頁改非同步（見 §3） |
| 實機驗證歸檔 | ✅ | `docs/phantom_validation_2026-08.md` |
| 上架計畫 | ✅ | `docs/app_store_submission_plan.md` |

已確認 Windows 本輪把上一批交辦全數完成並進 main：`on_weak` 放寬
（`LITE_WEAK_FRAC` 預設 0.01，比 Mac 建議的 0.3–0.5% 更寬鬆，合理）、
lay 修正率、console「民眾版資料」頁籤、影像檢視器、組織訓練集匯出鏈。

## 3. jetsam 根因（供 Windows 參考同類問題）

2026-08-19 的 `Terminated due to memory issue (code 9)`，根因是 Mac 端
自己寫的 Lite 紀錄列表**在 SwiftUI 的 `body` 裡同步呼叫 `loadThumbnail`**
——body 會被反覆求值，於是每列每次重繪都做一輪「AES 解密整張 JPEG ＋
解碼縮圖」。醫療版 `TimelineCharts` 早就是對的做法，Lite 是後寫的那版偷懶。

已改為 `.task(id:)`＋`Task.detached`＋`maxPixel: 160`，詳情頁的 `loadFull`
同步呼叫也一併改非同步。

**可移植的教訓**：與你們的「錯誤處理路徑壞掉是最難發現的壞」同族——
**在宣告式 UI 的 body 裡做 I/O，錯誤不會報，只會慢慢吃光記憶體**。

## 4. 臨床前門檻建議補三條（第 7 節）

現行 8 條門檻全是工程整合面，但門檻的目的是「可進入臨床」，
而**臨床收案要靠 TestFlight 發佈**。建議加入：

- [ ] 兩個 target 的 App Icon 有實際 PNG（Archive 前置；本輪已補）
- [ ] 兩個 target 的 `PrivacyInfo.xcprivacy` 存在且進 resources（本輪已補）
- [ ] TestFlight 示範帳號可登入（Beta App Review 需要；**待 Jack 建立**）

民眾版另有 App Attest 與醫材法規分類，屬 App Store 路線，不影響
醫療版 TestFlight。詳見 `docs/app_store_submission_plan.md`。

## 5. 給 Windows 的一個提醒：harvest 的 LFS 陷阱

母專案 harvest 用 `git diff --binary`，但**LFS 追蹤的檔案在 patch 裡是
pointer 文字**，套進母專案會得到一個指向 Proj LFS 儲存的指標。
若母專案 LFS remote 取不到該 oid，那個模型檔就永遠 checkout 不出真檔
——而 `git status` 乾淨、CI 可能也過，直到有人要做 iOS release build。

本輪已有前例（Windows clone 沒有 LFS，拿到 131 bytes 指標誤判為檔案變更）。

建議：harvest 前先 `diff` 兩 repo 的 `.gitattributes`；LFS 檔案不靠 patch 帶，
改為「Proj `lfs pull` 取真檔 → 母專案 `lfs track` 相同 pattern → 逐檔複製 →
`lfs push`」，harvest commit 後逐一驗證檔頭不是
`version https://git-lfs.github.com/spec/v1`。

另：patch 排除 `.github/**` 是對的，但 Proj 本輪修過的 iOS CI 兩處
（新增 XcodeGen 產生步驟、選最新 Xcode 以避開 objectVersion 77）
需要**手動 port** 到母專案，否則會重蹈同一坑。
