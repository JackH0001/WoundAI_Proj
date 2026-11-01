# Claude 覆核：Lite 服務與研究分流、原日回執與重試

本輪沿用 `b4e1a6840891a239139b5bb94487a7eb469188ca` 的額度／Apple 驗證核心，
新增預設不啟用的 HTTP 協調流程。這是請覆核的證據，並非 Claude 已覆核的結論。
精確 commit、patch 與 SHA-256 請見交接包 `SOURCE.txt` 和 `MANIFEST.sha256`。

## 實作與驗證

- `lite_service_operations.py`：可信帳號映射、固定操作 ID／原日、先預留後執行、
  持久化回執與遮罩結果。未取得唯一執行權就不能進模型；不確定結果不猜測重跑。
- `lite_service_http.py`：prepare/run/status，沿用真的 Flask App Attest 入口與
  request budget。呼叫者不能提供 paid、服務帳號、研究 epoch 或深度欄位。
- `lite_attest_http.py`：只有可信啟動程式明確注入 `commerce` 才註冊新端點。
  已驗證的服務路徑不取得研究 writer ticket；原研究撤回／重新同意流程保留。
- 8 個不同操作並行僅 2 個取得免費額度；8 個相同操作只有一張票。
  此並行在 coordinator 層；HTTP 簽章／重播另測，不聲稱跨網路壓測。
- 遺失 mapping/reserve/result/settlement 回應、程序重啟、跨午夜、模型版本變更、
  無效影像、無法執行隱私檢查、缺失基準／結果皆有回歸測試。
- 已簽署研究撤回仍生效，後續獨立服務可繼續；已用量不歸零，原研究路徑拒絕。
- 官方 Apple verifier 配合合成憑證／狀態：總額度 10、退款保留用量，舊回執可讀。
  人員身分與真實購買、恢復綁定尚未完成，不能當作實際金流已驗收。

本輪 **539 項不重複測試通過**：新 39、既有 App/核心 349、後台與規劃 151。
先前試跑的 loopback 沙盒權限與跨日 fixture 起始時間錯誤已修正，失敗日誌保留。
新增另有可重跑的 signed Flask + 真 GCS adapter／假傳輸探針，不計入單元測試數。
手動 Linux CI 已補步驟，但沒有推送或觸發遠端 CI；無新原生 build/TestFlight。

## 成本與臨界條件

新工作 prepare+run：5 次條件寫入、37 次讀取；完成後 status：0 寫入、10 讀取、
0 次模型。測試 status JSON 為 96 bytes，無照片或深度；簽章/header 等另計。
原生端尚未切換：若它仍重送 run，仍會傳照片，不能聲稱已節省手機流量。

沿用前輪每月 337,500 次嘗試、67,500 次 status 重試、300,000 次日基準初始化
及敏感度費率，這部分服務狀態讀寫約 US$15.44／月。它包含前輪 US$8.04 的
額度子項，不能兩項相加；也已在舊試算的保守讀寫預留中。未含 App Attest、
Apple/OCSP、結果留存／清理、競爭、模型、廣告、稅、客服，因此不是總營運費。
廣告沒有實際收入證據，高用量／長期留存的損益問題仍未結案。

## 請優先覆核與仍不放行的範圍

1. `commerce=None` 預設端點不存在；owner/signature/budget 不能被新路徑繞過。
2. service subject 映射只能可信 create-only；它不是完成的服務登入或訂閱升級。
3. result 存放的是有界多邊形／雜湊。24 小時只是讀取期限，**不是物理刪除**；
   上線前須完成服務暫存揭露、清理、刪除競爭與防重播 tombstone，不能只刪 mapping。
4. 已 claim 但無結果的工作保留待核對，避免重跑，但尚無有界人工／自動核對流程。
   未執行的 reservation 與未取得額度的 provisional mapping 也需清理規格。
5. 舊 segment 五次預設／非原子計數未切換。新舊共存不能成為 5+2 次或雙重扣費；
   須凍結基準、整合 research 寫入／新服務結果、失敗封閉及原生 persistent ID。
6. 固定 50% 收案、研究上傳不是免費服務強制交換；本輪沒有啟用收案或改同意內容。
7. 輸出 `confidence` 沿用既有 mask occupancy 欄位語意，不是校準的模型準確率。

完整 API、錯誤與切換規格：`engineering/contracts/lite-service-operations-v1.md`。
保留原始資料／稽核；沒有雲端資源、IAM、部署、清理、實際付款或商店變更。
