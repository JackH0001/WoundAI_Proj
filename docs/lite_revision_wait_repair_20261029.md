# Lite 研究修訂舊等待期限修復（ba970b1-L2）

## 原因與範圍

Claude原始`review_codex_consent_rollout_ba970b1_20261022.md`§3的L2指向
`LiteCloudSync.capacityNotBefore`，不是研究撤回的`LiteWithdrawalModel`。
舊組建可能保存`distantFuture`；讀取後原樣沿用，修訂一直顯示等待，沒有重試
按鈕。前面的撤回期限測試不能證明此路徑已修復。

本輪僅修改Lite本機紀錄讀取與研究修訂排程。未修改後端、研究同意、雲端
額度政策、照片辨識或量測算法；不部署、不清理真實資料、不更新商店。

## 修正行為

- 紀錄成功解碼且ID核對後，將超過讀取時刻一小時的容量期限縮短到一小時。
  全部需修復的紀錄一起原子保存；原本更短、已到期或缺少的期限不變。
- 保存`capacityWaitRepairs`本機排程稽核：原期限、原自動重試時間、調整時間、
  新期限。此資料不進研究payload；原`payloadJSON`逐位元組、digest、版次、
  綁定身分／服務／研究批次、面積及影像／深度附件不變。
- 有既存pending修訂才修復其自動排程，不為已確認或未綁定紀錄建立待傳。
  再次開啟不移動已保存期限，期限到達後恢復原有重試／自動續傳流程。
- 原子寫入失敗時保留已讀原紀錄、顯示修復尚未保存並禁止同步／改寫。
  解鎖或釋放空間後必須成功重新讀取與保存，才恢復操作。
- 到期仍檢查研究同意、原身分及未撤回的研究批次。成功回執後不新增下一版；
  回執遺失只重送同一修訂的原bytes，不重傳照片／深度或重新辨識。
- 自動續傳依既有App前景／續傳呼叫運作；不是iOS背景定時送達保證。

## 驗證

在父版本`8a95f7b`只加入同一條回歸測試，
`testLegacyExcessiveRevisionWaitIsBoundedOnLoad`會失敗，原因是期限仍為
`distantFuture`。修正後同一條測試通過。原報告沒有提供L2可執行Swift探針，
因此這是依原描述建立的原生測試，不宣稱執行了不存在的原探針。

新增9個XCTest涵蓋：載入後縮短並存回、重開不滑動與到期單次同步、更短／
到期／空值／剛好上限不改寫、磁碟寫入失敗及恢復、同意／身分／撤回閘、
舊pending缺自動時間、已確認／未綁定不建工作、多筆一次原子寫入且附件bytes
不變、回執遺失沿用同一payload與版次。合成sender只接收修訂JSON；沒有真實
雲端請求。對照與完整Release的原始xcresult、summary、tree另附覆核包。

```sh
cd iOS
xcodegen generate --spec project.institution.yml
xcodebuild test -project WoundMeasurementApp.xcodeproj -scheme WoundLite \
  -configuration Release -destination 'platform=iOS Simulator,id=YOUR_SIMULATOR_ID' \
  -only-testing:WoundLiteTests/LiteRevisionSyncTests \
  -only-testing:WoundLiteTests/LiteStoreLifecycleTests \
  CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- ENABLE_TESTABILITY=YES
```

沒有改動Xcode target或測試發現設定，現有ios-client PR workflow會包含新增案例。
獨立覆核重點是保存成功才放行、重開不延長、稽核不進研究payload，以及撤回
資料始終不被恢復傳送。L2本機修正不表示醫療N1/N2/N4/L1或完整刪帳已完成；
新版實機驗收及發布仍待後續授權與執行。
