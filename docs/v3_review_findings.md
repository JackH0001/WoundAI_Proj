# v3 程式碼審查發現與處置（2026-08-03）

> 範圍：「時間軸補送標註不必重測」這批變更（DB v3、`LocalImageStore`、`MeasurementReviewScreen`、
> 時間軸強化、保存期限清理）。編譯已通過，本次審查專找**編譯得過但跑起來會錯、或臨床上有害**的問題。

## 0. 驗證方式

| 項目 | 方法 | 結果 |
|---|---|---|
| v1→v2→v3 遷移 | 以真實 SQL 重建 v1、灌 3 筆資料、依序套用兩支 Migration，逐欄比對最終 schema | 26 欄全符、資料零損失、6 索引齊全、`wdCode` UNIQUE 擋得住重複 |
| 補送標註不需重傳影像 | `engineering/phase2/test_resubmit_from_timeline.py`（Flask test_client，16 項斷言） | 全通過 |
| 實機 | Pixel 7 模擬器覆蓋安裝，**既有 v2 紀錄（WD-F9DDE995 / 6.11 cm²）完整保留** | 遷移在真實裝置上成立 |
| 程式碼審查 | 逐檔閱讀 9 個檔案，查證 8 條高風險假設 | 見下 |

## 1. 已修（會產生錯誤資料或崩潰）

### M1 影像與輪廓可能落在不同座標空間 → 面積靜默高估約 3.8 倍 ⚠ 最嚴重

`MeasureViewModel.bindImage` 以**原圖**雜湊判斷「是不是同一張照片」，所以同一張照片先走端上
（畫布＝相機原圖，可能 4000px）、再切後端（畫布＝work ≤2048px）時，`lastSavedId` 會被保留而走
update 分支。舊的 update 分支「已有檔就沿用舊檔、刪掉剛存的」，於是：

- 檔案 ＝ 4000px 相機原圖
- `gtPolygon` / `imageW,imageH` / `mmPerPx` ＝ 2048 work 空間

回頭修邊時輪廓會縮在左上角——**醫師只會覺得「AI 框錯了」，不會想到是座標空間**；重算面積是拿
work 空間的 mmPerPx 去乘原圖像素數，高估 (4000/2048)² ≈ 3.8 倍，且**靜默**寫進 `estimatedArea`
汙染癒合趨勢。這類錯誤最危險的地方在於它看起來完全正常。

**處置**：update 分支改為**一律以本次畫布重存影像**，寫入 DB 成功後才刪舊檔（順序反過來會留下死路徑）。
代價是每次重存多幾百 KB 密文，換掉整類錯誤。
另在 `MeasurementReviewScreen` 加**第二道防線**：載入後比對點陣圖尺寸與 `imageW×imageH`，
不符就停用「重新修邊」並說明原因——既有已受汙染的紀錄也擋得住。

### M2/M3 重新修邊後，紀錄自相矛盾

原本只更新 `gtPolygon` / `estimatedArea` / `correctionIou` / `annotationSubmitted`。
但 PUSH 與組織比例只存在 `notes` 字串裡，時間軸卡片會出現「**新面積 + 舊 PUSH + 舊組織%**」並列；
`hasWound` 也沒重算（AI 空手、醫師從零畫出傷口時仍是 false）。

**處置**：面積子分是純面積的函式，用 `WoundPipeline.areaSubscore` 正確重算；
組織比例 v3 沒有落地（未存 `tissueFrac`）**重算不了就明講它是修邊前的值**，不假裝仍然成立。
`hasWound` 一併更新。修邊會在 notes 追加一行 `⟳ MM/dd HH:mm 醫師重新修邊：…`。

### M4 `correctionIou` 語意在二次修邊後悄悄改變

它的定義是「與 **AI 原始遮罩**的 IoU」，是評估模型修正幅度的指標，並會送進飛輪。
但從時間軸進來修邊時，起點是**已經修過的 GT**，覆寫上去會讓這個指標系統性趨近 1.0
——看起來像「模型幾乎不用修」，是失真的自我評分。

**處置**：`correctionIou` **不覆寫**；本次相對前版的 IoU 改記在 notes，保留追溯性但不污染指標。

## 2. 已修（防護層缺失／體驗）

| # | 問題 | 處置 |
|---|---|---|
| S1 | 補送標註把 `consent_train` **硬編碼成 true**，真值只用在按鈕 enabled；`trainOk` 進畫面讀一次後不再更新 | 抽出頂層 `readTrainConsent()`，進畫面與**按下當下**各讀一次，送出的是真值。硬編碼 true 正是 `BackendClient` 註解裡記載過的法規級缺陷，不能在新畫面重蹈 |
| S2 | `purgeExpiredImages` 先刪檔再清 DB，中途失敗會留下「路徑非空但檔案不存在」 | 改成先清 DB 再刪檔。最壞只留孤兒密文檔，不留死路徑 |
| S3 | 時間軸縮圖無法區分「還在解」與「解不開」，App 重裝後整排停在「…」 | 分成 `無影像 / … / 無法解密 / 已清除` 四種狀態 |
| S4 | `File.exists()` 在 composition 期間跑主執行緒 syscall；`items()` 缺 `key` | 併入 IO 協程；補 `key = { it.id }` |
| S6 | `markAnnotationSubmitted` 失敗仍顯示已送出 → 重開又變可補送 | 檢查結果，失敗時明說本機狀態未更新 |
| S10 | `PatientEntity.mrnHash` 註解寫「SHA-256 加鹽」，實作是 **Keystore HMAC** | 修正註解並寫明為何這個區別是實質的（病歷號熵低、鹽是公開的，HMAC 金鑰不可匯出才擋得住離線爆破） |
| S11 | `LocalImageStore` 註解宣稱「一律存 work 影像」與「filesDir 未被 backup 排除」，兩句都與實作／設定不符 | 改為正確敘述：關鍵是**與 gtPolygon 同座標空間**；備份規則已排除 file domain，加密防的是 root 與實體取得 |
| 後端 | 撤回同意後補送，因影像已隔離而先撞上「查無影像」，`audit.jsonl` 記到**錯的拒絕理由**（IRB 稽核看不出是撤回） | `api_flywheel.py` 把同意檢查移到檔案存在檢查**之前**；已加測試鎖住 |

## 3. 未修（列為下一輪，附理由）

| # | 問題 | 為何先不修 |
|---|---|---|
| M5 | 修邊頁 undo 堆疊在遮罩擴張到 2200×2200 上限時，8 筆快照 ≈ 77 MB，加上 overlay 與全尺寸點陣圖峰值 >120 MB，低階機可能 OOM | 需要改 undo 的資料表示（差分或 RLE），不是一行修得完。觸發條件是「最後一次擴張後再畫滿 8 筆」，大面積傷口收尾時最可能。**先在 SOP 註明大面積傷口分次修邊**，程式面下一輪處理 |
| S5 | 每個畫面各自 new `OkHttpClient`；每次開 review 都用**明文硬編碼的 admin 帳密**自動登入 | 帳密硬編碼是既有問題（`MeasureValidationEntry` 也有），要一起改成後端認證，屬另一個 Sprint |
| — | 後端位址硬編碼 `10.0.2.2:5000`（模擬器專用），實機上補送標註會顯示「後端未連線」 | 與量測路徑同一個限制，正解是把後端位址搬進「設定」畫面（目前標示開發中） |
| S7 | 修邊完成時 `boundary.size < 3` 靜默不動作；例外時靜默 `onCancel()` 丟掉醫師筆畫 | 一行可修但需同步調整 UI 訊息位置，併入下一輪 |
| S8 | 個案模式排序是舊→新，全域模式是新→舊，註解寫反 | 只影響顯示順序一致性，`prevArea` 的計算是對的 |
| S9 | `wound_images/` 沒有孤兒檔 GC；`purgeExpiredImages` 對 `caseId=null` 或永不結案的個案不會清 | 需要先定義「個案結案」的臨床流程（P3 已列）。**注意**：撤回②訓練同意時**不刪**本機影像是正確的——那是照護紀錄，撤回的是訓練用途 |

## 4. 查證為安全（不必動）

- **座標空間，純後端路徑**：`work` → `bindImage(canvas = work)` → `saveToTimeline` 存的就是它；
  後端 `image_w/h` 也是上傳影像的尺寸，JPEG 重壓不改變像素尺寸。只走後端就一致。
- **不存在「DB 有 imagePath 但從未寫檔」**：影像只在 `saveToTimeline` 寫入（全專案唯一寫入點）。
- **保存期限不會誤刪他人在用的影像**：每次 `save` 都是新的 UUID 檔名，一個檔只被一列引用。
- **金鑰失效不會崩潰**：`PhiCrypto.decrypt/decryptBytes` 與 `LocalImageStore` 三層都 catch，
  降級到「無影像」訊息並停用修邊按鈕。生物辨識變更**不會**使金鑰失效
  （`setUserAuthenticationRequired(false)`）；App 重裝／還原備份才會。
- **面積計算量綱正確**：`cm2PerPx = mmPerPx²/100 / mScale²`，mm²→cm² 與降採樣都處理了。
  前提是圖與 polygon 同空間——這正是 M1 打破的前提，也是為什麼 M1 必須修。
- **錯誤座標的 polygon 不會污染雲端訓練集**：後端會驗證 polygon 落在 `image_w/h` 範圍內。
  所以 M1 送雲端時是**響亮失敗**；真正的危害在本機面積與趨勢的**靜默錯誤**。
- **BackHandler 疊層正確**：修邊中按返回會先退出修邊而非退出畫面。

## 5. 回歸測試

```bash
python engineering/phase2/test_resubmit_from_timeline.py   # 16 項，含撤回/重簽同意閘門
python engineering/phase2/test_flywheel_datachain.py
python engineering/phase2/test_backend_http.py             # 需後端已啟動
```

**仍缺**：Room migration 的自動化測試。目前靠「裝上去沒崩 + SQL 模擬」驗證，
正解是開 `exportSchema = true` + `room.schemaLocation`，用 `MigrationTestHelper` 測
「v2 有資料 → v3 資料完整」。需新增 `androidTestImplementation("androidx.room:room-testing:2.6.1")`。
