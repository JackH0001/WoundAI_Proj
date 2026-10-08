# MMH 舊執行身分撤權：2026-10-08 驗證

**本次指定 Editor 撤權及驗收完成；MMH 服務尚未部署。** 實際政策轉換使用 `c52e23f433d3d36bb18f8753ccc83ab2d4b420e9`，執行前工作樹乾淨，PR #19 同 head 的 7 項 CI 全部 SUCCESS。

## 變更與範圍

Jack 明確授權在 `woundai-jackh001`，僅移除 `421209514056-compute@developer.gserviceaccount.com` 的專案層 `roles/editor`，驗證失敗可恢復原角色。初次執行被自動核准審查攔下，當時沒有修改；收到具體帳號／角色／回復範圍的再次授權後才執行。

台北 17:05 開始，使用新讀取的 IAM etag，只移除指定 member。讀回與預期政策語義一致，其他成員、角色及條件保持不變。舊媒體／稽核桶 objectAdmin、舊 admin／JWT 密文 secretAccessor 直接授權保留，沒有讀取密文值。51 項驗證通過，未觸發回復。這不是舊資料桶已經 append-only 的宣稱。

執行前，Cloud Asset API 已依授權啟用；六種工作負載類型僅列出兩個既有 Cloud Run 服務。Cloud Build 全部 44 地區再查，沒有活動建置或 triggers。這是查詢當下的同專案盤點，不包含跨專案依賴或消除索引延遲。

## 實際驗證

| 項目 | 結果 |
|---|---|
| IAM 純轉換及新 etag 回復測試 | 9/9；變更前 CI 7/7 |
| Policy Troubleshooter v3，least-privilege | **51/51**；沒有 UNKNOWN 或錯配回應當成通過 |
| 先前 9 個缺口 | 三 MMH 桶刪除、舊媒體桶刪除、MMH SA actAs/keyCreate/delete/disable/update 全改為拒絕 |
| 舊平台必要權限 | 舊媒體 get/list/create/delete、稽核 get/list/create、兩把既有密文 access 均仍允許 |
| 撤權後已登入原圖／preview.svg | HTTP 200；原圖 192×256 成功解碼；未宣稱 SVG 呈現通過 |
| Jack 撤權後幾何模擬圖 | 台北 17:07 登入 200、classify 200，0.912 秒；舊 care/attest 仍為原有 404 |
| 寫入中繼資料 | 同時間窗 1 個新 JPEG，717,209 bytes；指定 generation 再讀回一致 |
| medical builder 合成 smoke | SUCCESS；來源下載、容器內文字比對、映像推送與日誌標記通過 |
| 服務讀回 | 正式／demo revision、流量及 runtime 與變更前相同，health 皆 200／healthy |

51 項只涵蓋列出的主體／資源／權限，不能外推成整個專案所有冒用鏈都不可能。新圖寫入與既有 phantom 讀取是兩筆資料，**不是同一張影像的端到端位元組往返**。未下載新圖、未比對手機原始 hash、未新增虛構醫師標註，亦未驗收 MMH GCS 的 RGB-D／組織資料完整性。

## 撤權後建置證據

- [Cloud Build 8d1534c8-9762-49fa-873c-10d5af9ac6c7](https://console.cloud.google.com/cloud-build/builds;region=asia-east1/8d1534c8-9762-49fa-873c-10d5af9ac6c7?project=421209514056)，約 10.14 秒。
- 身分 `woundai-medical-build@woundai-jackh001.iam.gserviceaccount.com`；只有四個合成文字／設定檔，無應用模型或病患影像。
- 合成文字 SHA-256 `7b6e18ed7bee08232b5ae4799bc0bad1555a18f8e77088ab5caa06f480f011a3`；容器內逐位元組比對成功，日誌標記 1 筆。
- 映像 digest `sha256:52624c80793e44d9943a4f96505ae2ac5e0cc4f3e6abeec58efed4c7f07e3981`，與 Artifact Registry 讀回相符。這是 smoke 映像，不可拿來部署應用程式。
- Cloud Build 的專案預設身分仍為 Compute；後續必須使用指定 medical builder 的已驗證入口。依賴隱含 Editor 的舊建置指令不保證可用。

## 部署前置與下一階段

台北 17:14 重跑唯讀 metadata preflight，13 項完整執行、7 項通過，整體仍拒絕部署。新增通過為舊 Editor 已移除。剩餘六項：來源尚未等於已合併 main、稽核桶未鎖定、四把 MMH 專用密文版本尚無可用證據。查詢失敗沒有被當成 NOT_FOUND 或可忽略。

MMH 專用執行入口、限定 runtime IAM／密文、私有部署與真實 GCS 業務驗收仍須完成。Draft PR 未合併，不能將候選來源冒充 main。稽核桶不可逆七年鎖定仍需單獨具體授權，不包含在本次撤權授權中。

鎖定前內容複核另發現：稽核不只有雜湊；包含 actor／org／role、個案或影像代碼、事件與結果。排除／還原路徑會將操作人員的自由文字 note 寫進 result，拒絕事件可能寫入例外文字。**不能承諾稽核不含可識別資料**，也不能承諾撤回後刪掉已鎖定稽核。本階段維持合成資料，真實收案前需按 IRB／機構範圍另行檢視內容最小化及保留政策。七年是現行程式政策，不是法規年限結論。

## 本機參照

證據根目錄 `woundai-institution-evidence-20261007/mmh-isolation-20261008/`：

- `migration-result.json`、`project-iam-apply-before.json`、`project-iam-request.json`、`project-iam-final.json`
- `least-privilege-after.json` 與 `.raw.json`；`transform-ci.json`
- `post-read-requests.json`、`post-write-requests.json`、`post-write-object-metadata.json`
- `post-build-readback.json`、`post-build-artifact.json`、`post-build-proof-logs.json`、`post-build-validation.json`
- `services-before.json`、`services-after.json`、`post-health.json`、`post-metadata-preflight.json`

原始 IAM、物件名稱及請求路徑只留本機受控證據，不放公開 PR。相關規格見 [MMH 部署檢視](mmh_runtime_deployment_review_20261008.md) 與 [帳號交付說明](mmhps20261007_environment_and_accounts.md)。
