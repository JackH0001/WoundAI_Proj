# MMH IAP 保留、收據與歷史修訂更新

2026-10-11：Android 合成量測已由現有候選服務入庫，但成功回覆缺少 annotation_receipt_id，App 正確拒絕確認完成。使用者授權補齊保留現有 IAP 的部署檢查、更新候選後端，再使用同筆紀錄重送驗收。

## 修改範圍

- 標註成功、相同重送與並行重送均回傳 code、image_id、annotation_receipt_id。缺保存證據不回成功。
- 每次修訂保存独立組織遮罩及深度資產，以內容雜湊、修訂收據與 image_id 明確關聯；原始深度索引必須綁定指定標註，不能猜最新版本。讀取舊共享遮罩時核對雜湊，已覆寫且不符者拒絕使用。
- 舊 2D 紀錄只有在原圖內容符合 image_id、遮罩雜湊有效、完整必要／溯源欄位與本次一致時才去重，回傳原收據且不新增／改写舊紀錄。不同分數、位置或遮罩是新修訂；方向／深度不明的歷史 RGB-D 不自動重建簽章。
- 新增 `tools/stage_mmh_iap_revision.py`，保留原先未啟用 IAP 的建立／更新工具，避免放寬它們的檢查。

## 部署邊界

固定 woundai-jackh001 / asia-east1 / woundai-backend-mmhps20261007 與既有 service UID。只 PATCH template.containers、template.revision、traffic；只將 candidate 標籤改指向新映像，原預設 100% 流量的 revision 保持不變。候選標籤是實際驗證入口，更新後會影響使用該入口的測試者。

更新前後比對：IAP 必須 true、invoker 驗證不能旁路、run.invoker 僅既有 IAP agent、IAP 入口僅既有 Jack 帳號、兩層 IAP 祖先政策必須空，專案與祖先 settings 不得另有配置；既有 Web audience、secret 雜湊和全部 OAuth 設定逐項保留，不能加入 programmatic client 或未知欄位。專案政策、各層 policy etag 及服務其他設定均綁定快照。只讀 secret 雜湊，不讀 secret 值。

沿用原 main 來源／映像 manifest／建置 provenance／桶與密文版本／有效 IAM 檢查；完成長時間 IAM 查詢後，再讀一次全部現場設定，若不同立即停止，不 PATCH。PATCH 前寫入獨占日誌，結果不明不自動重試。服務就緒後再核對所有保留項目及實際流量，未通過不宣稱部署成功。

回復方案只恢復更新前的 traffic 陣列及舊 candidate 標籤，使用當下 etag；不回復資料、不改 IAM/IAP。程式提供可覆核的 rollback request，不自動送出。

## 本機與唯讀驗證

- 新 IAP 更新測試 17/17：未知／新增主體、祖先授權、OAuth audience／client 漂移、未觀察到的流量、改寫 mask、過期快照、UNKNOWN IAM、PATCH 逾時、回復舊標籤與成功狀態撤銷。
- 修訂資產與舊 2D 去重 HTTP 回歸 19/19；沿用原更新 18/18、建立工具 29/29。
- 共 13 支測試腳本全數 exit 0，另涵蓋 depth endpoint/chain、預覽、存取範圍、撤回、組織資料集／匯出、飛輪資料鏈及儲存抽象。
- 真實環境 read-only configuration 檢查通過：現場 IAP/OAuth/IAM 與目前 runtime-plan 相符。沒有部署、改 IAM、鎖桶、删除資料或公開收案。
- 本次依使用者明確的後端修正／部署授權在 Mac 修改 Backend/、engineering/；不變更 owner_guard 所有權規則，不冒稱已完成 Windows 全套。

## 發布與驗收順序

1. 本 PR CI 通過後，取得綁定 SHA 的合併確認。先前合併授權指定其他 PR，不能移用到本 PR。
2. 從合併後遠端 main 的乾淨來源重建映像，驗證 context 與容器，再產生綁定 image digest、manifest、現場 etag 的更新提案；不以本機分支冒充 main。
3. 執行即時 metadata、有效 IAM 與 IAP 保留檢查，再更新 candidate，檢查就緒／匿名拒絕／Google health。
4. 使用平板原筆、原遮罩重送；預期 duplicate_skipped 加原收據，佇列筆數不增、RGB／遮罩位元不變、source=phantom、depth_source=none。由 App 確認收據後才標完成。
5. 尚未完成上述實際部署與平板重送前，不宣告端到端通過。

本機證據（repo 外）：`../woundai-institution-evidence-20261007/mmh-iap-update-20261011/`，含 boundary-before.json 與 tests/。不包含密碼、token 或研究影像。

## PR #23 首次 CI 與補修

已授權的 a407b8f 推送並建立 Draft PR #23；6 項 check 通過，audit-contract 在既有 `test_medical_depth_orientation_storage.py` 失敗。該測試仍讀舊 image_id 固定檔名並預期覆寫；新契約為內容雜湊路徑、保留歷史版本。

本機补修只更新此測試與本文件：由深度索引及回覆收據選取實際資產，核對 image_id、annotation receipt、payload hash；新增修訂後逐位元組核對所有先前資產仍存在且不變。舊索引反例補齊 actor 與排序資訊，確實進入 legacy_unverifiable 檢查。方向保存 7/7、修訂資產 19/19、後續 p0_4_staging 與 runtime_golden_contract 均通過。未推送此追加修正，尚不能宣稱遠端 CI 全綠。
