"""P0-4 image boundary. No storage or Flask imports; pixels = decode(canonical).

JPEGs on the safe JFIF allowlist are byte-preserving. All other valid JPEGs
and opaque PNGs are re-encoded without metadata. Alpha PNGs are rejected,
not silently flattened. Version changes require new deployment golden bytes.
"""
from dataclasses import dataclass
import hashlib
import struct
import zlib

import cv2
import numpy as np

JPEG_QUALITY = 95
CANONICALIZATION_VERSION = f"canon-v1;cv2=={cv2.__version__};jpeg_q={JPEG_QUALITY}"
MAX_PIXELS = 32_000_000
MAX_BYTES = 24 * 1024 * 1024


class InvalidImage(ValueError):
    pass


def _size_ok(width, height):
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise InvalidImage("影像尺寸無效或超過 3200 萬像素")


def jpeg_safe_pass(raw):
    """Parse the complete stream (including progressive scans and EOI).

    Repeated DQT/DHT/SOS markers are legal table/scan structure, not metadata.
    Duplicate JFIF/SOF, non-JFIF APP0 and every other APP/COM force re-encode.
    """
    if not raw.startswith(b"\xff\xd8"):
        raise InvalidImage("不是 JPEG")
    pos, safe, frame, scan, jfif = 2, True, False, False, False
    while pos < len(raw):
        if raw[pos] != 0xFF:
            raise InvalidImage("JPEG marker 結構損壞")
        while pos < len(raw) and raw[pos] == 0xFF:
            pos += 1
        if pos >= len(raw):
            raise InvalidImage("JPEG marker 截斷")
        marker = raw[pos]
        pos += 1
        if marker == 0xD9:
            if pos != len(raw) or not frame or not scan:
                raise InvalidImage("JPEG 尾端或影像結構無效")
            return safe
        if marker in (0, 0xD8) or 0xD0 <= marker <= 0xD7:
            raise InvalidImage("JPEG marker 位置無效")
        if marker == 0x01:  # TEM is parseable, but outside the safe-pass subset.
            safe = False
            continue
        if pos + 2 > len(raw):
            raise InvalidImage("JPEG 長度截斷")
        length = int.from_bytes(raw[pos:pos + 2], "big")
        if length < 2 or pos + length > len(raw):
            raise InvalidImage("JPEG segment 截斷")
        data = raw[pos + 2:pos + length]
        pos += length
        if marker == 0xE0:
            safe &= not jfif and len(data) == 14 and data[:5] == b"JFIF\0" and data[12:14] == b"\0\0"
            jfif = True
        elif marker in (0xC0, 0xC2):
            if len(data) < 6:
                raise InvalidImage("JPEG SOF 截斷")
            _size_ok(int.from_bytes(data[3:5], "big"), int.from_bytes(data[1:3], "big"))
            safe &= not frame
            frame = True
        elif marker not in (0xDB, 0xC4, 0xDD, 0xDA):
            safe = False
        if marker == 0xDA:
            scan = True
            # Skip entropy-coded bytes, stuffed FF00 and restart markers.
            while True:
                edge = raw.find(b"\xff", pos)
                if edge < 0 or edge + 1 >= len(raw):
                    raise InvalidImage("JPEG 缺少 EOI")
                following = edge + 1
                while following < len(raw) and raw[following] == 0xFF:
                    following += 1
                if following >= len(raw):
                    raise InvalidImage("JPEG scan 截斷")
                if raw[following] == 0 or 0xD0 <= raw[following] <= 0xD7:
                    pos = following + 1
                    continue
                pos = edge
                break
    raise InvalidImage("JPEG 缺少 EOI")


def png_metadata(raw):
    pos, chunks, metadata = 8, [], False
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise InvalidImage("不是 PNG")
    while pos < len(raw):
        if pos + 12 > len(raw):
            raise InvalidImage("PNG chunk 截斷")
        size = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        end = pos + 12 + size
        if end > len(raw):
            raise InvalidImage("PNG chunk 長度無效")
        data = raw[pos + 8:pos + 8 + size]
        crc = struct.unpack(">I", raw[end - 4:end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise InvalidImage("PNG CRC 不符")
        if not chunks and (kind != b"IHDR" or size != 13):
            raise InvalidImage("PNG IHDR 無效")
        if kind == b"IHDR":
            if chunks:
                raise InvalidImage("PNG IHDR 重複")
            _size_ok(*struct.unpack(">II", data[:8]))
            if data[9] in (4, 6):
                raise InvalidImage("不接受帶 alpha 的 PNG；請使用不透明 JPEG/PNG")
        if kind == b"tRNS":
            raise InvalidImage("不接受帶透明度 tRNS 的 PNG")
        metadata |= kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND")
        chunks.append(kind)
        pos = end
        if kind == b"IEND":
            if size or pos != len(raw) or b"IDAT" not in chunks:
                raise InvalidImage("PNG 尾端結構無效")
            return metadata
    raise InvalidImage("PNG 缺少 IEND")


@dataclass(frozen=True)
class CanonicalImage:
    data: bytes
    pixels: np.ndarray
    had_metadata: bool

    @property
    def image_id(self):
        return hashlib.sha1(self.data).hexdigest()[:16]

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


def canonicalize(raw: bytes) -> CanonicalImage:
    if not raw or len(raw) > MAX_BYTES:
        raise InvalidImage("影像為空或超過 24 MiB")
    if raw.startswith(b"\xff\xd8"):
        safe = jpeg_safe_pass(raw)
        metadata = not safe
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        metadata = png_metadata(raw)
        safe = False
    else:
        raise InvalidImage("只接受 JPEG 或不透明 PNG")
    # IMREAD_COLOR applies EXIF orientation before encoding strips the tag.
    pixels = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if pixels is None:
        raise InvalidImage("影像解碼失敗")
    _size_ok(pixels.shape[1], pixels.shape[0])
    if safe:
        data = raw
    else:
        ok, encoded = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise InvalidImage("canonical JPEG 編碼失敗")
        data = encoded.tobytes()
    canonical_pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if canonical_pixels is None:
        raise InvalidImage("canonical JPEG 解碼失敗")
    return CanonicalImage(data, canonical_pixels, bool(metadata))
