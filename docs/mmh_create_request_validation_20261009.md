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
