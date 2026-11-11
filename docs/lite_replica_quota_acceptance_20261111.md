# Lite 雙程序用量與重播驗收 — 2026-11-11

## 結果與新增覆蓋

本輪以兩個獨立 Gunicorn master／worker、真實 loopback HTTP、簽章驗證、
正式狀態機與固定 ONNX 模型驗收；兩個程序共用合成 SQLite 狀態庫。
先前單一程序或 Flask test client 測試不能直接證明此跨程序情境。

| 情境 | 實際結果 |
| --- | --- |
| 程序 A 已取得執行權、暫停於模型入口；B 重送同一操作 | B 回 202，未再執行模型 |
| A 仍在等待；B 執行另一筆操作 | 第二筆成功，兩個 worker 均實際執行過 ONNX |
| 免費成功兩次後第三次 prepare | 429，reason=`success_quota`，用量仍為 2 |
| 未採用第一筆原回應，由另一程序查詢／重送 | 回執完全相同，模型次數維持 2；不是 TCP 丟包試驗 |
| 確認合成 Plus 購買，再由另一程序重播同一確認 | 原回執相同、未再查 Apple double，已用 2 次保留 |
| Plus 再成功八次，第十一筆 prepare | 當日成功／attempts 各 10；第十一筆 429；不是 2+10 |
| 停止一個 Gunicorn，再以相同狀態庫啟動新 master／worker | 十筆 status 加十筆 run 重播均相同；模型、額度及 Apple 查詢均未增加 |
| 研究與原圖 | 研究狀態未變，十筆皆 `research_stored=false`；合成 JPEG SHA-256 不變 |

這是一個整合驗收、四組情境，**不是新增十項單元測試**。十筆是使用者十個不同操作 ID，
共用同一張可重現的合成影像；同一操作的重試另外計算。使用合成信任根與版本 33，
不改正式 App Attest 允許版本，也不能替代 TestFlight 44／真實 Apple 驗收。

總共 10 次模型呼叫、10 次成功扣額度、10 次 attempts；Apple 狀態 double 共查詢 10 次：
一次購買確認、八筆新 Plus 辨識、一次被額度拒絕的 Plus prepare。
同一購買確認重播，以及工作完成後／重啟後讀回均不增加查詢。
沒有把這個小樣本換算成 p95、服務容量或正式營運費用。

## 來源、隔離與重跑

- OCI 原來源仍是 `a175a1549fce89c9910a890eaa4b8c3f33c056fb`。
- 映像 digest：`sha256:53eed5b05162d751c3311f77f863077eb57e5742ba65a98ee439c79c28a14195`。
- 本輪開始的工作樹 HEAD：`3c60528076b5030355d81618765906ca92ab1d2f`。
- 72 個封裝程式／設定檔逐檔比對目前來源一致；三個模型及全部 75 檔在容器內驗證。
  沒有把舊映像重新標成新的 commit。
- 固定模型在 `/app` 唯讀映像內，測試只注入 Apple／OAuth／KMS／GCS transport 與時間。
  正式模型、授權、額度、CAS、簽章與回執程式未替換。
- 容器 `--net none`，只存在 loopback；UID 1000、read-only root、cap-drop ALL、
  no-new-privileges；QA 唯讀掛載，合成暫存位於 tmpfs。VM 無 host mounts，完成後已停止。

工具 `tools/probe_lite_native_replicas.py` 使用原有 `tools/probe_lite_native_image.py`
的 Gunicorn 與 HTTP client；須在已驗證的 Linux/amd64 native 映像內執行。
`PYTHONPATH=/app:/qa:/qa/tests`；`QA_SOURCE` 必須等於映像來源。
QA 帶入 phase2 fixture 的遞迴依賴，但**不把正式 Backend 模組從 QA 掛入**。
Claude 包含本次 runner、來源對照、完整 JSON 回執與獨立驗證器，無模型權重、憑證或研究影像。

## 額度提示核對

原 HTTP after-request fallback 會在 429 加入泛用「服務忙碌」message。
實際 wire 同時含 `error=lite_usage_limit`、`reason=success_quota`。
已核對 iOS `LiteServiceOperation` 使用 error code 設為 `note=quota`，顯示額度提示，
不直接顯示這段 fallback；中英文 LiteCapture 資源均有對應文案。
既有 `testResubmitRechecksQuotaWithoutNewPhotoOrMoreOperations` 已覆蓋此分流。
這次未改產品訊息或重跑未變動的完整 iOS 套件。

## 證據與限制

本機證據根目錄：
`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundailite-replica-quota-20261111/`

重點檔案：`runtime-probe.json`、`runtime-probe.log`、`source-parity.json`、
`qa-manifest.json`、`runtime-vm-final.json`、`verify_evidence.py`。

此驗收尚不能替代：GCS generation／實際跨實例延遲、Cloud Run 擴縮與壓力／成本、
不同帳號混用、真實 Apple Sandbox 購買／恢復、實機覆蓋更新，以及模型臨床準確度。
同一帳號額度的程序隔離通過，不等於上市全數通過。
本輪沒有部署、雲端／IAM 操作、商店更新或原始研究資料清理。
