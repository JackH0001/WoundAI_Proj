# 組織分割資料集：從零到跑出第一個 Dice

> 對象：要在自己電腦上把 P1 跑起來的人。照順序做，每一步都有可驗證的產出。

---

## 先回答「先下載還是先跑？」

**先跑，用合成資料。**

下載公開資料集要填表、等回覆、解壓、對照類別編碼 —— 那是一個下午。
而 torch 裝不起來、CUDA 版本不對、訓練迴圈有 bug 這些事**十分鐘就能發現**。

先把整條鏈用假資料跑通一次，確定管線是好的，再去弄真資料 ——
就不會在「到底是資料有問題還是程式有問題」之間耗掉時間。

---

## 步驟 0：不需要下載任何東西（10 分鐘）

```powershell
cd C:\dev\WoundAI_Proj

# 0-1 先看類別映射表。有【待醫師覆核】標記的三條要找醫師確認。
python engineering\phase2\import_public_tissue.py --preset woundtissueseg --print-mapping
python engineering\phase2\import_public_tissue.py --preset dfutissue --print-mapping

# 0-2 產生合成資料（不是傷口，只是有已知類別區域的色塊）
python engineering\phase2\make_synthetic_tissue.py --out D:\synth_ds --n 60

# 0-3 檢查資料判讀（不需要 torch）
python engineering\phase2\train_tissue_seg.py --data D:\synth_ds --dry-run
```

**0-3 應該印出**：可用筆數、訓練／驗證切分、驗證集的傷口代碼。
若「驗證集是空的」或筆數不對，先解決那個再往下。

```powershell
# 0-4 裝 torch（約 2–3 GB，只要做一次）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install segmentation_models_pytorch opencv-python numpy

# 0-5 用合成資料跑一次完整訓練（CPU 幾分鐘）
python engineering\phase2\train_tissue_seg.py --data D:\synth_ds --epochs 8 --size 256 --out runs\smoke
```

> 有 NVIDIA 顯卡的話把 `--index-url` 換成 `https://download.pytorch.org/whl/cu121`
> （或你的 CUDA 版本），會快 10–30 倍。沒有也沒關係，這個任務 CPU 跑得完。

**0-5 應該看到**：每個 epoch 印出 loss 與逐類 Dice，最後存出 `runs\smoke\best.pt`。

⚠ **合成資料上的 Dice 沒有臨床意義。** 那個任務簡單到隨機初始化都學得會。
看到 0.9 不要高興；**看到 0.5 要擔心 —— 那代表管線有問題**。

這一步跑完，你就知道工具鏈是好的。接下來的任何問題都是資料問題。

---

## 步驟 1：下載公開資料集

### 1-1 DFUTissue（110 張，糖尿病足）

**位置**：<https://github.com/uwm-bigdata/DFUTissueSegNet>

UWM Big Data Lab 的公開 repo，同時含程式碼與資料。也可從
<https://sites.uwm.edu/bigdata/datasets/> 的資料集列表進入。

**8 個類別**：`granulation`、`callus`、`fibrin`、`necrotic`、`eschar`、
`neodermis`、`tendon`、`dressing`

### 1-2 WoundTissueSeg（147 張，6 類）

**論文**：<https://arxiv.org/abs/2502.10652>
（Kabir, Roy, Hossain, Featherston, Ahmed，2025）

**6 個類別**：`slough`、`granulation`、`maceration`、`necrosis`、`bone`、`tendon`

⚠ 這一份**沒有找到直接的下載連結**。論文本身在 arXiv 上公開，但資料的取得方式
要看論文內文的 Data Availability 段落，或直接聯絡作者
（與 DFUTissue 同一組人，先從 [uwm-bigdata](https://github.com/uwm-bigdata) 找）。

**這不是我可以代勞的一步** —— 取得授權需要一個真人回信。

### 1-3 沒拿到 WoundTissueSeg 也能開始

DFUTissue 的 110 張已經夠跑出一個基線。先用它，WoundTissueSeg 到手再 `--append` 併進去。

---

## 步驟 2：建 `classes.json`

解壓後在資料集根目錄建一個 `classes.json`，內容是**遮罩的像素值 → 類別名**：

```json
{
  "0": "background",
  "1": "granulation",
  "2": "callus",
  "3": "fibrin",
  "4": "necrotic",
  "5": "eschar",
  "6": "neodermis",
  "7": "tendon",
  "8": "dressing"
}
```

### ⚠ 這一步**絕對不能用猜的**

索引順序猜錯的話：訓練照樣跑得完、Dice 照樣有數字、圖也畫得出來 ——
**但模型學到的是完全不同的東西，而且從任何指標上都看不出來。**

正確做法：找資料集附的 `README` / `labels.txt` / 論文的類別表。
真的找不到就用這個方法確認：

```powershell
python -c "import cv2,numpy as np,sys; m=cv2.imread(sys.argv[1],cv2.IMREAD_UNCHANGED); print(np.unique(m))" D:\DFUTissue\masks\某一張.png
```

把出現的值列出來，再對照幾張影像目視確認「值 4 的區域看起來是不是壞死」。
花十分鐘做這件事，比事後發現整批標籤是錯的便宜太多。

---

## 步驟 3：匯入

```powershell
python engineering\phase2\import_public_tissue.py `
    --src D:\DFUTissue --preset dfutissue --out D:\public_ds

# WoundTissueSeg 到手後併進去（注意 --append）
python engineering\phase2\import_public_tissue.py `
    --src D:\WoundTissueSeg --preset woundtissueseg --out D:\public_ds --append
```

腳本會印出**類別像素分布**與「完全沒有的類別」。若某一類是 0%，
模型在它身上得不到任何訓練訊號 —— 而多類平均 Dice 會看起來正常。

⚠ 若出現「這些來源類別不在映射表裡」，**腳本會直接停下來**。
那是刻意的：不補的話它們會被當成背景，是一個安靜的資料損失。
請到 `import_public_tissue.py` 的 `PRESETS` 補上，**並寫下理由**。

---

## 步驟 4：訓練基線

```powershell
python engineering\phase2\train_tissue_seg.py --data D:\public_ds --dry-run
python engineering\phase2\train_tissue_seg.py --data D:\public_ds --epochs 60 --out runs\base
```

**驗收標準**（`docs/tissue_segmentation_plan.md` §6）：多類平均 Dice **≥ 0.55**。
達不到代表管線有問題，不是資料不夠 —— 已發表的最佳是 0.78。

---

## 步驟 5（之後）：加入自有臨床資料微調

等 App 收到夠多**經醫師修正**的遮罩之後：

```powershell
cd Backend\Flask
.\pull_dataset.ps1 -BaseUrl <網址> -Bucket woundai-flywheel-jackh001 -Out D:\woundai_ds -DryRun
.\pull_dataset.ps1 -BaseUrl <網址> -Bucket woundai-flywheel-jackh001 -Out D:\woundai_ds

cd ..\..
python engineering\phase2\train_tissue_seg.py `
    --data D:\woundai_ds --init runs\base\best.pt --epochs 40 --out runs\ft
```

**判斷點**：驗證 Dice 相對基線提升 **≥ 0.03** 才算自有資料有幫助。
沒提升就**不要**直接加量 —— 先查標註一致性（`analyze_interrater.py`）與影像品質。

---

## 常見卡點

| 症狀 | 原因 | 處置 |
|---|---|---|
| `No module named torch` | 沒裝或裝到別的 Python | `python -m pip install ...`，確認 `python -c "import sys;print(sys.executable)"` |
| 訓練極慢 | 裝到 CPU 版但想用 GPU | 重裝 `--index-url .../whl/cu121` |
| `驗證集是空的` | 所有樣本同一個 code | 資料太少或 code 沒分開，檢查檔名 |
| 某類 Dice 一直是 `nan` | 驗證集裡沒有那一類 | 正常。但要知道那一類**沒有被驗證到** |
| 匯入後某類 0% | 映射表沒對上該資料集的命名 | 看 `--print-mapping`，補 `PRESETS` |
| Dice 高得可疑（>0.9） | 切分洩漏，或還在跑合成資料 | 確認 `--data` 指對，檢查驗證集傷口代碼 |

---

## 參考

- [DFUTissueSegNet（程式碼與 DFUTissue 資料集）](https://github.com/uwm-bigdata/DFUTissueSegNet)
- [UWM Big Data Lab 資料集列表](https://sites.uwm.edu/bigdata/datasets/)
- [Deep Learning for Wound Tissue Segmentation（WoundTissueSeg 論文，arXiv 2502.10652）](https://arxiv.org/abs/2502.10652)
- [Prof. Ashad Kabir 的 GitHub](https://github.com/akabircs)
- [WoundFormer（同任務的較新方法，可作對照）](https://arxiv.org/html/2605.19868v1)
