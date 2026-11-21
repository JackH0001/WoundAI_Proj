# Lite 首次初始化：受限儲存接線與續接驗收

本輪是私有操作工具，接續 `598c7ad`。App／服務映像來源仍為已覆核的 `5315430`；沒有部署或變更既有資料。雲端 provider 的實際授權、清冊與簽署準備尚未完成，不能把下列合成結果當成正式雲端通過。

## 修正前後

先前初始化核心已有三段簽署操作及中斷續接，但還需要測試 fixture 才能組合實例。本輪 `tools/lite_fresh_bootstrap_factory.py` 可以由固定的公開設定、初始化計畫、獨立物理位置表與注入的 GCS client，組合既有正式 CAS／訂閱／額度／發布核心。初始化不需要 Apple 登入私鑰、App 內購買私鑰、KMS 解密或模型。

設定、計畫和位置表各有 SHA-256；先組合未連接儲存的正式核心、完成其原有驗證，再呼叫儲存 factory。錯誤的來源、映像、計畫、fence、資料位置或重疊前綴不會觸發 bucket 讀取。`build()` 只組合工具，不初始化、不開流量。每一次推進仍須另傳有期限、綁定前態及階段的簽章。

另一處修正是 quota 暫停的恢復：若原先失敗回復也無法關閉準備狀態，新的已簽署 recovery 先完成關閉，再核對 pause hash 並恢復。失敗紀錄、原意圖及回執都保留；不刪除資料、不改 namespace、不重建額度。

## 資料權限與信任邊界

18 個操作位置分為：

| 類型 | 位置 | 工具允許操作 |
|---|---|---|
| 7 個唯讀 | accounts、links、links_audit、quota、apple_proofs、observer、bootstrap_observer | GET |
| 6 個只新增 | subscription_audit、rollout_audit、release_audit、bootstrap_audit、gate_audit、cutover | GET；generation=0 的 create-only |
| 5 個可變控制 | subscription、rollout、release、bootstrap、gate | GET；指定 generation 的 CAS |

bootstrap、bootstrap_audit、bootstrap_observer、gate、gate_audit 使用 `lite-bootstrap/<namespace>/...` 的獨立位置，且不能與任何 native 資料位置重疊。帳號、既有訂閱關聯及額度本身皆不可由此工具改寫。工具沒有 LIST、刪除、IAM、Secret、Apple 或 KMS 呼叫。

`RestrictedStore` 是程式內防護，**不是 GCS IAM**。注入的 client 及 factory 是受信任的部署元件；正式執行前仍須核對服務身分及繼承後的有效權限。可變 CAS 在 GCS 底層可能需要 delete 權限，必須只限明列可變前綴；稽核前綴不能給覆寫或刪除。

`GCSFactory` 先用 metadata GET 核對指定 project number、ASIA-EAST1、UBLA、PAP、沒有版本控制／軟刪除／retention policy，以及固定的 metageneration，再建立既有 `GCSStateStore`。這是該次執行的政策核對，沒有鎖住管理員之後的政策修改，也沒有證明 bucket 的歷史一直為空。

`ClosedPreparationProvider.new_enabled` 表示額度已準備好，**不是已開 Cloud Run 入口**。其可變準備紀錄與不可變意圖／完成稽核都持久保存；只有目前已取得 quota 執行權、尚未過期的簽署請求可改動。每次仍須獨立 observer 確認入口關閉、舊 writer 停止、無在途工作，且固定清冊與 fence 相符。工具本身不停止 writer，也不製造實際清冊證據。

## 本機驗收

- 30 項既有初始化測試及 24 項新接線測試：54 項通過；新測試沒有重複收集舊的 TestCase。
- 使用正式 GCS CAS adapter 搭配合成 transport，核對 metadata 失敗、錯 project／region／版本、CAS 競爭及無權覆寫；並非實際 GCS 寫入。
- 保存訂閱啟用結果後遺失回覆，再以新授權續接：不重複啟用。
- quota 準備完成後，active 寫入與失敗關閉均出錯：保留 paused 及原稽核，新授權先關閉後恢復。
- 用正式 native web 組合、合成 Apple／KMS、簽署 HTTP 請求完成一張合成影像，再重送及重啟服務：模型 1 次、額度 1 次、結果一致。
- 重啟後不重跑初始化，已完成的服務不依賴 bootstrap observer 持續更新。既有 fixture 紀錄與稽核未變。

既有依賴環境下（Python 3.11+）：

```text
python -m unittest -v test_lite_fresh_bootstrap test_lite_fresh_bootstrap_factory
python tools/lite_fresh_bootstrap_probe.py --factory --output new-factory-probe.json
```

PYTHONPATH 應包含 Backend/Flask、engineering/phase2、tools；Windows 以系統路徑分隔符組合。probe 拒絕覆蓋舊輸出，不讀實際研究資料或登入憑證；合成簽章不可作為部署值。

## 正式部署仍須補齊

1. 獨立清冊：同一權益所有舊儲存位置、全部 writer、目前及歷史資料；不得只看新 bucket 不存在。若非空，改走已核定的遷移流程。
2. 清冊操作需在 writer fence 下完整分頁；GCS `versions=true` 和 `softDeleted=true` 分開查，兩者不能同時使用。沒有 soft-delete policy 時請求可能回 400；不能把錯誤當成零筆。欄位為零也不證明更早已不可見資料的歷史不存在。[Google Objects: list](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/list)
3. 正式 operator／observer 憑證保管、獨立簽章及狀態觀察程序，與本輪無私鑰的組合工具分開。注入的身份及清冊來源不能由合成 fixture 代替。
4. GCS/IAM／Secret 掛載與封閉入口的真實驗收，完成後才允許本人 Sandbox 流量；本輪未執行。
5. 共用 KEK 的歷史副本退休、完整刪帳與真實 Apple 登入／購買恢復仍待完成。

本輪沒有雲端寫入、啟用 Apple Secret、部署、商店交付、刪除研究資料或銷毀金鑰。
