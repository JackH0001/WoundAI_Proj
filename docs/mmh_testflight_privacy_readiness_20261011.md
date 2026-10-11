# MMH TestFlight 外测資料與隱私揭露核對（2026-10-11）

## 現況與本輪結果

實際開啟 App Store Connect 的 WoundAI MMHPS（6821572870）測試資訊頁，工作階段有效。原本 Beta 描述、回饋信箱、審查聯絡、審查帳號與隱私政策 URL 都未填。

本輪已在表單準備繁體中文 Beta 描述、使用者先前授權的審查聯絡資料，以及英文審查備註草稿，明示只限合成／模擬測試、Google 入口與 WoundAI 角色兩層登入、標註與深度保存回覆。沒有填造審查帳號，也沒有使用個人或管理者密碼。

勾選正確的「需要登入」後，Apple 要求使用者名稱及密碼，儲存按鈕為 disabled。因此上述資料**僅為未保存的表單草稿**，另已保存到本機 evidence；不是 ASC 已保存／送審的資訊。未新增外部測試者、外部群組或提交 Beta Review。沒有以取消「需要登入」迴避必填項目。

## Build 33 真實封存核對

讀取 `/private/tmp/WoundAI-MMHPS-33.xcarchive/Products/Applications/WoundAIInstitution.app`：

- Bundle ID `com.woundai.app.mmhps20261007`，版本 1.0，build 33 三項一致。
- 共找到並成功解析 **11 份 PrivacyInfo.xcprivacy**；逐份記錄路徑與 SHA-256。
- App 本身宣告影像／影片、健康資料、使用者 ID。
- GoogleSignIn 的實際打包 manifest 宣告 8 類，與 App 合併後共 **10 種不重複資料類型**。
- 這些資料類型宣告均為 linked=true、tracking=false。這是封存中的宣告內容，不是網路流量觀測，也不證明每次登入都收集全部種類。

| 資料類型 | 封存宣告來源 | 宣告用途 | 送審前核對重點 |
|---|---|---|---|
| 照片／影片 | App | 功能、其他 | 合成 RGB、修訂遮罩；不可宣稱完全匿名 |
| 健康資料 | App | 功能、其他 | 量測／組織／深度欄位；合成測試限制不應被誤解為正式臨床核准 |
| 使用者 ID | App、Google SDK | 功能；SDK 另含分析 | Google 主體與 WoundAI 角色識別不同，須分別說明 |
| 姓名 | Google SDK | 功能 | SDK 宣告，不等於 App 主動上傳病患姓名 |
| 電子郵件 | Google SDK | 功能 | App 亦顯示 Google 登入 Email；IAP 入口授權依身分核對 |
| 電話號碼 | Google SDK | 功能 | 需按 SDK／Google 登入流程揭露，不推論 App 讀取電話簿 |
| 概略位置 | Google SDK | 功能 | 官方提及可由 IP 推估概略位置防詐；不是本輪發現 GPS 採集 |
| 裝置 ID | Google SDK | 分析 | 不可沿用「不處理任何裝置識別資料」的絕對聲明 |
| 其他使用資料 | Google SDK | 分析 | 問卷需涵蓋第三方 SDK；不等同廣告追蹤 |
| 其他資料類型 | Google SDK | 功能、分析 | 需結合 SDK 文件與實際設定核對，不能只憑類別名猜內容 |

Apple 要求 App Store 資料揭露涵蓋 App 與第三方合作夥伴的處理；隱私 manifest 與商店問卷並非互相取代。參照 [Apple App Privacy Details](https://developer.apple.com/app-store/app-privacy-details/) 與 [Privacy manifests](https://developer.apple.com/documentation/bundleresources/describing-data-use-in-privacy-manifests)。

Google 的 iOS 登入資料揭露文件目前列明，使用者識別碼及 IP 可用於登入／OAuth 與防詐，其中 IP 可能用來估計概略位置。該簡短文件不能用來刪減封存 manifest 的其他宣告，也不能把 manifest 的通用類別當成已做過封包驗證。參照 [Google Sign-In App Privacy](https://developers.google.com/identity/sign-in/ios/app-privacy)。

## 不可直接沿用的舊資料

`site/privacy/woundai.html` 仍標示草稿，且未完整說明 MMH Google 身分入口。舊 demo 隱私說明的服務、保留範圍與帳號流程也不能直接視為 MMH 已核准政策。本輪沒有把任何草稿網址填成生效政策、沒有修改或發布對外資料處理承諾。

現有 SDK 已隨 Build 33 帶入自身 manifest，不是因 App 根目錄 manifest 沒列 Email 就能斷言二進位漏打包。此次先保留真實 SDK 宣告並完成問卷對照，不修改第三方 manifest 來讓揭露看似較少。

## 完成外測前剩餘工作

1. 確認專供合成審查的 Google 身分／入口方案，以及最低必要的 WoundAI 測試角色；從無既有登入狀態的裝置驗證可用性。新的入口成員須具體授權，不以個人 Google 帳密供審查。
2. 補齊 MMH 公開隱私說明，包括 Google／IAP 與角色帳號、上傳內容、實際保留與撤回範圍、私密副本／手動匯出、聯絡窗口，再由負責人核閱發布。不將未核對的保留期限或全面刪除當成承諾。
3. 依 App、後端與 SDK 證據完成 App Privacy 問卷；不以本表直接宣稱所有選項已適用、已提交或獲准。
4. 用實際可用審查帳號更新英文備註，移除準備階段標示，保存測試資訊後才提交 Beta Review，取得外測可測狀態並實際分發驗收。

證據：[封存 manifest 清單與雜湊](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/testflight-privacy-review/archive-manifests.json)、[未保存的表單草稿](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/testflight-privacy-review/testflight-draft.json)、[必填登入欄位畫面](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/testflight-privacy-review/testflight-review-login-required.jpg)。
