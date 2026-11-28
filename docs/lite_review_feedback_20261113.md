# Lite 獨立檢視入口：來源標示與失敗提示

2026-11-13。本輪修改 `Backend/LiteReview` 本機候選；未部署、開通人員權限、
讀取研究媒體或更新 Apple。原生 App 與個人／研究服務的寫入邏輯未改。

## 重現的問題與修正

舊詳情頁只列出修訂的面積與來源，未呈現 metadata 中的原始
`student_output_contract`。即使 App 已提醒雲端圈選版本未確認、範圍可能偏小，
後台也看不到這項限制。瀏覽器遇到快照過期、同意變更或權限不足時則顯示
JSON；兩服務之間還會把快照過期原因縮成通用 `review_reader_request_failed`。

| 情境 | 修正後 |
| --- | --- |
| 原始雲端 route 與 Student v1 contract 都相符 | 顯示保存的解讀版本，仍明示輪廓與面積精度未驗證 |
| 缺少、不明或超出長度的版本欄位 | 保持版本未確認；若沿用 AI 邊界計算，提醒範圍／面積可能偏小 |
| 後續手動修訂 | 原來源、版次與表面積／投影面積分開保留，不因原始 AI 版本改標或重算 |
| 僅 metadata 權限 | 可查看來源限制，仍不提供照片或任何多邊形 |
| 快照過期、權限、同意、來源或稽核出錯 | 保留 HTTP 狀態碼，文件導覽顯示原因及下一步，不自動刷新或重送 |
| reader、image 或 JSON 呼叫 | 維持 JSON 錯誤契約，不把 HTML 當 JPEG 或 API 回覆 |
| 深度缺檔或收據相符 | 顯示可讀說明；收據／版本相符仍不表示深度對位或精度通過 |

後台只展示已保存欄位，不獨立驗證模型訓練來源、簽章推論血緣、尺度或量測品質。
未知版本的提醒採條件式：手動畫出的修訂不應被說成必然低估。舊 reader 未回傳新增
欄位時，portal 也保持「版本未確認」。沒有用新版解讀去重寫歷史面積、遮罩或趨勢。

## 存取與錯誤邊界

兩服務只傳遞白名單中的固定錯誤代碼，必須與 HTTP 狀態相符，JSON 只能有
`error` 一個欄位。其他供應者 HTML、敏感字串、錯誤型別或額外欄位會變成通用
錯誤；錯誤 body 上限 1 KiB。沿用單次 GET、禁止 redirect，不因人類提示增加重試。

只有明確偏好 `text/html` 的 portal GET 導覽取得 HTML；JSON／未指定 Accept 的
既有探針保持不變。image／static 路徑不回傳 HTML 錯誤頁。驗證失敗不呼叫 reader；
檢視稽核未保存則不提供來源內容。CSP、no-store、escaped templates、兩層人員／
服務身分驗證與 generation／同意重核均保留。權限沒有擴大，HTTP 沒有新增寫入路由。

## 驗證證據

- 既有同一份合成資料：舊版沒有 segmentation provenance；瀏覽器快照過期為
  `503 application/json`。新版本為有原因的 `503 text/html`，保留相同拒絕行為。
- 13 項新回歸包含多組子案例；與既有 portal、人員入口、獨立封裝探針、限定執行器
  及方案測試共 **102 項通過**。13 已包含在 102 中，不加總。
- 新回歸中的前 12 項已對父版重跑，出現預期失敗；失敗與成功日誌分開保存。
- 真正 IAP ES256 驗證、兩層 Flask 路由及 BFF HTTP adapter 的合成稽核故障測試：
  只請求一次、狀態 503，畫面沒有面積、媒體或內部錯誤內容，來源資料逐位元不變。
- 本機瀏覽器核對四頁：未知圈選、Student v1、metadata-only、快照過期。
  這是獨立 loopback 合成預覽，未登入 Google IAP 或存取真實 GCS；預覽程序已停止。
- iOS、模型或上傳程式未改，沒有重跑完整 iOS／Lite 推論套件；新來源 CI 尚待授權推送。
  現有 CI 的 p0-4-audit 會執行此測試檔，lite-review 會建置獨立映像並驗證封閉入口。

可重跑的離線命令（依既有後端測試環境安裝套件）：

```sh
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B -m unittest -v \
  test_lite_review_portal test_lite_review_human test_probe_lite_review_container \
  test_plan_lite_review_human test_lite_review_executor
```

## 外測仍需的證據

既有兩個封閉檢視服務的負向驗收，不等於 owner IAP 登入與正向研究資料檢視已通過。
後續仍須受限部署方案、固定設定與來源清冊、真實有效 IAM／資料隔離、撤回競爭讀回。
本輪不更新既有部署授權或自動執行索引遷移／清理。

參照：[獨立入口契約](../engineering/contracts/lite-review-portal-v1.md)、
[A7 與 B1/B2 邊界](claude_lite_review_human_20261025.md)、
`iOS/WoundLite/LiteMeasurementContext.swift` 的 `LiteContourOrigin`，
以及 `Backend/Flask/lite_research_capture.py` 保存的原始 AI 來源欄位。
