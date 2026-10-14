# iOS Google 登入恢復身分邊界追加驗證（2026-10-13）

前一目標回合分類為 progress：本機 commit 6aef9a0 與兩平台驗證完成。本輪以該 commit 為基底，使用獨立分支 `codex/mmh-restore-boundary-20261013`。尚未公開推送、部署、安裝或提交商店；先前待確認的固定 SHA 沒有被改寫。

## 新發現與修正

Android 自動恢復只接受先前記住的 Google subject；iOS 6aef9a0 卻允許 SDK 恢復成另一個 subject，之後清除 WoundAI 帳密。雖不會繼承前一人角色，但不符合「使用者明確切換才換帳號」要求。

iOS 現在自動恢復遇到不同或缺少既有 subject 時，在建立機構 transport 前拒絕並提示明確登入。手動登入可選新帳號，但只有 IAP 確認成功後才清除前一個 WoundAI 帳密並保存新的 Google subject。IAP 拒絕、網路失敗與使用者取消不等於主動登出。

原本每個 App process 只允許一次恢復嘗試，離線啟動後即使網路恢復也只能互動登入。現在失敗後再次進設定可於60秒冷卻後嘗試，避免永久放棄與密集重试。成功的 ready session 不因進設定而重建；明確登出仍清除兩層工作階段及 SDK 記憶，不自動再登入。

將 Google SDK 邊界抽成可注入 adapter，production 仍使用 GoogleSignIn 9.0.0；並未實作自行簽 token、跳過 IAP 或放寬 origin／audience。

## 本輪實測

1. 本輪完整 WoundAIInstitutionTests：92/92 通過（12:12 台北時間）。
2. 追加併發／拒絕案例並加嚴 fixture 撤權行為後，InstitutionRestoreTests：11/11 通過（12:14）。這11項包含在同一測試群組，不與92相加宣稱103項。
3. git diff --check 通過。

11項內容：
- 無 Google 既有凭證不清除 WoundAI 加密帳密、也不標示成功。
- 明確登出清兩層狀態並停止自動恢復。
- 同 subject 透過 health 驗證，不傳 WoundAI 密碼，保留加密帳密。
- 自動不同 subject 在 transport 建立前拒絕。
- 明確换帳號但 IAP 403 時保留原帳號；成功後才清除舊帳密。
- 離線恢復可在冷卻後重試，無互動登入及 SDK signOut。
- 十次重入設定只用一次 SDK 恢復／一次機構 probe。
- 成功恢復後再登出不可自行重新登入。
- SDK 回覆錯誤 audience 不安裝 transport。
- 啟動與設定同時要求恢復，只呼叫一次 SDK 與一次 probe。
- 同 subject 遭 IAP 拒絕時不顯示已登入，清除候選 transport 並保留加密帳密。

測試使用合成 SDK／IAP 回覆；未連線 Google 或 MMH。不能把這些結果當成 Apple 可自行完成審查登入的證據，也不證明冷啟動／真機授權的時間已改善。

證據：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/mobile-parity-20261013/mmh-restore-boundary-ios-20261013.log`、`mmh-restore-boundary-focused-20261013.log`。

## 完整目標仍待

- 手機與後端 auth/session 成對發布、CI、候選部署及兩平台實機登入／連拍／收據驗收。
- 獨立合成示範方向已由使用者選定；舊可行性報告的「尚未採納」是當時狀態。現在仍未實作環境切換、完整本機 namespace 隔離或建立雲端資源，不能將入口 UI 當成資料隔離。
- MMH TestFlight 最新已讀證據仍為 Build33 準備提交，尚缺示範登入、審查表單及外測提交／批准；本輪未重新操作 ASC。
- Play 內測已啟用的舊證據不能代替最新原始碼的商店安裝版驗收。本輪 Android 未重跑或安裝。
- 目標二 WoundLite 與 WoundAI3D 整合尚未開始最終驗收，不提前宣告整體完成。
