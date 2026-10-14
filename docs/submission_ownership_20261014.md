# 提交場域更新 — 2026-10-14

Jack 在本工作串明確授權：「授權將本機也改為合格提交場域」。此更新接續110檔整合來源的Mac例外提交／推送授權，正式將Backend與engineering納入共用提交範圍。先前文件中windows-only的說明是歷史狀態，以本決策與 tools/owner_guard.py 為準。

| 範圍 | 提交分工 |
|---|---|
| iOS 與 iOS workflow | Mac |
| Backend、engineering、Android、tools、docs | 共用；Mac可提交 |
| Windows App 與 Android workflow | 保留原Windows分工 |

這不更動CI驗證內容的責任，也不表示Mac跑過的測試等同Windows／PowerShell實測。部署腳本修改後仍須相應平台／執行環境驗證；尚未執行的測試必須明列待驗。來源提交資格不包含任意雲端變更、合併或商店發布授權。

新增7項所有權回歸，涵蓋共享後端、保留iOS／Windows專屬範圍、混合變更拒絕、權威版本還原與Git狀態讀取失敗。CI納入此回歸。整合來源110檔先獨立提交，再以本筆更新規則，保留可追溯邊界；不改寫先前凍結交接包。
