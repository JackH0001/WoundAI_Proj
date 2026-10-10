# MMH CreateService 請求格式修正

2026-10-09。部署前 264/264 有效 IAM 及第二輪 metadata／資源政策查核均通過，
但實際 Cloud Run CreateService 回 HTTP 400。稽核日誌指出：
`service.name must be empty on CreateServiceRequest`。隨後唯讀 describe 確認服務不存在。
本次失敗不能歸因於憑證，也不能把先前容器測試成功當成部署成功。

## 修正與邊界

`tools/create_mmh_private_service.py` 不再於建立 body 傳送頂層 `name`，
服務 ID 仍由固定的 `?serviceId=woundai-backend-mmhps20261007` 指定。
回讀檢查仍要求 API 回傳完整正確的 `name`；未放寬計畫、IAM、私有入口、映像、
同名服務拒絕、未確定結果不自動重送或新 journal 要求。

回歸測試的假 API 會模擬真實 API 拒絕有 name 的建立 body，readiness fixture
明確補上伺服器回傳的 name，另驗缺失／空白／另一服務名稱皆拒絕。

後端來源仍為公開 main `7ed425d455e3e8526eadd230c226f327df3a82fc`，映像 digest
`sha256:814d266e84f4a50b36363923e8a262aa97f4eaa18e6a5588ac27331231ab996b`。
本修正只涉及部署端工具與測試，沒有修改或重標記既有後端映像。

## 證據

- MMH 相關部署工具測試：115/115 通過。
- 把 name 加回的變異：1/1 被 assertion 捕獲，沒有 import／環境錯誤。
- 新 payload 與失敗請求逐欄比較：僅移除頂層 name，其餘內容完全相同。
- 對真 API 使用 `validateOnly=true`：HTTP 200。此模式不持久保存請求或建立資源，
  **不是**服務已建立／就緒／GCS 驗收的證據。
- 後续實際部署必須使用新 journal，重新跑完整即時閘門，不能重用前次部分綠燈。

可重現離線測試：

```sh
python -B -m unittest discover -s tools -p 'test_*mmh*.py'
```

官方 API 定义：[CreateService：serviceId 與 validateOnly](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/create)。
本機證據位於 `woundai-institution-evidence-20261007/mmh-unlocked-main-20261008/` 的
`create-format-regression.log`、`create-format-mutation.json`、`create-format-validate-only.json`，
以及保留的 `deployment-retry-20261009-1139/` 失敗 journal。

## 後續：長時間 IAM 查核的憑證更新

修正格式後的新 journal 在 67 項已通過時，下一個 Troubleshooter 查詢回 401，
`creation_attempted=false`。原迴圈全程持有最初的權杖；重新向既有 gcloud 登入
取得權杖後，同一項唯讀權限查詢成功，不需要使用者重新登入。

新增 `effective_response()`：只有唯讀 IAM HTTP 401 才重新取得一次呼叫者權杖，
並以相同 principal／resource／permission 重查一次；後續項目沿用更新後權杖。
再次 401、更新失敗、403／429／500 都停止，不重試 Cloud Run create，也不把
UNKNOWN 或錯誤回應轉成允許。憑證始終只在記憶體，不存入報告。

- 增量後部署工具測試 **121/121** 通過，涵蓋以上拒絕條件及下一項確實使用新權杖。
- 真 API 唯讀探針先送一個刻意無效的非密文占位 token，驗證 401 後自動取得
  現有登入的 token 並完成相同權限查詢：通過。没有建立服務、授予 IAM 或存取密文值。
- 失敗 journal `deployment-create-formatfix-20261009/` 保留；再部署仍須新 journal
  重跑全部閘門，不能把 67 項部分成功當成完整證據。

## 線上就緒讀回：HTTP/1 預設欄位

264/264 即時 IAM 及第二次 metadata／政策查核通過後，CreateService 成功，
Cloud Run operation done=true、無 error，revision 已 Ready。覆核工具卻拒絕
`container port mismatch`：API 會在原本省略 name 的 8080 port 自動補上
`name: http1`。這是工具對伺服器正規化欄位的假陽性，不是容器啟動失敗。

現在 payload 明確指定 `http1:8080`，readiness 仍逐欄完全比對，不移除協定
或埠號檢查。fixture 的真實 API port 格式改為獨立常數，避免從 payload
複製同一個錯誤；缺協定、h2c、錯誤埠及多個埠皆拒絕。測試 **122/122**。
此次只對既有服務重新執行唯讀 status；沒有重新 create、PATCH、切流或變更 IAM。
實際業務驗收仍須另看合成 JPEG／組織圖層／深度的 GCS 讀回與撤回結果，
不能從 Ready 推定全部功能通過。
