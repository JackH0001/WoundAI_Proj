# Android MMH 紀錄檢視修正與驗證（2026-10-11）

## 本輪結論

已在 Alldocube iPlay 60 mini Pro（Android 14）保留資料安裝 1.0-mmh-validation-r3（26）。JVM 35/35、實機 Compose 6/6 通過，APK 建置成功。沒有清除 App 資料，也沒有透過測試送出研究資料。

## 問題與修正

- 使用者截圖顯示累計仍為一次量測。程式 `dao.updateMeasurement` 更新同一列，將每次修邊說明追加於 notes；卡片原本逐字展開，導致一筆佔滿畫面。現在目前面積仍直接顯示，原始備註及修訂歷程預設收合，按「查看修訂紀錄」展開。歷程原文完整保留，未刪除或合併資料。
- 紀錄檢視页原本沒有影像預覽。現在重用 AnalysisPreview，显示本機影像、保存的輪廓、可讀取的組織柵格；不重跑分類器補造歷史組織圖層。不曾保存的校正框不顯示、不聲稱未偵測。
- 原圖與輪廓尺寸不符或缺少座標尺寸時，只顯示原圖與原因，停用修邊。此判準不能單靠比例驗證 RGB-D 配準，未宣告方向實機驗收。
- 結果與紀錄預覽可雙指 1–6 倍縮放及移動、重設；單指保留給頁面捲動。
- 修邊入口原本可在影像先完成、登入尚未回應時啟用；完成修邊可能讀到尚未載入的權限。現在等影像／權限載入結束才啟用。登入失敗先說明登入問題，沒有醫師權限仍可本機修邊但不產生醫師確認。
- 送件期間及權限載入期間禁止重複進入；醫師確認與有效同意要求持續保留。取消或移動畫面不等於醫師確認。

## 實測

| 範圍 | 本輪結果 |
|---|---|
| 同意閘門 JVM | 9/9 |
| 保存收據 JVM | 23/23 |
| 防重複送件 JVM | 3/3 |
| 平板登入載入／送件中禁止進入修邊 | 通過 |
| 平板修訂備註展開及收合 | 通過 |
| 平板歷史缺少組織／校正框不補造 | 通過 |
| 平板雙指縮放及重設 | 通過 |
| 平板上傳等待防重複、失敗可重試 | 通過 |
| 平板已保存標註禁止重送 | 通過 |
| APK 安裝、啟動及版本核對 | 通過 |

Compose 測試使用合成小圖及隔離的 UI 狀態，未讀寫使用者量測資料；不是原紀錄雲端上傳驗收。沒有執行 Windows 全套或 iOS 重建。

## 尚待驗收及建議

1. 使用者需確認原紀錄上的疊圖和收合顯示。若原紀錄缺少柵格，會顯示缺少提示；測試不代表補回歷史組織標註。
2. 本輪唯讀核對原合成紀錄的保存收據仍與 baseline 相同，但沒有找到新版候選之後完整的 duplicate intent/outcome；雲端同筆重送驗收仍未通過。等待使用者補送後再核對，不以 HTTP 成功取代保存收據。
3. 後續建議區分「內容修訂」與「無修改確認」。只有完整遮罩／組織／座標／面積／標註欄位相同時，才能保留既有保存狀態；不可只用顯示面積或四捨五入 IoU 判定。本輪只改顯示收合，尚未實作無變更修訂抑制，也未刪除舊 notes。
4. 雲端每次有效修訂仍須保留獨立版本並綁定同一影像／深度；前台收合不應刪除研究稽核沿革。

## 證據

- [測試與 APK 摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-review-r3/summary.json)
- [實機六項測試](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-review-r3/device-tests.log)
- [建置紀錄](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-review-r3/build.log)


## r3 使用者送件後核對（2026-10-11 台北 14:34）

使用者回報登入、進入原紀錄修邊退出後，App 確認標註上傳完成。本輪唯讀雲端核對結果：

- 06:33:52Z 有匹配影像與新收據的 annotation_enqueue_intent；06:33:58Z 保存組織遮罩，06:34:06Z annotation_revised，06:34:12Z annotation_enqueued=ok。
- 事前保存收據雜湊能唯一定位原紀錄。原紀錄 canonical hash、原影像與舊組織遮罩的內容／generation 完全不變。沒有覆寫歷史版本。
- 同影像現在有 2 個標註版本。新修訂明確 supersedes 原收據；新影像 SHA-256 與原物件相符，組織遮罩 SHA-256 與內容定址路徑及宣告相符。
- 兩版面積都為 16.200923999940628 cm²，輪廓相同、組織 PNG 位元組相同；depth_source=none，符合此 Android 平板沒有深度採集的預期，不能當作 RGB-D/3D 完整性驗收。
- 未發現 duplicate 意圖／結果，本次是成功建立新修訂，不是完全相同 payload 的去重成功。舊驗證腳本用面積篩選，因現在有兩個同面積版本而安全停止；改用事前收據雜湊定位，沒有猜測目標或改 baseline。

### 尚需修正的兩個 P2

1. 時間軸補送缺少原始模型與影像品質溯源。原版 quality 有 6 項欄位且 seg_model 有值；新版兩者缺少。MeasurementReviewScreen 呼叫 submitAnnotation 時 segModel=null、未傳 quality，careNote 改成固定 resubmit from timeline。這些欄位參與後端修訂簽章，足以產生新版本。應在首次量測持久保存 immutable provenance，補送沿用；既有資料只能由經驗證收據綁定的舊紀錄恢復，不猜填。
2. 載回修邊重新 seedAuto，再由 tissue 與 auto 差異重算修改像素數。實際 PNG 相同，但 tissue_edit_px 從 106 變 22、ratio 隨之改變。應保存原始分類基準或可驗證修改來源；無變更完成時沿用原狀態，不能把重新跑出的 baseline 當成醫師修改事實。

此輪只做驗證並記錄問題，尚未修改上述資料結構／修訂契約、未部署新後端、未更改任何雲端研究物件。r3 的本機 35/35 與實機 6/6 結果維持有效，但不涵蓋這兩個新發現。暫不要求使用者反覆重拍／修邊；保留此合成樣本作為修正回歸案例。

證據：

- [雲端修訂與原件比對](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/same-record-revisions-after-r3.json)
- [本輪雲端驗收摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-r3-cloud-acceptance-summary.json)
- [唯讀核對腳本](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/inspect_retry_revisions.py)
