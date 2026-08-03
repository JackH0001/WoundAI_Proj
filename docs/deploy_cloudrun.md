# 部署到 GCP Cloud Run（彰化 asia-east1）

> 目的：**不必開著電腦也能讓 App 連到後端**，並且有一個固定網址可以看飛輪狀態。
> 免費額度足以涵蓋整個驗證期；region 選在台灣，日後收真實個案不必搬家。

---

## 0. 先決定：這個階段可以放什麼資料

| 階段 | 可放的資料 | 理由 |
|---|---|---|
| **現在（驗證）** | 範例圖、模擬圖、**自己的**傷口照 | 沒有他人個資，合規風險低 |
| **n=20 收案起** | 真實病人影像 | 需先完成 §6 的收案前檢核 |

**這條線不要越過。** 免費階段的目的是把連線與流程跑順，不是提早收案。
真實個案要等 §6 的加密、稽核、保存期限都到位。

---

## 1. 為什麼是 Cloud Run

| 候選 | 判斷 |
|---|---|
| **Cloud Run（採用）** | 閒置縮到零、不計費；免費額度每月 200 萬次請求；**有彰化 asia-east1**；已有 `Dockerfile` |
| GCP Always Free VM | e2-micro 只有 1 GB RAM，光是 OpenCV + ONNX Runtime + 79 MB 模型就會 OOM；而且**只在美國區** |
| GitHub Codespaces | 每月 60 小時、閒置 30 分鐘停機。跑不了常駐服務 |
| GitHub Actions | 任務執行器不是伺服器，單次上限 6 小時 |
| Oracle Always Free | 規格最好（4 核 / 24 GB）**但沒有台灣區**，真實個案階段仍要搬 |
| Cloudflare Tunnel + 家用機 | 合規最單純、資料留在台灣，但**需要一台 24 小時開機的電腦**（Mac mini 到位後改走這條，見 §7） |

**成本預期**：驗證期流量（每天數十次請求）遠低於免費額度，實際帳單應為 **US$0**，
物件儲存幾 MB 也在免費層內。真的超出時最可能的原因是被掃描機器人打——見 §5。

---

## 2. 前置

```powershell
# 1) 安裝 gcloud CLI 後登入
gcloud auth login
gcloud config set project <你的專案ID>

# 2) 開啟需要的服務
gcloud services enable run.googleapis.com artifactregistry.googleapis.com `
                       storage.googleapis.com secretmanager.googleapis.com

# 3) 建立影像用的儲存桶（彰化，統一存取控管）
gcloud storage buckets create gs://<你的桶名> --location=asia-east1 --uniform-bucket-level-access
```

**桶名要全球唯一**，建議 `woundai-flywheel-<你的識別>`。

---

## 3. 設定密碼（不要寫進映像）

```powershell
# 產生一個夠長的密碼並存進 Secret Manager
$pw = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | % {[char]$_})
$pw | gcloud secrets create woundai-admin-password --data-file=- --replication-policy=automatic
$pw    # 記下來，App 設定頁要填
```

`ADMIN_PASSWORD` 現在**沒有程式碼預設值** —— 沒設就是該帳號不存在、所有登入失敗。
這是刻意的：「有預設值」等於把上線密碼寫在公開 repo 裡。

JWT 簽章金鑰也要一組：

```powershell
$jwt = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 48 | % {[char]$_})
$jwt | gcloud secrets create woundai-jwt-secret --data-file=- --replication-policy=automatic
```

---

## 4. 部署

```powershell
cd C:\dev\WoundAI_Proj\Backend\Flask

gcloud run deploy woundai-backend `
  --source . `
  --region asia-east1 `
  --allow-unauthenticated `
  --memory 2Gi `
  --cpu 2 `
  --timeout 120 `
  --concurrency 4 `
  --min-instances 0 `
  --max-instances 3 `
  --set-env-vars "WOUNDAI_STORE=gcs,WOUNDAI_GCS_BUCKET=<你的桶名>,WOUNDAI_GCS_PREFIX=flywheel" `
  --set-secrets "ADMIN_PASSWORD=woundai-admin-password:latest,JWT_SECRET_KEY=woundai-jwt-secret:latest"
```

部署完會給一個網址，形如 `https://woundai-backend-xxxxx.asia-east1.run.app`。

### 這些參數為什麼是這樣

| 參數 | 理由 |
|---|---|
| `--memory 2Gi` | 三個 ONNX 模型 79 MB，但 ONNX Runtime 的執行期記憶體 + OpenCV + 影像緩衝遠大於檔案大小。1 Gi 會在難例集成路由（同時載三個模型）時被 OOM kill，而 Cloud Run 的 OOM 只會回 500，看不出原因 |
| `--cpu 2` | 實測難例集成在 2 vCPU 是 796 ms。1 vCPU 會翻倍到 1.5 s 以上，接近使用者會放棄的門檻 |
| `--concurrency 4` | 推論是 CPU-bound。預設 80 會讓幾十個請求擠在 2 顆核心上，每個都變慢。設低一點讓 Cloud Run 去開新實例 |
| `--min-instances 0` | 閒置不計費。代價是冷啟動（見下） |
| `--max-instances 3` | **成本上限**。沒有這個，被機器人掃到就會無限擴張 |
| `WOUNDAI_STORE=gcs` | Cloud Run 的容器檔案系統是暫時的。**沒有這一項，實例一回收佇列與影像就全沒了** |

### 冷啟動

`min-instances=0` 時，閒置一段時間後的第一個請求要等容器啟動＋載入模型，約 **10–30 秒**。
量測時 App 會看起來卡住。兩種處理：

**A. 接受它，但讓使用者知道**——App 設定頁的「連線測試」順便當暖機用，出門前按一下。

**B. 定時暖機**（免費額度內）：

```powershell
gcloud scheduler jobs create http woundai-warmup `
  --location asia-east1 --schedule "*/10 6-20 * * *" `
  --uri "https://<你的網址>/api/health" --http-method GET
```

只在 06:00–20:00 每 10 分鐘打一次，避開夜間。這樣門診時間內幾乎不會遇到冷啟動。

**不建議 `--min-instances 1`**：那會 24 小時計費（約 US$10–15/月），
而你現在的用量根本不需要——把錢留到真的多院同時使用時再說。

---

## 5. 安全（公開網址就是公開的）

`--allow-unauthenticated` 讓網址對全世界開放，唯一的門是應用層 JWT。所以：

- **密碼一定要夠長**（§3 產生的 32 碼隨機字串），不要用 `woundai-admin`
- **確認舊密碼已失效**：部署後試著用 `admin` / `woundai-admin` 登入，應該要失敗
- **設預算警示**，避免被掃描流量燒錢：

```powershell
gcloud billing budgets create --billing-account=<帳單ID> `
  --display-name="woundai" --budget-amount=5USD `
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9
```

- 日後合作院所固定後，改用 **Cloud Armor IP 白名單**只放行醫院出口 IP，
  這比任何密碼都有效。

---

## 6. App 端設定

主畫面 →「設定」→ 後端位址填 Cloud Run 網址（**含 `https://`**）→ 帳號 `admin`、
密碼填 §3 產生的那組 → 儲存 → 連線測試。

看到「✅ 連線成功」與佇列健康度就完成了。**之後不必再開電腦**，也不必每次確認連線。

主控台：瀏覽器開 `https://<你的網址>/console`，登入後可看收案進度與資料鏈異常。

### 收案前檢核（真實個案階段才需要）

- [ ] 儲存桶啟用 **CMEK 或至少確認預設加密**，並設定生命週期規則對齊「結案 90 天刪影像」
- [ ] `audit.jsonl` 的物件加上 **Object Lock（WORM）**，或實作雜湊鏈
- [ ] Cloud Armor IP 白名單
- [ ] 與院方簽 DPA；備妥 SBOM 與開源授權清單
- [ ] 確認所有資源都在 `asia-east1`（含儲存桶、Secret、日誌）

---

## 7. 未來搬到 Mac mini

儲存層已經抽象化（`store.py`），搬家只是換一個環境變數：

```bash
export WOUNDAI_STORE=local
export WOUNDAI_FLYWHEEL_DIR=/Users/<你>/woundai/flywheel
python app.py
```

再用 **Cloudflare Tunnel**（免費）給它一個固定 HTTPS 網址，不必開防火牆連接埠、
也不必有固定 IP：

```bash
cloudflared tunnel create woundai
cloudflared tunnel route dns woundai woundai.<你的網域>
cloudflared tunnel run --url http://localhost:5000 woundai
```

App 那邊只要改設定頁的位址，其他都不用動。

**要不要搬？** 兩邊各有代價：Mac mini 的資料留在你手上、沒有雲端帳單，
但可用性靠你家的電力與網路；Cloud Run 反過來。真實收案階段建議**先留在 Cloud Run**，
因為醫院資安審查對「有 SOC 2 的雲端供應商」通常比對「工程師家裡的電腦」友善得多。

---

## 8. 排錯

| 症狀 | 原因 |
|---|---|
| 登入一直失敗 | `ADMIN_PASSWORD` secret 沒掛上。`gcloud run services describe woundai-backend --region asia-east1` 看環境變數 |
| 量測回 500，日誌沒訊息 | 多半是 OOM。把 `--memory` 調到 4Gi 試試；Cloud Run 的 OOM kill 不會留下 Python 例外 |
| 統計數字歸零 | `WOUNDAI_STORE` 沒設成 `gcs`，資料寫進了暫時的容器檔案系統 |
| 第一次請求很慢、之後正常 | 冷啟動，見 §4 |
| App 顯示「連不到後端」 | 位址少了 `https://`，或設定頁存的還是 `10.0.2.2` |
