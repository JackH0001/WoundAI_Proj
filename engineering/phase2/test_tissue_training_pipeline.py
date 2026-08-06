# -*- coding: utf-8 -*-
"""契約測試：組織分割的訓練資料管線（P1）。

**不需要 torch。** 這裡驗的是資料怎麼被解讀，不是模型學得多好——
而資料被解讀錯的那些方式，全部都不會讓程式報錯：

  1. **類別映射猜錯** → 訓練跑得完、Dice 有數字，但模型學到的是別的東西
  2. **切分洩漏**（同一傷口橫跨訓練/驗證）→ Dice 虛高好幾個百分點，而那個提升是假的
  3. **未經醫師修正的遮罩混進去** → 模型學會複製自己的輸出，指標漂亮而臨床表現不動
  4. **某類別完全缺席** → 多類平均 Dice 看起來正常，缺口完全看不出來
  5. **inter-rater Dice 算錯** → 一個會被拿去當「模型天花板」對外引用的數字

真正的訓練（`train_tissue_seg.py` 的 torch 部分）在有 GPU/CPU torch 的機器上跑；
這支測試鎖的是**在那之前就會決定成敗**的每一個判斷。

    python engineering/phase2/test_tissue_training_pipeline.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def load(mod):
    """直接從檔案載入，避開 phase2 目錄沒有 __init__ 的問題。"""
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    spec.loader.exec_module(m)
    return m


def main():
    import numpy as np
    import cv2

    imp = load("import_public_tissue")
    trn = load("train_tissue_seg")
    itr = load("analyze_interrater")

    tmp = tempfile.mkdtemp(prefix="woundai_pipe_")

    # ── 1 類別映射 ────────────────────────────────────────────────
    #
    # 這張表是整條管線裡最沒有防護的一環：映射錯了不會有任何徵兆。
    m = imp.PRESETS["woundtissueseg"]["map"]
    check("1  骨與肌腱歸『其他』，不是壞死",
          m["bone"][0] == 5 and m["tendon"][0] == 5, (m["bone"][0], m["tendon"][0]))
    check("1b 浸潤歸背景（那是傷口周圍皮膚，不是傷口內組織）", m["maceration"][0] == 0)
    check("1c 焦痂併入壞死（乾燥後的形態，臨床處置相同）", m["eschar"][0] == 3)
    check("1d 每一條映射都寫了理由（沒有理由的映射等於沒有映射）",
          all(isinstance(v[1], str) and len(v[1]) >= 4 for v in m.values()),
          [k for k, v in m.items() if not v[1]])
    check("1e 五類全部都有來源可對應（缺哪一類就學不會哪一類）",
          {1, 2, 3, 4, 5} <= {v[0] for v in m.values()},
          sorted({v[0] for v in m.values()}))

    # 來源有沒認識的類別 → 必須**硬失敗**，不可默默當背景
    lut, missing = imp.build_lut("woundtissueseg",
                                 {"0": "background", "1": "granulation", "7": "hypergranulation"})
    check("1f 未知類別會被指出來而不是靜默丟掉",
          missing == [("7", "hypergranulation")], missing)
    lut2, miss2 = imp.build_lut("woundtissueseg", {"0": "background", "3": "TENDON  "})
    check("1g 大小寫與空白不影響比對（來源命名不一致是常態）",
          not miss2 and lut2[3] == 5, (lut2, miss2))

    # 端到端：造一個假的來源資料集，跑匯入，檢查像素值真的被換過
    src = os.path.join(tmp, "src")
    os.makedirs(os.path.join(src, "images")); os.makedirs(os.path.join(src, "masks"))
    json.dump({"0": "background", "1": "granulation", "2": "bone", "3": "maceration"},
              open(os.path.join(src, "classes.json"), "w"))
    lab = np.zeros((20, 20), np.uint8)
    lab[2:8, 2:8] = 1        # granulation → 1
    lab[10:16, 2:8] = 2      # bone        → 5
    lab[10:16, 10:16] = 3    # maceration  → 0
    cv2.imwrite(os.path.join(src, "images", "a.png"), np.full((20, 20, 3), 120, np.uint8))
    cv2.imwrite(os.path.join(src, "masks", "a.png"), lab)
    out = os.path.join(tmp, "pub")
    rc = imp.main.__globals__["sys"].argv
    sys.argv = ["x", "--preset", "woundtissueseg", "--src", src, "--out", out]
    imp.main()
    sys.argv = rc
    got = cv2.imread(os.path.join(out, "masks", os.listdir(os.path.join(out, "masks"))[0]),
                     cv2.IMREAD_COLOR)[:, :, 2]
    check("1h 匯入後像素值真的被映射（肉芽 1、骨→其他 5、浸潤→背景 0）",
          got[4, 4] == 1 and got[12, 4] == 5 and got[12, 12] == 0,
          (int(got[4, 4]), int(got[12, 4]), int(got[12, 12])))

    # ── 2 切分洩漏 ────────────────────────────────────────────────
    #
    # 同一個傷口的不同回診照片極為相似。落在訓練與驗證兩邊，模型只要記住
    # 那個傷口長什麼樣就能拿高分——而那個 Dice 不代表它在新病患身上的表現。
    items = [(("/x/WD-%s__%d.jpg" % (c, i)), "/m", {}) for c in "ABCDEFGHIJ" for i in range(3)]
    tr, va, vc = trn.split_by_patient(items, val_frac=0.2, seed=1)
    tr_codes = {os.path.basename(i[0]).split("__")[0] for i in tr}
    va_codes = {os.path.basename(i[0]).split("__")[0] for i in va}
    check("2  訓練與驗證的傷口代碼完全不重疊", not (tr_codes & va_codes), tr_codes & va_codes)
    check("2b 同一傷口的多張照片一定同組（否則就是洩漏）",
          all(len({os.path.basename(i[0]).split("__")[0] for i in grp}) ==
              len({os.path.basename(i[0]).split("__")[0] for i in grp})
              for grp in (tr, va)) and len(va) % 3 == 0, (len(tr), len(va)))
    check("2c 切分可重現（同 seed 同結果，否則兩次訓練無從比較）",
          trn.split_by_patient(items, val_frac=0.2, seed=1)[2] == vc)
    check("2d 驗證集不會是空的（否則訓出來的數字無法解讀）", len(va) > 0)

    # ── 3 未經醫師修正的遮罩 ──────────────────────────────────────
    ds = os.path.join(tmp, "ds")
    os.makedirs(os.path.join(ds, "images")); os.makedirs(os.path.join(ds, "masks"))
    man = {"items": []}
    for code, iid, edited in [("WD-A", "aa", True), ("WD-A", "bb", True),
                              ("WD-B", "cc", True), ("WD-C", "dd", False)]:
        stem = "%s__%s" % (code, iid)
        cv2.imwrite(os.path.join(ds, "images", stem + ".jpg"), np.full((8, 8, 3), 100, np.uint8))
        lab = np.zeros((8, 8), np.uint8); lab[1:5, 1:5] = 1
        cv2.imwrite(os.path.join(ds, "masks", stem + ".png"),
                    np.dstack([np.zeros_like(lab), np.zeros_like(lab), lab]))
        man["items"].append({"code": code, "image_id": iid, "tissue_edited": edited})
    json.dump(man, open(os.path.join(ds, "manifest.json"), "w", encoding="utf-8"))

    got, skipped = trn.collect(ds, require_edited=True)
    check("3  未經醫師修正者被排除（否則模型在複製自己的輸出）",
          len(got) == 3 and skipped["未經醫師修正"] == 1, (len(got), dict(skipped)))
    got_all, _ = trn.collect(ds, require_edited=False)
    check("3b 明確要求時才納入", len(got_all) == 4, len(got_all))
    check("3c 排除的原因會被計數（不能靜默消失）", "未經醫師修正" in skipped)

    # 缺遮罩檔的也要被算進去，不是安靜跳過
    os.remove(os.path.join(ds, "masks", "WD-B__cc.png"))
    got2, sk2 = trn.collect(ds, require_edited=True)
    check("3d 缺遮罩檔會被計數", sk2["無遮罩"] == 1 and len(got2) == 2, (dict(sk2), len(got2)))

    # ── 4 inter-rater Dice 的算法 ─────────────────────────────────
    #
    # 這個數字會被當成「模型表現的天花板」對外引用，算錯不會有徵兆。
    a = np.zeros((10, 10), np.uint8); a[0:6, 0:10] = 1      # A：60 px 肉芽
    b = np.zeros((10, 10), np.uint8); b[0:4, 0:10] = 1      # B：40 px 肉芽
    b[4:6, 0:10] = 2                                        # 兩人分歧的 20 px：A 說肉芽 B 說腐肉
    inter, union, agree, total, conf = itr.pair_stats(a, b)
    # Dice = 2·40 / (60+40) = 0.8
    check("4  逐類 Dice 正確", abs(2 * inter[1] / union[1] - 0.8) < 1e-9,
          2 * inter[1] / union[1])
    check("4b 雙方都留白（0）的像素不計入有效範圍", total == 60, total)
    check("4c 同意像素數正確", agree == 40, agree)
    check("4d 混淆矩陣抓得到『A 說肉芽時 B 說腐肉』的 20 px",
          conf[1, 2] == 20 and conf[1, 1] == 40, (int(conf[1, 2]), int(conf[1, 1])))
    check("4e 完全一致 → Dice 1.0",
          abs(2 * itr.pair_stats(a, a)[0][1] / itr.pair_stats(a, a)[1][1] - 1.0) < 1e-9)
    z = np.zeros((10, 10), np.uint8)
    check("4f 兩人都留白 → 有效像素 0（不可算成完美一致）", itr.pair_stats(z, z)[3] == 0)

    # ── 5 類別缺席要看得見 ────────────────────────────────────────
    #
    # 訓練集若完全沒有某一類，模型不可能學會它；而驗證集若也沒有，
    # 多類平均 Dice 會看起來正常——那個缺口從指標上完全看不出來。
    cnt = np.zeros(trn.N_CLASS, dtype=np.int64)
    for _, mp, _ in got_all:
        mm = cv2.imread(mp, cv2.IMREAD_COLOR)
        if mm is None:
            continue
        cnt += np.bincount(mm[:, :, 2].ravel(), minlength=trn.N_CLASS)[:trn.N_CLASS]
    absent = [trn.CLASSES[i] for i in range(1, trn.N_CLASS) if cnt[i] == 0]
    check("5  合成資料只有肉芽 → 其餘四類都應被判定為缺席",
          set(absent) == {"腐肉", "壞死", "上皮", "其他"}, absent)
    check("5b 類別權重用倒數平方根，且背景權重為 0（傷口外不是要學的東西）",
          "np.sqrt" in open(os.path.join(HERE, "train_tissue_seg.py"), encoding="utf-8").read()
          and "w[0] = 0.0" in open(os.path.join(HERE, "train_tissue_seg.py"), encoding="utf-8").read())

    # ── 6 遮罩必須無損 ────────────────────────────────────────────
    check("6  遮罩以 INTER_NEAREST 縮放（類別值不可內插——『3.7 類』沒有意義）",
          "INTER_NEAREST" in open(os.path.join(HERE, "train_tissue_seg.py"), encoding="utf-8").read())

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：類別映射明確且未知類別硬失敗、切分依傷口無洩漏、"
          "未修正遮罩被排除且計數、inter-rater Dice 算法正確、類別缺席看得見。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
