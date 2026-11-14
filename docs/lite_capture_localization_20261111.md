# WoundAILite 量測、保存與精確度說明中英文 — 2026-11-11

## 範圍與修正前後

先前紀錄頁已可中英文，量測、重新拍攝、保存確認及精確度詳細頁仍以繁中為主。
本輪加入126組成對字串與唯讀 `LiteCaptureCopy`；已有紀錄名稱不翻譯，新增傷口的預設標籤
只在 target ID 為空時翻譯。原位置代碼、照片、深度、數值、收據、額度與重送狀態均保留。

`LiteMeasure.swift` 在量測頁標記前的37,315 bytes（ViewModel與品質門檻）與parent完全一致。
本輪只把View顯示接上翻譯，沒有改變拍攝、原始JPEG保存、估算、存檔或服務辨識流程。
完整固定訊息才翻譯；未知錯誤／使用者文字原樣顯示，不猜成成功。服務逾時、拒收、額度耗盡
與尚待核對仍分開；翻譯不授權自動重送或建立新operation。

面積結果卡原英文長標題會截斷，改為垂直換行，保留數值與cm²。精確度頁保留全部限制，
包含兩位小數不代表0.01 cm²精度、缺值不以零代替、完整深度不代表小起伏已量準。
沒有更改面積計算或把工程測試寫成臨床精度通過。

## 測試與證據

- 一般英文/US與本人候選繁中/TW Release各**621通過、0失敗、1項既有模擬器檔案保護略過**。
  新增8項已包含在621內，兩套測試重疊，不相加。
- 新增回歸：126組字串與格式參數；真實使用者名稱與部位；沒有深度仍保留照片且不能保存／清空；
  方向不符保持未綁定；4種品質阻擋不變；未知錯誤保留；10種服務狀態及期限／operation不變；
  中英文結果卡實際渲染。
- 獨立模擬器UI兩項：量測按鈕、精確度說明與最後用途段可讀；6張畫面與2張結果卡已檢視。
- UI前後另核對相同索引與3張合成JPEG雜湊，沒有研究綁定或雲端同步資料；研究選擇仍為unset，
  沒有啟用研究。未操作本人TestFlight App或實機資料。
- 正式View／資源67檔與QA副本逐位元相同；只有QA入口改為測試展示host及另加合成資料測試。
  模擬器沒有LiDAR，正式硬體門檻保持原樣；不將展示host的UI結果當成真實相機／深度驗收。

## 失敗紀錄與修正

1. 首次8項測試中有1項失敗（10個斷言）：10種服務狀態漏譯。補齊對應字串，完整套件後通過。
2. 初次結果卡英文標題截斷，實際渲染後補換行並重新跑兩種完整Release。
3. 合成fixture設定false後，下次測試host讀到unset；當時尚未開始UI，索引／照片雜湊已通過。
   保留這次失敗，不把它寫成實機同意保存已修好。後續記錄實際unset作為唯讀前後基準，
   未再改同意設定；最終仍unset。此fixture現象不證明正式App存在或不存在同意持久化問題。
4. 正式root因模擬器沒有LiDAR而不顯示拍攝按鈕，測試正確被擋；之後使用獨立展示host直接呈現
   原View，不修改正式硬體判定。
5. 英文UI超過128字的XCTest identifier查詢失敗，改為完整label predicate；產品文案未縮短。
   再補捲動，讓最後限制段顯示在浮動頁籤上方。各次失敗日誌／摘要保留。

## 尚待完成

相機控制／手動修邊工具列、操作指南及其他未納入畫面尚待多語化。真實LiDAR拍攝、Apple登入／
購買／恢復、正式服務與研究分流端到端另案驗收。未部署、合併或更新Apple；原資料與稽核保留。
本輪後續本機提交不屬於parent 3fdc0f2的CI，推送需要新的固定來源授權。

## 覆核入口

- `iOS/WoundLite/LiteCaptureCopy.swift`、`Resources/*/LiteCapture.strings`
- `iOS/WoundLite/LiteMeasure.swift`、`LiteMeasurementInfoView.swift`
- `iOS/WoundLiteTests/LiteCaptureCopyTests.swift`
- `iOS/WoundLiteNativeUITests/NativeEntryUITests.swift`：僅LITE_QA_PHOTO_FIXTURE的展示驗收
- 本機：`woundailite-capture-localization-20261111/verification-report.html`及Claude覆核包。
