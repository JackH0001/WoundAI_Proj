# WoundAI 1.0 (27) TestFlight 發布驗證

日期：2026-10-05。結果：已上傳並啟用既有內部／外部群組。不是公開 App Store 發布，也不是臨床研究收案許可。

## 新增內容

- 傷口個案列明確標示「臨床拍攝」、相機與右箭頭。
- 已有影像時拍照按鈕顯示「重新拍照」。
- 修邊器修正首次雙指操作可能留下圓點、ROI 擴張後還原失敗、縮放加平移錨點偏移及動態標頭造成畫布變動。

## 固定來源與驗證

工作分支 codex/ios-release-assets-20261001，HEAD d7e36175020f041bef39b130f811862613902180，有既有未提交修改。不能用 HEAD 代表 IPA 內容；本輪以 source-sha256.json 固定來源，封存／IPA 匯出後逐一核對未漂移。

- project.yml 醫療 build 設為 27，PARITY 登記 Android 22 對 iOS 27 的確切差異；Lite 維持 33。
- Release XCTest：79/79 通過、0 失敗，包含七個手勢／畫面專項；前階段 Lite 共用修邊器回歸 141/141。
- static mobile logic 50 項通過；parity 未宣告落差 0。
- Swift 粗略掃描實際 target 資料夾：重複宣告、缺 import、未終止註解、不平衡括號均 0；另報 52 個未辨識參照，包含 Apple SDK 型別及 Lite 條件編譯。該工具不是完整編譯器，沒有把參照警告寫成 0；真實 Release archive 與測試均編譯通過。
- Release archive 成功；Distribution IPA 匯出成功；bundle com.woundai.app、1.0 (27)、最低 iOS 17.0。
- codesign strict 驗證成功；Distribution profile 無 ProvisionedDevices、get-task-allow=false，team LY2F24ZM68。
- IPA 隱私 manifest 與來源一致；執行檔含預期 demo URL，後端 profile 靜態檢查通過。
- IPA SHA-256：`cff2ec8a3305c17131e72ab254f3396ddd5065b8e0962e1084ba0d0f1b800a2c`。

## Apple 實際回讀

上傳 exit 0、Upload succeeded。Build ID `316f4860-00e2-4041-9437-8d267beea722`，processingState VALID、qcState BETA_APPROVED。WoundAI Internal QA 與 WoundAI 外部測試皆已加入，internalBuildState／externalBuildState 均 IN_BETA_TESTING。autoNotifyEnabled=true；不把此欄當作信件已送達證明。677 字繁中測試說明已與本機文件逐字比對，保留模擬資料、nurse 權限及 LocalStore 暫存限制；沒有變更審查登入密碼。

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-competitive-review-20261003/medical-release27-20261005/`。含 archive、IPA、測試 xcresult、來源雜湊、preflight.json、asc-build-readback.json、validation.json、upload.log 及測試說明。

## 待實機驗收

TestFlight 更新後先在設定確認 1.0 (27)，再測首次雙指縮放、第二指稍晚落下、放大 ROI 精修及 undo/redo。取消重新拍照應保留舊結果。尚未確認 Jack 裝置已安裝，也未把模擬器測試外推為所有實機手勢皆無問題。

## Lite 下一關

已核對既有一次 Linux Cloud Build SUCCESS，證據及最新狀態補入 lite_candidate_build_review.md。公開服務仍缺 Apple App Attest／GCS 全鏈路、完整 raw RGB-D 封存與撤回、研究範圍及送審配套。IRB 尚未送件；公開研究招募不是本輪可宣稱完成的項目。下一階段優先補真實整合驗收及資料保存缺口，維持兩產品獨立發布。
