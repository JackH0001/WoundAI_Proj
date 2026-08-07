#!/usr/bin/env python3
"""
Swift 靜態稽核（無編譯器可用時的替代驗證）。

沙箱與使用者的 Windows 機器都沒有 Swift 工具鏈，download.swift.org 也不可達，
所以無法 `swiftc -typecheck`。這支腳本補上三項**編譯器一定會抓、而人眼常漏**的檢查：

  1. 頂層型別／全域符號重複宣告（同一 module 內衝突 → redeclaration 錯誤）
  2. 引用了不存在的頂層符號（例如 Pipeline/ 那批檔案引用不存在的 `Preproc`）
  3. 括號／引號配對

它**不是**型別檢查器：方法簽名、泛型、可選性、協定符合度都驗不到。
最終仍必須在 macOS 上跑一次 `xcodebuild`。這裡的價值是在送去 Mac 之前就把
「整批檔案沒被加進專案」「符號打架」這類低階錯誤清乾淨。
"""
import re
import sys
import os
import json
from collections import defaultdict

# 頂層宣告：class / struct / enum / protocol / actor / extension / typealias / func / let / var
DECL_RE = re.compile(
    r'^\s*(?:@\w+(?:\([^)]*\))?\s+)*'
    r'(?:public\s+|internal\s+|private\s+|fileprivate\s+|open\s+|final\s+|indirect\s+)*'
    r'(class|struct|enum|protocol|actor|typealias)\s+([A-Za-z_]\w*)'
)
GLOBAL_FUNC_RE = re.compile(
    r'^(?:public\s+|internal\s+|private\s+|fileprivate\s+)*func\s+([A-Za-z_]\w*)')

def strip_noise(src):
    """去掉字串字面值、註解，避免它們裡的字被當成程式碼。"""
    out = []
    i, n = 0, len(src)
    in_line_c = in_block_c = in_str = False
    depth_block = 0
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ''
        if in_line_c:
            if c == '\n':
                in_line_c = False
                out.append(c)
            else:
                out.append(' ')
        elif in_block_c:
            if c == '/' and nxt == '*':
                depth_block += 1; out.append('  '); i += 2; continue
            if c == '*' and nxt == '/':
                depth_block -= 1
                if depth_block == 0: in_block_c = False
                out.append('  '); i += 2; continue
            out.append('\n' if c == '\n' else ' ')
        elif in_str:
            if c == '\\':
                out.append('  '); i += 2; continue
            if c == '"':
                in_str = False
            out.append(' ' if c != '\n' else c)
        else:
            if c == '/' and nxt == '/':
                in_line_c = True; out.append('  '); i += 2; continue
            if c == '/' and nxt == '*':
                in_block_c = True; depth_block = 1; out.append('  '); i += 2; continue
            if c == '"':
                in_str = True; out.append(' ')
            else:
                out.append(c)
        i += 1
    return ''.join(out)


def scan(paths):
    decls = defaultdict(list)       # name -> [(file, kind, line)]  頂層
    nested = defaultdict(list)      # 巢狀型別，只用來認符號
    exts = defaultdict(list)
    files = {}
    for p in paths:
        with open(p, encoding='utf-8', errors='replace') as f:
            raw = f.read()
        clean = strip_noise(raw)
        files[p] = clean
        for ln, line in enumerate(clean.split('\n'), 1):
            m = DECL_RE.match(line)
            if m:
                kind, name = m.group(1), m.group(2)
                if kind == 'extension':
                    exts[name].append((p, ln))
                elif not line.startswith((' ', '\t')):
                    decls[name].append((p, kind, ln))   # 頂層 → 參與重複宣告檢查
                else:
                    nested[name].append((p, kind, ln))  # 巢狀 → 只計入「已知符號」
            m2 = GLOBAL_FUNC_RE.match(line)
            if m2 and not line.startswith((' ', '\t')):
                decls[m2.group(1)].append((p, 'func', ln))
    return decls, exts, files, nested


def check_unterminated_comments(paths):
    """
    Swift 的區塊註解**會巢狀**：`/* /* */ */` 是合法的。

    後果是——寫在文件註解裡的一個 `/*`（例如提到 `openapi/*.yaml` 這種路徑萬用字元）
    會開啟一層巢狀註解，於是第一個 `*/` 只關掉內層，外層一路吞到檔尾，
    整個檔案變成註解。實測就這樣讓 `BackendClient.swift` 673 行全部消失。

    編譯器會報 unterminated comment，但那是在 Mac 上才看得到。這裡先抓出來。
    """
    bad = []
    for p in paths:
        src = open(p, encoding='utf-8', errors='replace').read()
        depth = 0; i = 0; n = len(src); in_line = in_str = False
        for ln, line in enumerate(src.split('\n'), 1):
            j = 0
            while j < len(line) - 1:
                two = line[j:j+2]
                if depth == 0 and two == '//':
                    break
                if two == '/*':
                    depth += 1; j += 2; continue
                if two == '*/':
                    depth -= 1; j += 2; continue
                j += 1
        if depth != 0:
            bad.append((p, depth))
    return bad


def check_braces(files):
    bad = []
    for p, clean in files.items():
        for open_c, close_c, label in [('{', '}', 'brace'), ('(', ')', 'paren'), ('[', ']', 'bracket')]:
            d = clean.count(open_c) - clean.count(close_c)
            if d != 0:
                bad.append((p, label, d))
    return bad


SWIFT_STDLIB = set("""
String Int Double Float Bool Data Date URL UUID Array Dictionary Set Optional Result Error
Character Substring Range ClosedRange Sequence Collection Comparable Equatable Hashable Codable
Encodable Decodable Identifiable CustomStringConvertible LocalizedError Void Any AnyObject Never
Int8 Int16 Int32 Int64 UInt UInt8 UInt16 UInt32 UInt64 CGFloat CGPoint CGSize CGRect CGImage
CGContext CGColorSpace CGImageAlphaInfo UIImage UIColor UIView UIViewController View Text VStack
HStack ZStack Button Image Canvas Path Color Font ObservableObject Published StateObject State
Binding EnvironmentObject NavigationStack NavigationLink ScrollView List ForEach Toggle Slider
Picker TextField SecureField ProgressView Spacer Divider Group AnyView Task MainActor Sendable
JSONSerialization JSONEncoder JSONDecoder URLSession URLRequest HTTPURLResponse URLSessionConfiguration
UserDefaults FileManager Bundle NSLock NSNull NSNumber NSString NSData DispatchQueue OperationQueue
SymmetricKey AES HMAC SHA256 SecRandomCopyBytes OpaquePointer Notification Timer Locale Calendar
CIImage CIContext CIFilter AVCaptureSession AVCapturePhotoOutput AVCaptureDevice AVCaptureDeviceInput
AVCaptureVideoPreviewLayer AVCapturePhoto AVCapturePhotoCaptureDelegate AVCapturePhotoSettings
NSObject FileProtectionType CFDictionary CFTypeRef CFString CFData OSStatus utsname
CGImageSourceCreateWithData CGImageSourceCreateThumbnailAtIndex PropertyListSerialization
XCTestCase XCTAssert Notification Self GCM SealedBox Builder Companion
kSecClass kSecAttrService kSecReturnData kSecMatchLimit kSecValueData kSecAttrAccount
kSecAttrAccessible kSecRandomDefault kCGImageSourceCreateThumbnailFromImageAlways
kCGImageSourceThumbnailMaxPixelSize kCGImageSourceCreateThumbnailWithTransform
CChar CGColorSpaceCreateDeviceRGB SecItemCopyMatching SecItemAdd T U V
App Section Label ToolbarItem DragGesture UIGraphicsImageRenderer UIBezierPath UIColor
PhotosPicker PhotosPickerItem Form NavigationView Toolbar Menu Alert Sheet GeometryReader
Scene WindowGroup EnvironmentValues Namespace FocusState AppStorage SceneStorage
ISO8601DateFormatter DateFormatter NumberFormatter IndexSet EdgeInsets Angle UnitPoint
SQLITE_OK SQLITE_ROW SQLITE_DONE SQLITE_NULL SQLITE_OPEN_READWRITE SQLITE_OPEN_CREATE
SQLITE_OPEN_FULLMUTEX SQLITE_TRANSIENT NSRegularExpression NSRange XCTAssertEqual
XCTAssertNil XCTAssertNotNil XCTAssertTrue XCTAssertFalse
""".split())


def main():
    root = sys.argv[1]
    include_dirs = sys.argv[2:] or ['.']
    paths = []
    for d in include_dirs:
        base = os.path.join(root, d)
        for dirpath, _, names in os.walk(base):
            for n in names:
                if n.endswith('.swift'):
                    paths.append(os.path.join(dirpath, n))
    paths = sorted(set(paths))
    decls, exts, files, nested = scan(paths)

    problems = {'redeclarations': [], 'unterminated_comment': [], 'unbalanced': [], 'undefined_refs': []}

    # 1. 重複宣告
    for name, sites in decls.items():
        uniq_files = {s[0] for s in sites}
        if len(sites) > 1 and len(uniq_files) > 1:
            problems['redeclarations'].append({
                'symbol': name,
                'sites': [{'file': os.path.relpath(f, root), 'kind': k, 'line': l} for f, k, l in sites]
            })

    # 2a. 未終結的區塊註解（巢狀 /* 吞掉整個檔案）
    for p, depth in check_unterminated_comments(paths):
        problems.setdefault('unterminated_comment', []).append(
            {'file': os.path.relpath(p, root), 'depth': depth,
             'hint': '註解內出現 /*（Swift 區塊註解會巢狀），整個檔案會被吞掉'})

    # 2. 括號配對
    for p, label, d in check_braces(files):
        problems['unbalanced'].append({'file': os.path.relpath(p, root), 'kind': label, 'delta': d})

    # 3. 引用不存在的頂層型別（保守：只看 Foo.bar 形式的大寫開頭 receiver）
    known = set(decls.keys()) | set(exts.keys()) | set(nested.keys()) | SWIFT_STDLIB
    # ⚠ 只比對 `Foo.bar` 不夠：實測漏掉 `CaptureContainer(rgb:…)`——建構子呼叫與
    #   型別標註都不含點號，而那正是刪掉一個型別後最常見的殘留形式。三種都要看。
    ref_re = re.compile(
        r'\b([A-Z]\w*)\s*\.'
        r'|\b([A-Z]\w*)\s*\('
        r'|(?::|->)\s*\[?([A-Z]\w*)')
    for p, clean in files.items():
        seen = set()
        for ln, line in enumerate(clean.split('\n'), 1):
            for m in ref_re.finditer(line):
                nm = m.group(1) or m.group(2) or m.group(3)
                if not nm or nm in known or nm in seen:
                    continue
                seen.add(nm)
                problems['undefined_refs'].append(
                    {'file': os.path.relpath(p, root), 'symbol': nm, 'line': ln})

    print(json.dumps({
        'files_scanned': len(paths),
        'top_level_symbols': len(decls),
        'problems': problems,
        'counts': {k: len(v) for k, v in problems.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
