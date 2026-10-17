# 醫療版結構化部位與評估：第一階段實作

日期：2026-10-17。這是來源分支的基礎實作，尚未部署、未替換商店版表單，也未啟用新臨床量表。

## 已實作

- 同一份有版本代碼表產生 iOS、Android 與後台離線字典；48 個部位、16 種主要成因、7 種共存因素。這是候選清單，不宣稱已達成特定臨床覆蓋率。
- 兩端選單元件：身體區域 → 部位 → 左右側／表面／指趾 → 成因；共存因素另列，特殊補充平時收合。更換部位時清除不相容選項。
- 三端嚴格拒絕未知欄位、非字串代碼、非法組合、重複因素及未支援版本。糖尿病共存因素不會把壓傷自動改成糖尿病足。
- 後端 annotation 可接受 `wound_profile`；同內容重送去重，更正 profile 建立新修訂；保存回執回傳實際存下的 profile。舊請求不增加此欄位，也不改舊去重身分。
- iOS／Android 傳輸介面有對應可選參數與收據驗證；舊後端沒有回傳新資料時拒絕宣稱保存成功。
- 研究匯出保留 profile 並重新驗證；自由備註不在研究封包白名單內。

## 自動整理與人工輸入界線

| 傷口成因／用途 | 候選分類或量表 | 可由系統協助 | 仍須人工或檢查資料 |
|---|---|---|---|
| 壓力性損傷 | 分期、PUSH 追蹤 | 確認完整輸入後依指定版本算分 | 分期；標準長×寬、滲液及組織類別確認 |
| 糖尿病足 | SINBAD | 已確認部位、有效面積可作候選預填；六項齊全才加總 | 缺血、感覺、感染、深度 |
| 糖尿病足感染 | IWGDF／IDSA | 臨床輸入齊全後依規則分類 | 局部／全身徵象、紅斑範圍、深部侵犯 |
| 專科血管評估 | WIfI | 完整分項後查規則表 | 傷口程度、血流生理量測、足部感染 |
| 靜脈性傷口 | CEAP、VCSS | CEAP 顯示分類；VCSS 分項確認後加總 | 腿層級疼痛、水腫、皮膚變化、潰瘍及壓迫治療資料 |
| 皮膚撕裂 | ISTAP | 依確認皮瓣狀態顯示分類 | 皮瓣與皮膚損失評估 |
| 其他／未判定 | 通用量測 | 呈現面積、組織、變化趨勢 | 成因與專科評估；不硬套總分 |

目前程式只提供上述評估需求提示，所有新量表狀態均為 `requirements_only`。沒有產生新總分，也沒有用未知值補零。現有歷史 PUSH 衍生數值仍需另加算法版本標示；分割面積不是標準長×寬，組織百分比也不能代替標準組織類別判定。

參照：[IWGDF 2023 分類指引](https://iwgdfguidelines.org/classification-2023/)、[IWGDF／IDSA 感染指引](https://www.idsociety.org/practice-guideline/diabetic-foot-infections/)、[NPIAP PUSH 工具](https://learn.npiap.com/products/push-tool-1)、[CEAP 2020 共識](https://pubmed.ncbi.nlm.nih.gov/32113854/)、[ISTAP 資源](https://www.skintears.org/resources)。各量表啟用前仍要完成定義、版本、使用許可與臨床覆核。

## 資料欄位與尚待完成的接線

新契約 `wound_profile` 只含 `schema_version`、`site`、`side`、`surface`、`digit`、`etiology`、`factors`。區域由 site 對照，顯示語言不改代碼；跨區的 additional_sites 與黏膜專用分類仍待臨床覆核後擴充版本，不硬塞入既有代碼。

後續必須完成才可啟用表單：

1. SQLite／Room 新增可空欄位／評估表與無破壞遷移。舊自由文字原樣保留，不用關鍵字自動配診斷。
2. 個案 profile 更正留版本；每次量測固定當時 profile，送件、outbox、收據與兩張輸出使用同一快照。
3. 後台能力協商與帳號權限；舊服務不支援時清楚提示，不能只靠 HTTP 200。
4. 特殊備註長度限制與「其他」必填；一般流程不要求文字。前後台細節頁、搜尋及管理介面顯示對齊。
5. 按量表建立獨立 assessment（含 scope、輸入來源、缺項、算法版本與確認者），通過界值測試後逐一啟用。
6. 兩平台合成實機案例、升級舊資料、修訂、撤回、後台讀回、研究匯出及本地輸出驗收，再更新商店。

本階段的資料契約測試不能替代上述完整流程驗收。

## 本機驗證結果

- 工程測試 112／112 個檔案通過；tools 388 項中 387 通過、1 項跳過。
- Android MMH validation／internal 各 136 項通過，包含新增欄位及收據拒絕案例。統計限本次執行的 XML，排除建置目錄殘留報表。
- iOS institution 模擬器 25 項通過：22 項既有研究待傳測試及 3 項新契約測試；其中金標測試包含跨平台共用 83 個輸入案例。
- iOS build-for-testing、Android 兩種測試建置、SwiftUI 型別檢查通過；parity 零未宣告差異、logic 50／50、Mac ownership 通過。
- 第一次 iOS 未簽署測試遇到模擬器 Keychain 權限錯誤；改以 ad-hoc 簽署的相同測試目標重跑後全部通過。此結果不是實機驗收。

原始紀錄存於本機 `woundai-lite-readiness-20261016/isolation-recheck-20261016/`，包括 `medical-test-summary.json`、`medical-engineering-full/python-summary.json`、`medical-ios-signed-tests.log` 與本次 Android XML 副本。Windows 與遠端 CI 仍須依提交後固定 SHA 另行核對。
