#!/usr/bin/env python3
"""
Android ↔ iOS 平台一致性檢查。

## 為什麼需要這支

兩端各自開發時，漂移**不會有任何徵兆**。這個專案已經實際發生過三次：

  1. iOS 的預設後端網址抄自已廢棄的 service → 症狀是「登入失敗」，訊息卻叫人去查帳密
  2. `Pipeline/` 8 個檔沒被加進 Xcode 專案 → 躺三個月，git diff 看起來一切正常
  3. Android 加了 `wound_polygons`（多處傷口）而 iOS 沒接 → 第二個傷口在修邊畫面
     沒有初始輪廓，醫師沒注意的話那個傷口在訓練集裡會被標成背景

第 3 類最危險：**沒有錯誤、沒有警告，只有訓練資料悄悄變錯**。人工比對抓不到，
因為要比的是「後端回傳的每一個鍵，兩端是不是都讀了」。

## 檢查項目

  A. 後端端點路徑：兩端呼叫的集合必須相同
  B. classify 回應欄位：兩端讀取的 JSON 鍵必須相同（漏讀＝功能靜默消失）
  C. annotation 請求欄位：兩端送出的 JSON 鍵必須相同（漏送＝後端守門擋下或資料不全）
  D. 預設後端網址
  E. 版本號對應關係
  F. SSOT 常數（由 verify_logic.py 負責，這裡不重複）

## 誠實邊界

這支比的是**契約層面**，不是功能等價。兩端都讀了 `wound_polygons` 不代表都正確地
用它畫出第二個輪廓——那要靠 `docs/PARITY.md` 的人工宣告與實機測試。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KT = os.path.join(ROOT, 'Android/app/src/main/java/com/woundmeasurement/app/pipeline/BackendClient.kt')
SW = os.path.join(ROOT, 'iOS/WoundMeasurementApp/Core/BackendClient.swift')

problems = []

# ── 已宣告的差異 ──────────────────────────────────────────────────────────
# 從 docs/PARITY.md 讀。**刻意的不對稱要用宣告處理，不是在程式裡寫死白名單**——
# 寫死的話，差異就從「有人決定過」變成「沒有人記得」。
def load_declared():
    p = os.path.join(ROOT, 'docs/PARITY.md')
    if not os.path.exists(p):
        return {}
    src = open(p, encoding='utf-8').read()
    m = re.search(r'```yaml(.*?)```', src, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).split('\n'):
        mm = re.match(r'\s{2,}(\w+):\s*\[(.*?)\]', line)
        if mm:
            out[mm.group(1)] = {x.strip() for x in mm.group(2).split(',') if x.strip()}
    return out


DECLARED = load_declared()


def note(kind, detail, why=None):
    """已宣告的差異不計為問題，但仍列出來讓人看得見。"""
    if detail in DECLARED.get(kind, set()):
        declared.append({'kind': kind, 'detail': detail})
        return
    problems.append({'kind': kind, 'detail': detail, **({'why': why} if why else {})})


declared = []


def strip_comments(src):
    """剝掉註解。

    ⚠ 沒有這一步的話，寫在文件註解裡的端點表會被當成「實際呼叫的端點」。
    實測：`BackendClient.swift` 的檔頭列了端點表、又列了「舊版 iOS 打的 404 路徑」
    當警告，於是比對器報出 5 個假的「Android 缺少端點」。
    """
    out = []
    i, n = 0, len(src)
    line_c = block_c = in_str = False
    depth = 0
    while i < n:
        c, nx = src[i], (src[i + 1] if i + 1 < n else '')
        if line_c:
            if c == '\n':
                line_c = False; out.append(c)
            else:
                out.append(' ')
        elif block_c:
            if c == '/' and nx == '*': depth += 1; out.append('  '); i += 2; continue
            if c == '*' and nx == '/':
                depth -= 1
                if depth == 0: block_c = False
                out.append('  '); i += 2; continue
            out.append('\n' if c == '\n' else ' ')
        elif in_str:
            if c == '\\': out.append('  '); i += 2; continue
            if c == '"': in_str = False
            out.append(c)
        else:
            if c == '/' and nx == '/': line_c = True; out.append('  '); i += 2; continue
            if c == '/' and nx == '*': block_c = True; depth = 1; out.append('  '); i += 2; continue
            if c == '"': in_str = True
            out.append(c)
        i += 1
    return ''.join(out)


def read(p, strip=True):
    if not os.path.exists(p):
        problems.append({'kind': 'missing_file', 'file': os.path.relpath(p, ROOT)})
        return ''
    src = open(p, encoding='utf-8', errors='replace').read()
    return strip_comments(src) if strip else src


kt, sw = read(KT), read(SW)


# ── A. 端點路徑 ───────────────────────────────────────────────────────────
# Kotlin 用字串內插（"$baseUrl/api/..."），Swift 用字面值（"/api/..."）。
# 兩種寫法都要抓得到，否則會得到「Android 一個端點都沒有」這種假結果。
def endpoints(src):
    return set(re.findall(r'/api/v\d+/[a-zA-Z0-9/_-]+|/api/(?!v\d)[a-zA-Z0-9/_-]+', src))


ep_kt, ep_sw = endpoints(kt), endpoints(sw)
for e in sorted(ep_kt - ep_sw):
    note('endpoint_missing_ios', e)
for e in sorted(ep_sw - ep_kt):
    note('endpoint_missing_android', e)


# ── B. classify 回應欄位 ──────────────────────────────────────────────────
# Android: s2.getString("model") / j.optBoolean("phantom_hint", false) / isNull("x")
# iOS:     s2["model"].string    / j["phantom_hint"].bool(false)
KT_READ = re.compile(r'\.(?:get|opt|isNull)(?:JSONObject|JSONArray|String|Int|Double|Boolean)?'
                     r'\(\s*"([a-z0-9_]+)"')
SW_READ = re.compile(r'\[\s*"([a-z0-9_]+)"\s*\]')

# 只比對 classify 解析區塊，避免把 annotation 的送出欄位混進來。
def slice_between(src, start_pat, end_pat):
    m = re.search(start_pat, src)
    if not m:
        return ''
    rest = src[m.end():]
    e = re.search(end_pat, rest)
    return rest[:e.start()] if e else rest


kt_classify = slice_between(kt, r'fun classify\(', r'\n    fun submitAnnotation')
# ⚠ 結束錨點不能用 `// MARK:`——註解已在 strip_comments 被剝掉，
#   錨點找不到就一路切到檔尾，把 annotation 的欄位混進 classify 的集合。
sw_classify = slice_between(sw, r'static func parseClassify\(', r'\n    func submitAnnotation')

keys_kt = set(KT_READ.findall(kt_classify))
keys_sw = set(SW_READ.findall(sw_classify))

# 這些鍵 iOS 刻意不讀（Android 也沒讀，或屬於別的用途），列白名單避免雜訊
IGNORE = {'stats', 'by_source', 'issues', 'error', 'note', 'status', 'code', 'access_token',
          'identity', 'org', 'user', 'role', 'role_zh', 'display_name', 'perms'}

for k in sorted(keys_kt - keys_sw - IGNORE):
    note('classify_key_missing_ios', k, 'Android 讀了這個鍵、iOS 沒讀 → 該功能在 iOS 上靜默消失')
for k in sorted(keys_sw - keys_kt - IGNORE):
    note('classify_key_missing_android', k)


# ── C. annotation 請求欄位 ────────────────────────────────────────────────
kt_submit = slice_between(kt, r'fun submitAnnotation\(', r'\n    private fun summarize')
sw_submit = slice_between(sw, r'func submitAnnotation\(', r'\n    func withdrawConsent')

# Kotlin 送出欄位有三種寫法：obj.put("k",…)、鏈式 .put("k",…)、
# 以及巢狀 JSONObject().apply { put("k",…) }（tissue_raster 就是這種）。
put_kt = set(re.findall(r'\bput\(\s*"([a-z0-9_]+)"', kt_submit))
put_sw = set(re.findall(r'"([a-z0-9_]+)"\s*:', sw_submit))
put_sw |= set(re.findall(r'obj\["([a-z0-9_]+)"\]', sw_submit))

for k in sorted(put_kt - put_sw):
    note('annotation_field_missing_ios', k, '兩端送出的欄位不同 → 訓練資料的溯源欄位會缺一半')
for k in sorted(put_sw - put_kt):
    note('annotation_field_missing_android', k)


# ── D. 預設後端網址 ───────────────────────────────────────────────────────
g = os.path.join(ROOT, 'Android/app/build.gradle')
ios_url = re.search(r'return "(https://[^"]+run\.app)"',
                    read(os.path.join(ROOT, 'iOS/WoundMeasurementApp/Core/AppSettings.swift')))
and_url = re.search(r'DEFAULT_BACKEND_URL",\s*\n?\s*\'"(https://[^"]+run\.app)"\'', read(g)) if os.path.exists(g) else None
if ios_url and and_url and ios_url.group(1) != and_url.group(1):
    problems.append({'kind': 'backend_url_mismatch',
                     'detail': f'iOS={ios_url.group(1)} Android={and_url.group(1)}'})


# ── E. 版本號 ─────────────────────────────────────────────────────────────
vp = os.path.join(ROOT, 'Android/version.properties')
py = os.path.join(ROOT, 'iOS/project.yml')
av = re.search(r'^versionCode=(\d+)', read(vp), re.M) if os.path.exists(vp) else None
iv = re.search(r'CURRENT_PROJECT_VERSION:\s*"?(\d+)"?', read(py)) if os.path.exists(py) else None
if av and iv and av.group(1) != iv.group(1):
    problems.append({'kind': 'version_mismatch',
                     'detail': f'Android versionCode={av.group(1)} vs iOS CURRENT_PROJECT_VERSION={iv.group(1)}',
                     'why': '排錯的第一個問題永遠是「你裝的是哪一版」，兩邊版號要能互相對照'})


# ── 輸出 ──────────────────────────────────────────────────────────────────
by_kind = {}
for p in problems:
    by_kind.setdefault(p['kind'], []).append(p)

print(f"Android 端點 {len(ep_kt)} 個 / iOS 端點 {len(ep_sw)} 個")
print(f"classify 讀取鍵：Android {len(keys_kt)} / iOS {len(keys_sw)}")
print(f"annotation 送出欄位：Android {len(put_kt)} / iOS {len(put_sw)}")
print(f"\n落差 {len(problems)} 項")
for kind, items in sorted(by_kind.items()):
    print(f"\n[{kind}] {len(items)} 項")
    for it in items:
        line = f"  - {it.get('detail', it.get('file',''))}"
        if it.get('why'):
            line += f"\n      {it['why']}"
        print(line)

if declared:
    print(f"\n已宣告的差異 {len(declared)} 項（見 docs/PARITY.md，不計為落差）")
    for d in declared:
        print(f"  · [{d['kind']}] {d['detail']}")

if not problems:
    print("\n✅ 契約層面完全一致（未宣告的落差為 0）")
sys.exit(1 if problems else 0)
