# -*- coding: utf-8 -*-
"""影像讀寫：**路徑含非 ASCII 字元時 cv2.imread/imwrite 會失敗。**

## 為什麼需要這個檔

Windows 上 `cv2.imread` 內部走 `fopen`，用的是系統 ANSI 字碼頁（繁中是 Big5）。
路徑含中文時開檔失敗，函式**回 `None`**——不丟例外。

實測（2026-08-06）：

```
C:\\dev\\WoundAI_weights_archive\\雲端 AI 模型訓練及分析服務\\DFUTissueSegNet-main\\...
[WARN] cv::findDecoder imread_(...): can't open/read file
AttributeError: 'NoneType' object has no attribute 'shape'
```

而本專案的匯入腳本原本寫的是：

```python
if img is None or m is None:
    continue          # ← 整批靜默跳過
```

於是把資料集放在有中文的資料夾裡，會得到「匯入 0 張」而**沒有任何錯誤**——
使用者只會覺得「腳本壞了」或「資料集格式不對」，而真正的原因是路徑編碼。
這與本專案反覆遇到的失敗形狀完全相同：沒有錯誤、沒有警告、結果是空的。

## 解法

用 Python 自己開檔讀成位元組，再交給 `cv2.imdecode`／`cv2.imencode`。
Python 的 `open()` 走 Windows 的寬字元 API，不受字碼頁影響。
"""
import os


def imread_any(path, flags=None):
    """讀影像。路徑可含任何字元。讀不到回 None（與 cv2.imread 相同語意）。"""
    import cv2
    import numpy as np
    if flags is None:
        flags = cv2.IMREAD_COLOR
    try:
        with open(path, "rb") as f:
            buf = np.frombuffer(f.read(), dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, flags)
    except Exception:
        return None


def imwrite_any(path, img):
    """寫影像。副檔名決定格式。回 True/False。"""
    import cv2
    try:
        ext = os.path.splitext(path)[1] or ".png"
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(buf.tobytes())
        return True
    except Exception:
        return False


def find_pairs(images_dir, masks_dir, exts=(".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg")):
    """遞迴配對 images/ 與 masks/ 下同名（不含副檔名）的檔案。

    公開資料集常把 train/val/test 分成子目錄
    （例如 DFUTissue：`Labeled/Original/{Images,Annotations}/{Train,Val,Test}`），
    只掃第一層會一張都找不到——而那看起來就像「資料集是空的」。
    """
    def index(root):
        out = {}
        for dirpath, _, files in os.walk(root):
            for fn in files:
                stem, ext = os.path.splitext(fn)
                if ext.lower() in exts:
                    # 同名檔出現在多個子目錄時保留第一個，並記下來源子目錄，
                    # 讓呼叫端能把 train/val/test 的來源標進 manifest。
                    key = stem
                    if key not in out:
                        out[key] = os.path.join(dirpath, fn)
        return out

    im = index(images_dir)
    mk = index(masks_dir)
    common = sorted(set(im) & set(mk))
    return [(k, im[k], mk[k]) for k in common], len(im), len(mk)
