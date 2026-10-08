# MMH 候選醫療映像：執行期驗證

2026-10-08 台北時間 11:28 完成。**固定 digest 的 Linux 容器與本機各通過 39 項合成流程檢查，三個 ONNX 模型均實際載入並完成有限數值推論。MMH 服務仍未部署。**

## 版本及證據

- 應用來源：`073732dbf6338c01073f911ec9f3bac8a7eaaf6a`；不是測試工具提交後的新 HEAD。
- 來源 manifest SHA-256：`adbdbb210f7f354dab938b1f859b4506b8f2b4630d2f7a1c5da8ab5d36d3b1f8`。
- 映像：`asia-east1-docker.pkg.dev/woundai-jackh001/woundai-medical-build/medical@sha256:777c6cfb7f097946fdf8ea7bfaf8684d1f45ced3b115129139c3d492292e7e8b`。
- 測試工具：`tools/probe_medical_container.py`，SHA-256 `2cea6291771becc023d7a51b1b63eab99a119f1bcc39d4c912f3b7c23e9e081f`。
- [Cloud Build 024ec31c](https://console.cloud.google.com/cloud-build/builds;region=asia-east1/024ec31c-adab-4b7a-b154-da69ab5171cf?project=421209514056)：SUCCESS，11:27:55–11:28:44，約 49 秒；探針本身 16.097 秒。
- 本機探針 11.146 秒，39/39。這是同一來源上下文的 Mac 執行結果，不當成 Linux 映像證據。

雲端工作只拉取既有映像並執行測試，没有重新建映像、推送、部署、改 IAM 或訓練模型。提交前重新讀回原映像 build 與 Artifact Registry；完成後核對專用 SA、每一步配方、来源 generation、腳本雜湊、映像 digest，以及日誌內唯一的測試結果。測試沒有接觸正式帳號密碼或研究照片。

## 實際通過的流程

| 類別 | 驗證內容 |
|---|---|
| 啟動與模型 | 套件內原始檔雜湊、canonical-byte gate；真實 Gunicorn 1 worker／8 threads 啟動及 health；student、A-UNet、UNet++ 原生 CPU 推論產生有限輸出 |
| 機構帳號 | 綁定 mmhps20261007；建立僅存在暫存區的 admin／physician／nurse；跨機構登入及帳號寫入被拒 |
| 手機保存契約 | 無照護 receipt 不回 image_id／persisted；care/attest 成功後 classify 明確回 persisted=true、staged、image_id 及正確尺寸 |
| 醫師標註 | nurse 無法宣稱醫師確認；缺研究同意被拒；醫師標註入列；完全相同重送回 duplicate_skipped，僅一筆可訓練紀錄 |
| 逐位元組保存 | JPEG、組織 PNG、Float32 LE 公尺深度與送出值一致；內參及參考解析度保存一致；覆蓋率及最小／最大距離由後端按實際 bytes 計算 |
| 深度拒絕 | nurse、截斷資料、缺內參參考寬度、全零重送皆拒絕；壞資料不能覆蓋已接受深度；相同 bytes 重送不宣稱替換 |
| 撤回／停用 | 撤回後可訓練數量歸零，影像移出可訓練區，標註及深度補傳被拒；停用醫師後已簽發 token 也被拒 |

39 是探針內各項斷言數量，包含兩次 classify 和各角色登入，不是 39 個獨立測試檔。三模型 smoke 與 canonical gate 另列，不灌入斷言數量。

## 隔離條件與可重現方式

雲端 Docker 使用 `--network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges`，僅 `/tmp` 為 256 MiB tmpfs，限制 4 GiB／2 CPU。沒有傳入雲端憑證；應用 child 使用明列的環境，固定 LocalStore 與暫存 runtime，帳密及簽章金鑰每次隨機產生且不輸出。Gunicorn 使用映像內原本的 app 模組，未 mock API、帳號、模型或儲存實作。測試結束停止子程序並移除暫存資料。

在隔離 Linux 容器內執行：

```sh
python -B /verification/probe.py \
  --manifest-sha256 adbdbb210f7f354dab938b1f859b4506b8f2b4630d2f7a1c5da8ab5d36d3b1f8
```

`/verification/probe.py` 以唯讀掛載上述已核對雜湊的檔案。實際 Docker argv、完整 Cloud Build 配方、提交紀錄及來源物件 generation，保存於本機受控證據目錄：

`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/medical-runtime-probe-20261008/`

重要檔案：`plan.json`、`build-final.json`、`container-probe.json`、`local-probe.json`、`validation.json`、`services-after.json`。這些資料不含真實病患影像、登入 token 或密碼。

首次本機試跑被 sandbox 阻止 loopback socket；開放僅本機測試後，另一項測試預期也修正了：後端合法增加 coverage／min_m／max_m，所以不能要求整份 metadata 沒有新增欄位。現在逐項核對上傳欄位不變，另驗後端計算值；沒有更改應用程式來配合測試。

## 尚不能宣稱完成的部分

1. **不是雲端 GCS 驗收**：LocalStore 不證明三桶 IAM、鎖定稽核、冷啟動持久化、網路故障或並行写入。既有 42/51 權限基線仍有九項不通過，沒有在本輪改權限。
2. **不是完整 RGB-D／3D 研究封包驗收**：此流程驗證既有 image／tissue／depth／metadata 保存；醫療 raw/meta 仍是分開 mutable 寫入，不能宣稱跨資產不可變快照或並行原子性。相同深度重送仍可追加稽核／索引紀錄，並不代表完全沒有額外寫入。
3. **不是臨床精度驗證**：HTTP 量測使用合成 phantom color route；模型 smoke 使用零值張量。沒有真實 LiDAR 誤差、RGB-D 配準、曝光同步時間、world pose 或組織 GT 匯入 WoundAI3D 的驗證。
4. **手機 App 27 問題尚未在線上解除**：讀回只有舊 medical 與 demo 兩個服務，revision／100% 流量維持原狀。舊 medical 505ff2e 的 care/attest 404 與缺 persisted 契約仍存在；不能靠改網址就把 App 27 變成 MMH 特殊版。

下一步仍是完成舊 runtime 最小權限遷移前置證據、MMH 專用部署與稽核方案覆核，以及依實際服務 URL 交付 iOS／Android 特殊版。不可逆稽核桶鎖定和 PR 合併仍未執行。詳細現況見 [MMH 登入與帳號交付說明](mmhps20261007_environment_and_accounts.md) 及 [runtime 遷移方案](mmh_runtime_migration_plan_20261007.md)。
