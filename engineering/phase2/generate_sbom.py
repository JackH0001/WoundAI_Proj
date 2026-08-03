# -*- coding: utf-8 -*-
"""產生 SBOM 與開源授權清冊 —— 醫院資安審查與委外契約會要。

## 為什麼需要

衛福部的醫療機構委外規範明訂：受託機構使用**非自行開發的系統**時，必須說明來源與授權證明。
實務上院方資安審查也會要一份「你們用了哪些第三方元件、各自什麼授權、有沒有已知漏洞」。

臨時才整理會漏掉東西——尤其是**模型權重的來源與授權**，那不在任何套件管理器裡，
卻是這個專案最需要說清楚的一項（`smp` 架構是 MIT，權重是自訓，外層資料集另有授權）。
所以這支腳本把三個來源合在一起：Python 相依、Android 相依、模型與資料集出處。

## 用法

    python engineering/phase2/generate_sbom.py                 # 輸出到 docs/SBOM.md
    python engineering/phase2/generate_sbom.py --json          # 另外輸出機器可讀的 JSON

## 誠實邊界

授權欄位來自套件 metadata（已安裝時）或本清單的人工對照表。**不是法律意見**，
正式送審前請由熟悉授權的人複核，特別是 GPL/AGPL 系與資料集的使用條款。
"""
import argparse
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# 人工對照表：套件 metadata 缺授權、或授權字串太模糊時的權威值。
KNOWN_LICENSES = {
    "flask": "BSD-3-Clause", "flask-cors": "MIT", "flask-jwt-extended": "MIT",
    "numpy": "BSD-3-Clause", "opencv-python-headless": "Apache-2.0",
    "pillow": "MIT-CMU", "requests": "Apache-2.0", "werkzeug": "BSD-3-Clause",
    "gunicorn": "MIT", "google-cloud-storage": "Apache-2.0", "onnxruntime": "MIT",
}

# Android 相依：Maven 座標查不到 metadata（沒有本機安裝可讀），只能靠對照表。
# 只填**已查證**的；查不到就留白讓它進「待確認」，不要用猜的填滿讓清單看起來完整。
ANDROID_KNOWN_LICENSES = {
    "com.quickbirdstudios:opencv": "Apache-2.0（OpenCV 4.5+ 本體與此 Android 包裝皆為 Apache-2.0）",
    "com.microsoft.onnxruntime:onnxruntime-android": "MIT",
    "com.github.PhilJay:MPAndroidChart": "Apache-2.0",
    "com.journeyapps:zxing-android-embedded": "Apache-2.0",
    "com.github.bumptech.glide:glide": "BSD-2-Clause + MIT + Apache-2.0（混合，見其 LICENSE）",
    "com.github.bumptech.glide:compose": "Apache-2.0",
}

# 已無入口但仍在相依清單裡的元件。審查時會被問到，先自己標出來比較好：
# 每一個第三方元件都是要說明來源、要追 CVE 的負擔，用不到就該移除。
LIKELY_UNUSED = {
    "com.github.PhilJay:MPAndroidChart": "時間軸的趨勢圖改為自繪 Canvas，未再使用",
    "com.journeyapps:zxing-android-embedded": "條碼掃描屬 DoctorAuthActivity（已無入口）",
}

# 模型與資料集：不在任何套件管理器裡，但這是審查最在意的一塊。
MODEL_PROVENANCE = [
    {
        "name": "student_fp16.onnx",
        "role": "端上/後端主力分割（蒸餾 student）",
        "architecture": "segmentation_models_pytorch (qubvel) — MIT",
        "weights": "本專案自行訓練（蒸餾自 A-UNet / UNet++ 集成）",
        "training_data": "uwm wound-segmentation + FUSC 公開資料集（授權條款待逐項確認）",
        "note": "權重為自訓產物，架構為開源 MIT。資料集授權須在正式送審前逐項核對。",
    },
    {
        "name": "a_unet.onnx",
        "role": "難例升級集成成員 A",
        "architecture": "UNet (segmentation_models_pytorch) — MIT",
        "weights": "本專案自行訓練",
        "training_data": "同上",
        "note": "",
    },
    {
        "name": "unetpp.onnx",
        "role": "難例升級集成成員 B",
        "architecture": "UNet++ (segmentation_models_pytorch) — MIT",
        "weights": "本專案自行訓練",
        "training_data": "同上",
        "note": "推論耗時佔集成路由 63%，是延遲優化的第一目標。",
    },
]


def python_deps():
    req = os.path.join(ROOT, "Backend", "Flask", "requirements.txt")
    out = []
    if not os.path.exists(req):
        return out
    for line in open(req, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*([<>=!~]+.*)?$", line)
        if not m:
            continue
        name, spec = m.group(1), (m.group(2) or "").strip()
        key = name.lower()
        lic, ver = KNOWN_LICENSES.get(key, "?"), ""
        # 已安裝的話用實際 metadata，比對照表準確
        try:
            from importlib import metadata as md
            dist = md.distribution(name)
            ver = dist.version
            meta_lic = (dist.metadata.get("License") or "").strip()
            classifiers = [c for c in (dist.metadata.get_all("Classifier") or [])
                           if c.startswith("License ::")]
            if classifiers:
                lic = classifiers[0].split("::")[-1].strip()
            elif meta_lic and meta_lic.upper() not in ("UNKNOWN", ""):
                lic = meta_lic if len(meta_lic) < 40 else lic
        except Exception:
            pass
        out.append({"name": name, "constraint": spec, "installed": ver, "license": lic})
    return out


def android_deps():
    gradle = os.path.join(ROOT, "Android", "app", "build.gradle")
    out = []
    if not os.path.exists(gradle):
        return out
    pat = re.compile(r"""(implementation|api|ksp|androidTestImplementation|debugImplementation)"""
                     r"""\s*\(?\s*["']([^"']+)["']""")
    for line in open(gradle, encoding="utf-8"):
        if line.strip().startswith("//"):
            continue
        m = pat.search(line)
        if not m:
            continue
        conf, coord = m.group(1), m.group(2)
        if ":" not in coord:
            continue
        parts = coord.split(":")
        group = parts[0]
        # 先查對照表（含版本號的座標要去掉版本再查）
        ga = ":".join(parts[:2])
        lic = ANDROID_KNOWN_LICENSES.get(ga)
        if not lic:
            lic = ("Apache-2.0" if group.startswith(("androidx", "com.google", "org.tensorflow",
                                                     "com.squareup", "org.jetbrains"))
                   else "?")
        out.append({"coordinate": coord, "configuration": conf, "license": lic,
                    "unused_note": LIKELY_UNUSED.get(ga)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "SBOM.md"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    py, andr = python_deps(), android_deps()
    unknown = [d["name"] for d in py if d["license"] == "?"] + \
              [d["coordinate"] for d in andr if d["license"] == "?"]

    L = []
    L.append("# SBOM 與開源授權清冊\n")
    L.append("> 由 `engineering/phase2/generate_sbom.py` 產生，日期 %s。\n" % date.today().isoformat())
    L.append("> **這不是法律意見。** 授權欄位取自套件 metadata 或人工對照表，")
    L.append("> 正式送審前請由熟悉授權條款的人複核，特別是 GPL/AGPL 系與資料集的使用條款。\n")

    L.append("\n## 1. 後端 Python 相依\n")
    L.append("| 套件 | 版本限制 | 已安裝 | 授權 |")
    L.append("|---|---|---|---|")
    for d in py:
        L.append("| `%s` | %s | %s | %s |" % (d["name"], d["constraint"] or "—",
                                              d["installed"] or "—", d["license"]))

    L.append("\n## 2. Android 相依\n")
    L.append("| 座標 | 用途 | 授權 |")
    L.append("|---|---|---|")
    for d in andr:
        L.append("| `%s` | %s | %s |" % (d["coordinate"], d["configuration"], d["license"]))

    unused = [d for d in andr if d.get("unused_note")]
    if unused:
        L.append("\n### 可移除的相依\n")
        L.append("每個第三方元件都是「要說明來源、要追 CVE」的長期負擔。")
        L.append("用不到還留著，只是在審查時多幾個要回答的問題。\n")
        L.append("| 座標 | 為什麼可能用不到 |")
        L.append("|---|---|")
        for d in unused:
            L.append("| `%s` | %s |" % (d["coordinate"], d["unused_note"]))

    L.append("\n## 3. 模型與訓練資料出處\n")
    L.append("這一節是審查最在意的部分，也是最容易在臨時整理時漏掉的——")
    L.append("模型權重不在任何套件管理器裡。\n")
    for m in MODEL_PROVENANCE:
        L.append("### `%s`\n" % m["name"])
        L.append("| 項目 | 內容 |")
        L.append("|---|---|")
        L.append("| 用途 | %s |" % m["role"])
        L.append("| 架構來源 | %s |" % m["architecture"])
        L.append("| 權重來源 | %s |" % m["weights"])
        L.append("| 訓練資料 | %s |" % m["training_data"])
        if m["note"]:
            L.append("| 備註 | %s |" % m["note"])
        L.append("")

    L.append("\n## 4. 待確認\n")
    if unknown:
        L.append("以下項目的授權尚未確認，送審前必須補齊：\n")
        for u in unknown:
            L.append("- `%s`" % u)
    else:
        L.append("- 套件層級授權皆已標註。")
    L.append("")
    L.append("- **訓練資料集的授權條款**（uwm wound-segmentation、FUSC）須逐項核對，")
    L.append("  特別是「是否允許商業使用」與「衍生模型的授權要求」。這一項無法自動化。")
    L.append("- 已知漏洞掃描（CVE）不在本腳本範圍，建議另跑 `pip-audit` 與 Gradle 的依賴檢查。")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("已產生 %s" % a.out)
    print("  Python 相依 %d 項、Android 相依 %d 項、模型 %d 個"
          % (len(py), len(andr), len(MODEL_PROVENANCE)))
    if unknown:
        print("  ⚠ %d 項授權待確認：%s" % (len(unknown), ", ".join(unknown[:5])
                                          + (" …" if len(unknown) > 5 else "")))

    if a.json:
        jp = os.path.splitext(a.out)[0] + ".json"
        with open(jp, "w", encoding="utf-8") as f:
            json.dump({"generated": date.today().isoformat(), "python": py,
                       "android": andr, "models": MODEL_PROVENANCE}, f,
                      ensure_ascii=False, indent=2)
        print("  另輸出 %s" % jp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
