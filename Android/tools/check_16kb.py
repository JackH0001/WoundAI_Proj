#!/usr/bin/env python3
"""檢查 APK/AAB 內每顆 .so 是否符合 Google Play 的 16 KB page size 要求。

為什麼不用 readelf：macOS 預設沒有 readelf，NDK 也不一定裝。這支只用標準庫
直接讀 ELF program header，任何有 python3 的機器都能跑，不依賴工具鏈。

判定規則（依 developer.android.com/guide/practices/page-sizes）：
  * 只有 **64 位元** ABI 受規範（arm64-v8a、x86_64）。armeabi-v7a / x86 不受限，
    因為 16 KB page 的裝置本身就是 64 位元。
  * 條件是所有 PT_LOAD segment 的 p_align >= 0x4000（16384）。
  * 另外檢查 .so 在 zip 內必須是 STORED（未壓縮）且 offset 對齊 16 KB，
    否則系統無法直接 mmap。

用法：python3 tools/check_16kb.py <path-to-apk-or-aab>
離開碼 0 = 全部合規；1 = 有違規。
"""
import sys, zipfile, struct, pathlib

ALIGN = 0x4000
ABI64 = ("arm64-v8a", "x86_64")
PT_LOAD = 1


def load_aligns(data: bytes):
    """回傳所有 PT_LOAD 的 p_align；非 ELF64 回 None。"""
    if data[:4] != b"\x7fELF" or data[4] != 2:          # EI_CLASS 2 = ELF64
        return None
    little = data[5] == 1
    e = "<" if little else ">"
    e_phoff, = struct.unpack_from(e + "Q", data, 0x20)
    e_phentsize, e_phnum = struct.unpack_from(e + "HH", data, 0x36)
    out = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, = struct.unpack_from(e + "I", data, off)
        if p_type == PT_LOAD:
            p_align, = struct.unpack_from(e + "Q", data, off + 0x30)
            out.append(p_align)
    return out


def container_kind(z):
    """APK 還是 AAB。兩者的判準**不一樣**，混用會給出假警報。

    AAB 是給 Play 的中介格式，裡面的 .so 一律 DEFLATE 壓縮；要不要解壓、
    怎麼對齊，是 Play 端 bundletool 依 BundleConfig.pb 產生分割 APK 時才決定的。
    所以對 AAB 只能驗 ELF 的 p_align；拿 APK 的 STORED／offset 判準去套，
    會把一顆完全合規的 AAB 判成不合規（2026-10-05 實際發生過）。
    """
    names = set(z.namelist())
    if "BundleConfig.pb" in names:
        return "AAB"
    if "AndroidManifest.xml" in names:
        return "APK"
    return "未知"


def main(path):
    bad = []
    rows = []

    def data_offset(fh, info):
        """zip 內實際資料起點。info.header_offset 指的是 local header，
        而 zipalign 是靠 local header 的 extra field 補位來對齊**資料**，
        所以必須把 30 bytes 固定頭 + 檔名長 + extra 長加回去才是真正的 offset。"""
        fh.seek(info.header_offset + 26)
        n, m = struct.unpack("<HH", fh.read(4))
        return info.header_offset + 30 + n + m

    with zipfile.ZipFile(path) as z:
        kind = container_kind(z)
        print(f"容器格式：{kind}" + (
            "（只驗 ELF p_align；zip 層的解壓與對齊由 Play 的 bundletool 處理）"
            if kind == "AAB" else "（驗 ELF p_align ＋ zip 層 STORED／offset 對齊）"))
        print()
        for info in z.infolist():
            name = info.filename
            if not name.endswith(".so"):
                continue
            parts = name.split("/")
            abi = parts[-2] if len(parts) >= 2 else "?"
            data = z.read(name)
            aligns = load_aligns(data)
            if aligns is None:
                rows.append((abi, parts[-1], "—", "32-bit/非 ELF64", "略過"))
                continue
            worst = min(aligns)
            stored = info.compress_type == zipfile.ZIP_STORED
            notes = []
            if kind == "APK":
                if not stored:
                    notes.append("壓縮(非 STORED)")
                else:
                    with open(path, "rb") as fh:
                        if data_offset(fh, info) % ALIGN:
                            notes.append("zip offset 未對齊")
            ok = worst >= ALIGN and not notes
            if abi in ABI64:
                rows.append((abi, parts[-1], hex(worst),
                             "; ".join(notes) or "-", "✅" if ok else "❌"))
                if not ok:
                    bad.append(f"{abi}/{parts[-1]} (p_align={hex(worst)}"
                               + (", " + "; ".join(notes) if notes else "") + ")")
            else:
                rows.append((abi, parts[-1], hex(worst), "32-bit 不受規範", "略過"))

    w = max([len(r[1]) for r in rows] + [10])
    print(f"{'ABI':<13} {'library':<{w}} {'p_align':<9} {'備註':<22} 判定")
    print("-" * (13 + w + 9 + 24 + 6))
    for abi, lib, al, note, verdict in sorted(rows):
        print(f"{abi:<13} {lib:<{w}} {al:<9} {note:<22} {verdict}")
    print()
    if bad:
        print(f"❌ {len(bad)} 顆 64 位元 .so 不符合 16 KB page size：")
        for b in bad:
            print("   -", b)
        print("\nGoogle Play 自 2027-02-01 起，targetSdk >= 35 的更新若不合規將無法發佈。")
        return 1
    print("✅ 所有 64 位元 .so 均符合 16 KB page size。")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not pathlib.Path(sys.argv[1]).exists():
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
