# Claude 覆核：Lite 服務帳號訂閱綁定 — 2026-10-26

接續本機 `20a50cfa5224c0e0433b7fb863079835531aeb36`。本輪完成**既有、已驗證服務帳號的
訂閱關聯核心及合成驗證**，不是完整會員登入或正式訂閱啟用。
未推送、未部署、未更新 TestFlight／商店、未收費或投放廣告；沒有改原始研究資料及稽核。

## 改版差異

之前 `ServiceAccounts` 只有 create-only 安裝登記，免費登記完成後無法附加付費關聯。
現在可明確注入 `ServiceSubscriptionLinks`：由既有登記取得服務 subject 與 token，
透過既有 Apple authority 核對交易與帳號，再保存意圖、CAS 關聯及完成回執。
同帳號其他已驗證裝置讀取同一關聯；不覆寫原登記、不重建用量、不修改研究同意。

本輪收到／修復的測試發現：Python `True == 1` 會讓普通 dict 比對誤收 schema；
完成回執改以 type-sensitive canonical digest 核對，並以損壞資料測試固定。
初跑另有合成測試時鐘／HTTP 狀態預期不一致，已修正 fixture，最終完整回歸通過。

## 本輪實跑結果

| 項目 | 結果 |
| --- | --- |
| 新增綁定／恢復／故障／並行／簽署 HTTP 測試 | 30 通過 |
| 新增操作成本探針斷言 | 2 通過 |
| 相關後端與契約回歸 | **462 通過**，包含上述 32 項；既有 430 項未重複加總 |
| 免費用完 2 次後升級 | 原 used=2、attempts=2 保留；總額度 10，剩餘 8 |
| 兩裝置同帳號、12 個並行新請求 | 8 個預留、4 個額度拒絕；連同原用量不超過 10 |
| 用完 10 次後退款 | used=10 保留、limit=2、remaining=0，不清零 |
| 同一已完成關聯再次恢復 | 0 寫入，回原關聯回執；不代表仍為有效付費 |
| 研究撤回後再升級／辨識 | 撤回狀態完全相同，service 回覆 research_stored=false |

Apple 使用固定官方 Python library 3.1.2 與真正合成 EC 憑證／JWS 驗章；Server API
回覆為 fixture。SQLite 是真實持久化 CAS；有真正雙／多執行緒競爭、Flask 簽署請求，
完整回歸包含既有 localhost HTTP。GCS 使用實際 adapter＋fake transport。
沒有真實 Apple Sandbox、OCSP／網路延遲、GCS IAM、實機或醫療量測精確度驗收。
未修改 Swift，所以不把上一輪 iOS 建置／測試數字列為本輪成果。

## 核對與成本

故障注入涵蓋 intent、link、completion 寫入後遺失回覆；明確續接可讀回並完成，
quota 內容不變。損壞／缺少完成證據會停止，不能覆寫解鎖。兩筆不同交易競爭時只
保留一個關聯，另一方明確衝突，兩邊的驗證意圖均保留。未知裝置不會自動建立帳號。

以下是無競爭的合成 adapter 計數，含帳號／關聯／Apple proof，不含辨識與 quota：

| 呼叫 | 條件寫入嘗試 | metadata＋content 讀取 | 模擬 Apple status |
| --- | ---: | ---: | ---: |
| 尚無關聯的免費 grant | 0 | 3 | 0 |
| 首次關聯 | 5 | 17 | 1 |
| 讀取帳號並取得新的付費 grant | 2 | 12 | 1 |
| 已完成關聯恢復 | 0 | 8 | 0 |

新核對流程有額外成本，不能把「恢復 0 寫入」解讀成所有付費請求免費。
計數中的重複 create-only 嘗試未必產生新版本。真實 API 計費、延遲、重試、稽核版本、
廣告填充率及實際付費用量仍未量測；本輪不宣稱 US$1.99／月已能保證獲利。

## 請優先覆核

1. `attach` 僅可信控制端使用。`authentication_proof_sha256` **不是登入驗證**，只是
   日後真正驗證證據的索引；不得把它直接接受為公開 HTTP 的自選授權。
2. 歷史關聯回執不是付費 grant。新的 quota admission 仍需 authority 即時查詢；退款／
   到期不拆除關聯、不清空用量。已接納工作按原回執完成一次。
3. 不支援同帳號更換 original transaction、帳號轉移、家庭共享或未登記裝置恢復。
   不能為了通過 UI 就自動產生新 subject 或免費餘額。
4. 服務帳號、訂閱、研究身分三者沒有自動合併。撤回不會被訂閱升級重新同意。
5. 完成稽核缺失時正常讀取會阻擋，明確重試控制操作可復原；正式 UI／營運要有有界
   修復流程，不能讓使用者永久等待。此輪沒有新增公開修復端點。
6. 關聯一旦啟用，回復版本必須保留 reader／policy；移除注入會錯誤回到原免費登記。
   目前尚未部署，也沒有自動遷移或回復雲端。
7. CI 範本已加入測試與探針，僅本機語法及實跑核對，**未觸發遠端 CI**。

下一步仍需實作／決定真正服務帳號登入、裝置連結及服務端 token 交付，接原生購買／
恢復與 UI 回執流程，再完成舊額度切換、Apple Sandbox 及受限雲端驗收。
本輪不需使用者重新拍攝或操作手機。

契約：`engineering/contracts/lite-service-subscription-links-v1.md`。
測試：`engineering/phase2/test_lite_service_subscription_links.py`、
`engineering/phase2/test_lite_service_binding_probe.py`。
覆核包含精確 commit 來源、patch、完整日誌、探針及獨立重跑步驟，未代傳 Claude。
