# 組織分割：資料量級評估與實作規劃

> 對象：決定要不要投入、投入多少。結論在 §1，理由在後面。

---

## 1. 結論先講

**三件事必須先講清楚，否則後面的規劃會建立在錯誤的期待上。**

### 1.1「高精度成熟模型」目前不存在——這是領域現況，不是資源問題

已發表的最佳結果（2025–2026）：

| 任務 | 最佳 Dice | 出處 |
|---|---|---|
| **傷口範圍**分割（我們現在做的） | **0.927** | JMIR mHealth 2022 |
| 肉芽 granulation | **0.696** | Sci Rep 2025 |
| 纖維滲出／腐肉 | **0.750** | Sci Rep 2025 |
| 壞死 necrosis（僅 56 張訓練影像） | **0.503** | Sci Rep 2025 |
| 多類綜合（DFUTissue） | **0.78–0.79** | WoundFormer / DFUTissueSegNet |

我們現行的傷口範圍模型是 0.737 單模 → 0.800 TTA → 0.855 集成。
**組織分割的天花板比它低一大截**，而且那是別人用更多資料做出來的。

投入這件事的合理目標是「**一個能省下醫師時間的助手**」，不是「一個可以取代判讀的模型」。
若對外（IRB、法遵、投資人）承諾後者，做不到的風險很高。

### 1.2 真正的瓶頸是**醫師的時間**，不是 GPU

- 5 類逐像素標一張傷口 ≈ **5–15 分鐘**的專科時間
- 200 張 ≈ **20–50 小時**
- 訓練本身：512×512、5 類、~250 張，ResNet34-UNet 在中階 GPU 約 **20–40 分鐘**，
  CPU 過夜也跑得完。**計算成本可以忽略。**

所以規劃的重心全部放在「怎麼讓標註幾乎不花額外時間」。

### 1.3 我們已經意外地把標註工具做好了

醫師**本來就要**做修邊確認（那是 GT 的合法性來源）。現在修邊畫面已經：

- 逐像素預填分類結果（`TissueSeg`）
- 提供 5 類筆刷（含「其他」）
- 即時顯示比例

**把他修正後的 raster 上傳，額外成本接近零**——這是整個規劃裡最重要的一句話。
公開資料集要花數十小時人工才拿得到的東西，我們是臨床流程的副產品。

---

## 2. 公開資料可用什麼

| 資料集 | 張數 | 類別 | 備註 |
|---|---|---|---|
| **WoundTissueSeg** | 147（118/14/15） | 腐肉、肉芽、浸潤、壞死、骨、肌腱 | 2025 發表，**與我們的 5 類最接近** |
| **DFUTissue** | 110（78/16/16） | 含肉芽 | 糖尿病足 |
| **Swift Wound Dataset** | 較大 | 上皮／肉芽／腐肉／焦痂 | 商業資料，取得條件待查 |

⚠ **公開總量只有兩百多張。** 這解釋了為什麼 SOTA 只到 0.7 左右——
不是方法不好，是資料太少。也因此我們自己收的每一張都有實質邊際價值，
不像傷口範圍分割那樣早已被公開資料飽和。

⚠ **類別定義必須先對齊**才能混用。WoundTissueSeg 有骨與肌腱兩類，
我們把它們歸在「其他」——匯入時要做明確的映射表，不能默默 argmax。

---

## 3. 資料量級：實際需要多少

以 5 類、512×512、ImageNet 預訓練骨幹估算：

| 階段 | 我們的張數 | 加上公開資料 | 期望 Dice（多類平均） | 用途 |
|---|---|---|---|---|
| **A 基線** | 0 | ~250 | 0.55–0.65 | 確認管線通、建立比較基準 |
| **B 首批臨床** | 20–50 | ~300 | 0.60–0.70 | 驗證自有資料是否真的有幫助 |
| **C 有意義** | 150–250 | ~450 | 0.70–0.78 | 追上已發表水準 |
| **D 超越現況** | 600+ | ~850 | 未知（>0.80 需驗證） | 才有發表／宣稱的空間 |

**關鍵判斷點在 B → C 之間**：如果加了 50 張自有資料，Dice 沒有明顯上升，
代表問題出在標註一致性或影像品質，而不是數量——那時候繼續收 500 張只是把錯誤放大。
所以 §6 的驗收條件是「學習曲線還在爬」，不是「收滿幾張」。

> 對照：文獻中有以 17,000 張訓練的例子，但那是機構級資料。
> 我們的路徑不是靠量取勝，是靠**同一台相機、同一套校正貼紙、同一群標註者**的一致性。
> 小而齊的資料集在這個任務上未必輸給大而雜的。

---

## 4. 要實作什麼

### 4.1 遮罩上傳（P0，最優先）

**現況**：`tissue_frac` 只上傳**比例**，不是遮罩。比例訓練不了分割。

**格式**：8-bit 調色盤 PNG，尺寸＝修邊柵格（長邊 ≤1024），值＝組織碼 0–5。

```
POST /api/v1/annotation
  tissue_mask_png : base64（≈10–40 KB，相對於 JPEG 可忽略）
  tissue_raster   : { rx0, ry0, mw, mh, m_scale }   ← 映回影像座標的仿射參數
```

送小柵格而不是影像尺寸的遮罩：省頻寬，而且**那才是醫師實際畫的解析度**——
放大成影像尺寸再送，等於在資料裡加入我們自己插值出來的假精細度。

### 4.2 ⚠ 最關鍵的一個欄位：`tissue_edited`

醫師如果**沒有動組織筆刷**，那張遮罩就是 `TissueSeg` 的色彩啟發式輸出。
把它當 GT 拿去訓練，等於**用模型自己的輸出訓練自己**——指標會很漂亮，
因為模型只是在學會複製它已經會做的事，而臨床表現不會有任何改善。

這是本專案反覆出現的那個失敗形狀：**沒有錯誤、沒有警告、結果是錯的。**

```
tissue_edited     : bool    醫師是否動過組織筆刷
tissue_edit_px    : int     被重畫的像素數
tissue_edit_ratio : float   佔遮罩比例
```

匯出訓練集時 **`tissue_edited=false` 一律排除**，且統計要分開列出，
讓人隨時看得到「有多少筆其實沒人看過」。

### 4.3 影像品質閘門（沿用你既有的條件篩選）

模糊、過曝、角度過斜的影像會產生垃圾 GT，而垃圾 GT 比沒有 GT 更糟。
classify 當下就算好並落盤，匯出時可依門檻篩：

| 指標 | 方法 | 建議門檻 |
|---|---|---|
| 對焦 | 傷口 ROI 內 Laplacian 變異數 | < 80 → 標記模糊 |
| 曝光 | ROI 內 0/255 飽和像素佔比 | > 5% → 標記過曝／死黑 |
| 角度 | ArUco 四邊形的長寬比與對邊夾角 | 偏離正方 > 25% → 透視過斜 |
| 尺度 | 標記邊長 / 影像長邊 | < 4% → 標記太小，mm/px 誤差放大 |
| 解析度 | 傷口 ROI 短邊像素 | < 256 → 細節不足 |

⚠ **只標記、不自動丟棄。** 自動丟會讓「為什麼這張沒進訓練集」變成黑箱；
標記讓匯出時可以調門檻，而且每次調整都有紀錄。

### 4.4 從雲端把資料拉回本機訓練

**控制面與資料面分開。**

| | 走哪裡 | 內容 | 體積 |
|---|---|---|---|
| **控制面** | Cloud Run `/api/v1/dataset/manifest` | 哪些 image_id 合格 + 完整溯源 | 幾百 KB |
| **資料面** | GCS（`gcloud storage cp`） | 影像與遮罩本身 | GB 量級 |

理由不是潔癖：

- Cloud Run 的回應有 **32 MB 上限**、有請求逾時，而資料集是 GB 量級
- 串流大檔要付 CPU-秒與出口流量；GCS 本來就有 IAM、可續傳、可增量
- 分開之後**規則仍然在控制面執行**（同意、撤回、誤送排除、醫師修正判準、品質門檻），
  而且每次匯出都寫進稽核軌跡——那才是控制面該做的事

```powershell
cd C:\dev\WoundAI_Proj\Backend\Flask
.\pull_dataset.ps1 -BaseUrl <網址> -Bucket woundai-flywheel-jackh001 -Out D:\woundai_ds
.\pull_dataset.ps1 ... -DryRun          # 只看有幾筆合格、被什麼排除
```

⚠ **逐檔 `cp` 而不是整個前綴 `rsync`。** manifest 已經套用了同意與品質規則；
`rsync` 會把**被排除的樣本也拉下來**——包含已撤回同意的那些。那是合規事故。

⚠ **下載之後，本機那份不再受後端的撤回同意與保存期限約束。**
病患日後撤回時雲端會自動排除，你手上那份不會。這無法自動化（我們不知道你複製到哪去了），
只能靠流程：訓練完刪除原始影像，只留權重與指標。

### 4.5 匯出與訓練

- `pull_dataset.ps1`：控制面取 manifest → 資料面對 GCS 抓 → 輸出 `images/` + `masks/` + `manifest.json`
- `train_tissue_seg.py`：smp + ResNet34-UNet，5 類，類別權重取像素頻率倒數平方根
  （壞死通常只出現在少數影像；不加權模型會學會「永遠不預測壞死」而整體 Dice 看起來還行）
- **切分依 WD-code，不依張數**。同一個傷口的不同回診照片極為相似，落在訓練與驗證
  兩邊會讓 Dice 虛高好幾個百分點，而那個提升是假的
- 公開資料集用獨立映射腳本轉成同格式，`manifest` 標明來源

---

## 5. 四階段路線（可隨時停在任一階段）

| 階段 | 內容 | 誰做 | 時間 | 成本 |
|---|---|---|---|---|
| **P0** | 遮罩上傳 + `tissue_edited` + 品質指標落盤 | 我 | ~1 週 | 0 |
| **P1a** | 匯入腳本＋類別映射＋管線驗證（**已完成**） | 我 | 完成 | 0 |
| **P1b** | 實際下載資料集並跑基線訓練 | 你（需 torch） | ~1 天 | 0（本機） |
| **P2** | 收 20–50 張自有 → 微調 → **比較曲線** | 你＋醫師 | 隨收案 | 0 |
| **P3** | 決策點：曲線還在爬才續收至 150–250 | — | — | 視結果 |

**P0 要現在做的理由只有一個**：影像依保存政策會被清除，
現在沒收下來的遮罩**日後永遠補不回來**。而 P1–P3 隨時可以往後延。

**P1 刻意先用公開資料**：在自己的資料進來之前就把管線、指標、切分方式全部跑通。
等到臨床資料到手才發現匯出格式有問題，那批醫師時間就白花了。

### P1a 已完成的部分

- `import_public_tissue.py`：**明確的類別映射表**，每一條都寫了理由；
  來源出現未知類別時**硬失敗**而不是默默當背景
- `test_tissue_training_pipeline.py`（22 項）：鎖住五件「錯了也不會報錯」的事——
  映射猜錯、切分洩漏、未修正遮罩混入、類別缺席、inter-rater Dice 算錯
- `analyze_interrater.pair_stats` 抽成可測函式並以已知重疊驗證（Dice 0.8、混淆矩陣）

### P1b 需要你做的兩件事

1. **下載資料集**。WoundTissueSeg / DFUTissue 都要到原始出處取得（多半要填表或
   接受授權條款）。腳本刻意不自動下載——把「取得授權」自動化既做不到也不應該。
2. **建 `classes.json`**（值 → 類別名）。⚠ **不要用猜的**：
   索引順序猜錯的話訓練照樣跑得完、Dice 照樣有數字，但模型學到的是完全不同的東西。

```powershell
python engineering/phase2/import_public_tissue.py --preset woundtissueseg --print-mapping
python engineering/phase2/import_public_tissue.py --src D:\WoundTissueSeg --preset woundtissueseg --out D:\public_ds
python engineering/phase2/import_public_tissue.py --src D:\DFUTissue --preset dfutissue --out D:\public_ds --append
python engineering/phase2/train_tissue_seg.py --data D:\public_ds --dry-run
pip install torch torchvision segmentation_models_pytorch     # 本沙箱裝不了，你的機器可以
python engineering/phase2/train_tissue_seg.py --data D:\public_ds --epochs 60 --out runs/base
```

**全程本機即可。** 不需要雲端 GPU，也不需要把臨床影像送去第三方訓練平台——
後者還會多一整套資料處理協議要簽。

---

## 6. 驗收條件（先寫，避免事後挪動）

- **P1**：在 WoundTissueSeg 官方切分上重現 Dice ≥ 0.55（多類平均）。
  達不到代表管線有問題，不是資料不夠。
- **P2**：加入自有資料後，**驗證集 Dice 相對 P1 提升 ≥ 0.03**。
  沒提升 → 先查標註一致性與影像品質，**不要**直接加量。
- **一致性基準**：inter-rater Dice。這個數字是模型表現的**天花板**——
  模型不可能比兩個人彼此同意的程度更準。沒有它，Dice 0.70 到底是好是壞無從判斷。

  **不需要特地做「20 張研究」。** 任何被兩位以上標註者標過的影像都是免費的一致性資料：
  後端會把平行標註保留成 `parallel_rater` 狀態（不計入訓練，避免同一張圖佔兩倍權重），
  `pull_dataset.ps1 -Kind interrater` 抓下來，`analyze_interrater.py` 算逐類 Dice
  與混淆矩陣。日常流程中偶爾讓兩位醫師標同一張，樣本自己會累積。

  > ⚠ 這在 2026-08-05 前是**做不到的**：舊規則只用 `image_id` 當「取最新」的鍵，
  > 第二位醫師的標註會把第一位的標成 `superseded` 而排除，稽核還會記成「醫師修訂版」——
  > 一件沒發生過的事。改成 `(image_id, actor)` 之後兩份都保留。

---

## 7. 誠實邊界

- 組織分割**不會**在 n=20 收案階段變得可用。它是一條與臨床導入並行的長線。
- 「其他」類（肌腱、異物、血水、遮蔽物）**沒有公開資料**，只能靠自有累積。
  它也是最難的一類——定義上就是「不屬於其他四類的所有東西」，類內變異極大。
- 現行的 `TissueSeg` 色彩啟發式**不是**這個模型的前身，而是它的**待取代對象**。
  兩者的關係要在文件與 UI 上分清楚，否則日後會出現「模型 vs 規則」的指標混用。

---

## 參考

- [Deep Learning for Wound Tissue Segmentation: A Comprehensive Evaluation using A Novel Dataset (arXiv 2502.10652)](https://arxiv.org/abs/2502.10652)
- [Enhancing chronic wound assessment through agreement analysis and tissue segmentation (Sci Rep 2025)](https://www.nature.com/articles/s41598-025-06703-5)
- [WoundFormer: Multi-Scale Spatial Feature Fusion for Multi-Class Wound Tissue Segmentation](https://arxiv.org/html/2605.19868v1)
- [Fully Automated Wound Tissue Segmentation Using Deep Learning on Mobile Devices (JMIR mHealth 2022)](https://mhealth.jmir.org/2022/4/e36977)
- [MedSAM2: Segment Anything in 3D Medical Images and Videos](https://opencv.org/blog/medsam2/)
- [Interactive Medical-SAM2 GUI（Napari 半自動標註工具）](https://arxiv.org/html/2602.22649v1)
- [UWM Big Data Lab Datasets](https://sites.uwm.edu/bigdata/datasets/)
