# Lite 專屬金鑰準備與登入續接（本機合成驗收）

父來源 `9ce7e704e41e45efd7253478634826011a639b49`。本輪完成獨立準備元件與原生登入橋接，**不是雲端已部署或可正式收案的證據**。一般網站沒有取得 KMS 建立／銷毀權；原始影像、研究資料、稽核與真實金鑰均未修改。

## 修正前後

先前所有合成登入測試預先放入帳號 KMS key。實際新帳號無此 key 時，`AccountGrantCipher.prepare()` 會在 Apple 授權碼交換前拒絕，安全但無法完成首次登入。

本輪新增預設關閉的 `KeyReadinessClient`：只有在既有 App Attest、Apple 簽章／nonce／state 核對及耐久 grant membership 完成後，才送出 scope、不可逆 person 識別、grant 與固定 key version。原生設定需明確增加 `key_preparation.origin`，並重新核對設定雜湊。未設定時保留原有預先準備規則。首次 wrapped DEK 已保存後，不再為後續登入額外呼叫準備服務。

新 `AccountKeyProvisioner` 在獨立程序／身分下運行：讀既有 lifecycle 與 membership，僅執行固定 keyring 下的 `cryptoKeys.get`、version GET 及 `cryptoKeys.create`。名稱依既有 scope/person 派生，同帳號兩台裝置共用同一版本；沒有建立 version 2、換名、輪替、restore、encrypt/decrypt、銷毀、IAM 或刪除資料的動作。

新使用者不需要預先列在批次名單，**但本輪執行器是有限時、有限量的候選驗收模式**：核對明確 manifest 雜湊、來源與映像標記，最長一小時，最多 1,000 次 create POST（不是 1,000 個成功帳號）。並行失敗／重試可能多保留一個已用名額，寧可保守計入，不以回應遺失重置上限。到期仍可讀回同一已建立 key，不能建立新 key。正式日常供應的長期預算／配額政策尚待核准與驗收，不把這個有限窗口當作一般用戶上市後的永久方案。

## 保存順序與競爭情境

1. 耐久帳號／grant 清冊先存在；未註冊、不同帳號／scope、未列入 grant 的請求不進 KMS。
2. 保存每帳號準備工作與固定 key version；以 CAS 保留一次 create 額度，寫入不可變稽核，才發出 KMS create。
3. 使用固定 cryptoKeyId、預設產生 version 1，固定 SOFTWARE、Google symmetric encryption、非 import、無自動輪替、30 日 destruction window。讀回 metadata／labels／版本狀態必須吻合。
4. 逾時不猜成功，不立即建立替代名稱。下一次同一登入要求先查原 key；若已建立便只讀回，不重送 create。409 也要讀回，不能單憑 409 當成功。403 不是不存在。
5. 無來歷的既有 key、錯版本、停用、已排程銷毀或已銷毀均停止；不能改設定、恢復或建立替代 key 掩蓋錯誤。
6. 完成讀回前後都核對帳號仍 active。若建立途中已刪帳，拒絕登入、保留孤立 key／cleanup_required 稽核，不自行銷毀。即使極晚的關閉發生在回執之後，Web cipher／grant 再查 lifecycle，仍不得交換 code 或恢復服務。稽核者須同時查帳號 closing 與所有準備工作，不能只查單一 orphan 訊息。
7. 準備失敗回 503，原 Apple code 尚未交換；iOS 現有 confirm checkpoint 保留同一要求，使用者重試可續接。超過 challenge 或 Apple 授權碼有效期仍須明確重新登入；不宣稱一次性 code 可以無限期重送。交換已開始的 uncertain code 仍依原規則禁止重用。

Apple code/token、email、Apple subject、明文 DEK 均不傳入或存入準備元件。只有原有不可逆帳號索引與資源關係用於稽核。

## 身分與部署界線

- Web 的 private RPC 只向固定 HTTPS Cloud Run origin 送出，ID token audience 固定該 origin；使用 metadata identity endpoint，核對實際 service account email，沒有搜尋 ADC 或 SA JSON key。禁止 redirect，有限 timeout；應用層不自動重試 POST。
- `create_app()` 僅為**私有**程序的 WSGI 路由組合；它本身不授予 IAM，也不是可公開的授權端點。Cloud Run 必須保留驗證，僅指定 Web SA 有 run.invoker；無 allUsers/allAuthenticatedUsers。真實 Google 身分、未授權呼叫 401/403、錯 audience、其他 SA、MMH/Demo 身分及 Web 直接 create KMS 全部待雲端負向驗收。
- Web 可 get/encrypt/decrypt；provisioner 可 get/create，不能 decrypt/destroy；清理身分另案。worker 對 lifecycle/member 僅 GET，自己的進度與稽核有精確路徑的 create/CAS。不得讓它讀 Apple vault、研究媒體、模型結果或訂閱憑證。
- 本輪新增 client 至原生 60 檔打包清單；**沒有**把 `lite_key_provisioning.py` 放進 Web 映像。沒有改舊 Lite 的打包允許清單或依賴鎖定。
- 獨立 worker 的設定啟動器、部署包、IAM／桶／KMS 實機驗收仍待完成；本輪以明確注入依賴的本機 Flask transport 驗證，不能直接當作已完成部署入口。
- 本輪沒有改 iOS/Android，也未重新宣稱其實機測試通過。

## 模擬結果與重跑

新增 35 項金鑰／登入測試與 3 項原生設定測試，涵蓋未建立的新帳號、同帳號多裝置、並行、建立或 RPC 回應遺失、重啟、關閉競爭、預算／時限、稽核失敗、錯 metadata、私有回執範圍、設定與呼叫身分錯誤。測試沿用真實 Apple/App Attest 簽章驗證與 SQLite CAS，KMS/Apple 網路由合成替身代替；不等於真實提供者驗收。

獨立探針四種情境：

| 情境 | 第一次／重試 | Apple code 交換 | KMS 版本 | 模型／額度 |
|---|---|---:|---:|---:|
| 新帳號 | 200／200 | 1 | 1 | 1／1 |
| 私有回覆遺失 | 503／200 | 1（第一次為 0） | 1 | 1／1 |
| KMS 建立回覆遺失 | 503／200 | 1（第一次為 0） | 1 | 1／1 |
| 建立途中刪帳 | 503／401 | 0 | 1（保留待處置） | 0／0 |

三個成功情境均重送同一服務要求並核對相同回執，沒有再次執行模型／扣額度；四個情境原登入要求均不變。

```powershell
$env:PYTHONPATH='tools;engineering/phase2;Backend/Flask;.'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:WOUNDAI_STORE='local'
python -m unittest -v test_lite_key_provisioning test_lite_native_startup test_lite_native_package test_lite_build_context
python tools/lite_key_provisioning_probe.py --output key-provisioning-probe.json
python -m unittest discover -v -s engineering/phase2 -p 'test_lite*.py'
```

實際測試總數及原始 log 隨本輪外部覆核包；完整 Lite 與 prelaunch 有重疊，不相加。未部署、未公開、未更新商店，完整刪帳、研究資料處置與 Apple Sandbox S0–S9 仍是未完成閘門。

## 原廠參照（2026-10-31 核對）

- [Cloud KMS create](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys/create)：cryptoKeyId 唯一、可選擇是否建立初始版本。
- [CryptoKey 結構](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys)：purpose、versionTemplate、importOnly、rotation、destroyScheduledDuration 的意義。固定 metadata 是本專案策略，不是 Google 保證此 App 已驗收。
- [Cloud Run 服務間驗證](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)：專用服務身分、run.invoker、固定 receiving-service audience。本輪沒有修改 IAM。
