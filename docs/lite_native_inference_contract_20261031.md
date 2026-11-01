# WoundAILite 候選推論隔離與 Student 輸出契約修正

基底：`1cf4ab6b0fbabf115ca71f2a1fa725d19e04a470`（本機原生後端組合）。
公開覆核基底為 `b1b99a91b5f7a401ca53abe7afac50c17e992484`。
保持 PR #36 draft；本輪沒有部署、改寫歷史結果或更新商店。

## 發現與修正

候選後端的 `RasterInference` 原先向 `api_lite` 匯入輪廓工具，連帶載入研究
API 與其儲存依賴。將既有的人臉檢查與輪廓轉換原樣移至
`Backend/Flask/lite_raster_tools.py`，舊 API 也改用相同工具。
人臉檢查的限制、模型雜湊、失敗拒絕與輪廓篩選門檻未放寬。

建立獨立 `LiteNativeInference` 後，以既有三個固定 ONNX 檔驗證時，發現
Student 輸出是 **logits（未轉換的分數），不是機率**。既有後端將它直接
縮放、裁切至 0–1，再以 0.4 切遮罩；缺少 sigmoid 轉換。

可直接核對的來源：

- `engineering/phase2/deploy_student.py`：ONNX 匯出為 `activation=None`，註明 logits。
- `engineering/phase2/convert_student_coreml.py`：CoreML wrapper 已內含 sigmoid。
- `Android/app/src/main/java/com/woundmeasurement/app/processing/OnnxSegmentationModule.kt`：
  ONNX 分數先 sigmoid，再將機率圖放大。
- 本機固定 ONNX 的白圖輸出範圍為 −11.620964 至 −4.662474，直接排除「已是 0–1 機率」的假設。

現在共用 `student_logits_probability`：只對 Student ONNX 使用一次 sigmoid，
**先轉機率，再縮放，再依既有 0.4 門檻取遮罩**。不依 min/max 猜測，因為
合法 logits 也可能全部落在 0–1。CoreML 不再轉一次；其他模型規則未改。
`preprocessing.json` 的 Student `output` 同步更正為 `logits_mask`。

在單一未插值像素上，舊分數門檻 0.4 等於機率約 0.598688；正確機率門檻
0.4 對應 logit 約 −0.405465。縮放與非線性轉換不可以交換順序，因此不能
只把門檻改成另一個數字代替修正。測試以獨立 `math.exp` 公式與插值反例驗證。

## 獨立候選模組

- 只接受明確指定的獨立包、三個固定模型雜湊及前處理檔雜湊；不搜尋環境、
  checkout 備援或其他模型。全部檔案核對完成後才建立推論 session。
- 載入實際讀取並重新核對雜湊的 bytes，拒絕符號連結、錯誤輸入形狀及型別。
- Student 輸入仍為 RGB、ImageNet normalization、NCHW、256×256；既有
  on-weak A/U 集成、輪廓門檻與路由判定沿用。
- 模型錯誤、NaN 或輸出形狀錯誤會回報無法辨識；不把錯誤當成成功的空遮罩，
  也不偷偷改走色彩分割。一般民眾沒有新增模擬圖模式。
- 模型版本雜湊包含三個模型、前處理、演算法及輸出契約，供辨識操作／重試
  核對。變更模型後，不可用原操作 ID 默默執行另一演算法。
- 舊 API 新結果另記 `student_output_contract`；缺欄位的歷史資料不補寫。
  這個欄位表示所設定的 Student 解讀契約，不能單獨證明舊 API 推論成功；
  舊 API 原有例外備援未在本輪重構。獨立模組則明確拒絕推論錯誤。
- 此模組尚未接入正式候選映像的設定載入器。呼叫者須明確將 `model.raster`
  與 `model.version` 傳給原生服務組合入口，不會因匯入本模組自動啟用。

## 驗證結果與範圍

新增 **25 項**，包含獨立公式、轉換順序、真實分數語義、錯誤模型／設定、
影像界線、前處理張量一致、路由／輪廓一致、研究 API 匯入隔離，以及新結果
保存契約但不修改舊資料。這些是程式契約／合成測試，不是臨床準確度驗收。

本機最終重跑：完整 Lite **1,699 項**、prelaunch **1,667 項／57 個命令**，
皆通過；兩套有重疊，不可相加。新增 25 項包含於上列。另核對既有打包前處理
10 項、門檻一致性 15 項、Lite 分割流程 62 項、app 接線 13 項與 runtime golden，
均通過；其中也有與主套件重疊的內容。這些不是新來源 GitHub CI 回執。

另以固定的真實 ONNX 模型，在六張程式生成影像比較：

| 合成影像 | 修正前前景像素 | 修正後前景像素 | 修正後原路徑 vs 獨立模組 |
|---|---:|---:|---|
| 全白 | 0 | 0 | 遮罩／輪廓／路由一致 |
| 全黑 | 0 | 0 | 一致 |
| 隨機正方形 | 19 | 19 | 一致，走 A/U |
| 隨機直式影像 | 281 | 449 | 一致，168 像素改變 |
| 紅色矩形 | 13 | 13 | 一致，走 A/U；沒有達到輪廓最小面積 |
| 灰階漸層 | 0 | 0 | 一致 |

前後比較基底為 `1cf4ab6`，不是將修正後程式假裝成舊版。原 RGB 與模型檔案
雜湊前後一致；沒有讀取病患影像。紅色矩形並未因此變成可靠傷口分割標靶。
六組結果只支持新模組與修正後既有路徑一致，**不能推論臨床精度提升幅度**。
它也不能解釋或修復 LiDAR 小階差／體積低估；本輪沒有改深度資料或面積幾何。

本機環境：Python 3.12、ONNX Runtime 1.29.0、OpenCV 5.0.0、NumPy 2.4.6。
目前尚未建 Linux 候選映像，不能把本機 runtime 當成候選鎖定相依的驗收。
Android 與 iOS 僅核對既有轉換程式，沒有宣稱跨裝置像素級相同或新增實機通過。

## 重跑

從完整 checkout 使用既有測試相依；不需要雲端權限：

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local python -m unittest -v test_lite_native_inference
```

真實模型 probe 另外需要已有的固定三個模型，以及 Git 中的比較基底 `1cf4ab6`：

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local python tools/lite_native_inference_probe.py --models /path/to/reviewed/models --output evidence/model-contract-parity.json
```

工具只複製模型至暫存目錄、生成合成 RGB；不下載模型、不匯入整個 app、不連
雲端。覆核包不含模型或研究影像；只有 delta 的覆核包不是完整可執行 checkout。
CI 已加入合成 session 契約測試；真實模型 probe 不在公開 CI 自動執行。

## 尚待完成

1. 原生候選的設定載入器、鎖定相依、獨立映像 profile 與啟動驗收。
   舊映像允許清單只補共用 raster 工具，沒有把商業後端依賴直接塞入舊映像。
2. 新模組預載三個模型，舊流程 A/U 為延遲載入；啟動記憶體／時間、Linux
   相依與成本尚需實測，不能援用這次合成 probe 的耗時作吞吐量保證。
3. 真實 Apple Sandbox、App Attest、候選 GCS/KMS 與手機驗收；完整刪帳及
   既有醫療覆核待辦仍未因此完成。
4. 如本修正進入候選部署，需用保留的已同意驗收資料另做分割品質比較，辨識
   來源與版本清楚分開，不重算既有人工修邊或改寫歷史回執。

## Claude 覆核重點

確認只對 Student ONNX 轉換一次、先轉換後插值、沒有用全域倍率修正深度；
確認真實模型 probe 同時保留舊基底與修正後兩路徑；核對三模型／設定版本，
舊打包隔離、失敗不產生成功結果，以及歷史資料不補寫。整體測試與新 CI 狀態
以本輪覆核包的原始 log／執行回執為準，不以先前 commit 的 CI 代替。
