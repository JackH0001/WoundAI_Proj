"""Metadata admission for Lite research RGB images. Keep accepted bytes unchanged."""
import struct

from image_canonical import InvalidImage, jpeg_safe_pass, png_metadata


class ResearchPhotoMetadata(ValueError):
    pass


# TIFF types SHORT, LONG, and RATIONAL. Accept only single values produced
# by the RGB renderer. IFD1 (embedded thumbnail) and GPS are not admitted.
_IMAGE_TAGS = {274: (3,), 282: (5,), 283: (5,), 296: (3,), 34665: (4,)}
_EXIF_TAGS = {40961: (3,), 40962: (3, 4), 40963: (3, 4)}
# Apple's renderer can emit an empty IPTC resource and its MD5 checksum.
_EMPTY_IPTC = bytes.fromhex(
    '50686f746f73686f7020332e30003842494d0404000000000000'
    '3842494d0425000000000010d41d8cd98f00b204e9800998ecf8427e'
)


def _require(condition):
    if not condition:
        raise ResearchPhotoMetadata('unsupported research image metadata')


def _check_exif(tiff):
    endian = '<' if tiff[:2] == b'II' else '>'
    _require(tiff[:2] in {b'II', b'MM'} and len(tiff) >= 8)
    magic, root = struct.unpack_from(endian + 'HI', tiff, 2)
    _require(magic == 42)
    covered = bytearray(len(tiff))

    def cover(at, size):
        _require(0 <= at <= len(tiff) - size and not any(covered[at:at + size]))
        covered[at:at + size] = b'\1' * size

    def directory(at, allowed):
        _require(8 <= at <= len(tiff) - 2)
        count, = struct.unpack_from(endian + 'H', tiff, at)
        _require(count <= len(allowed))
        end = at + 2 + count * 12
        cover(at, end + 4 - at)
        _require(tiff[end:end + 4] == bytes(4))
        seen = set()
        for i in range(count):
            entry = at + 2 + 12 * i
            tag, kind, n, value = struct.unpack_from(endian + 'HHII', tiff, entry)
            _require(tag not in seen and kind in allowed.get(tag, ()) and n == 1)
            seen.add(tag)
            if tag == 34665:
                directory(value, _EXIF_TAGS)
            elif kind == 5:
                cover(value, 8)
            elif kind == 3:
                _require(tiff[entry + 10:entry + 12] == bytes(2))

    cover(0, 8)
    directory(root, _IMAGE_TAGS)
    _require(not any(value for value, used in zip(tiff, covered) if not used))


def check_research_jpeg(raw):
    """Check each metadata segment, even after scan data. ICC color is retained."""
    seen = set()
    color_parts = []
    color_count = None

    def check(marker, data):
        nonlocal color_count
        if marker != 0xe2:
            _require(marker not in seen)
            seen.add(marker)
        if marker == 0xe0:
            _require(len(data) == 14 and data.startswith(b'JFIF\0') and data[-2:] == bytes(2))
        elif marker == 0xe1:
            _require(data.startswith(b'Exif\0\0'))
            _check_exif(data[6:])
        elif marker == 0xe2:
            _require(data.startswith(b'ICC_PROFILE\0') and len(data) > 14
                     and data[12] == len(color_parts) + 1 <= data[13]
                     and color_count in (None, data[13]))
            color_count = data[13]
            color_parts.append(data[14:])
        elif marker == 0xed:
            _require(data == _EMPTY_IPTC)
        elif marker == 0xee:
            _require(len(data) == 12 and data.startswith(b'Adobe'))
        else:
            raise ResearchPhotoMetadata('unsupported research image metadata')

    try:
        jpeg_safe_pass(raw, inspect_metadata=check)
        if color_parts:
            header = color_parts[0]
            _require(len(color_parts) == color_count and len(header) >= 128
                     and header[16:20] == b'RGB ' and header[36:40] == b'acsp'
                     and int.from_bytes(header[:4], 'big') == sum(map(len, color_parts)))
    except InvalidImage as exc:
        raise ResearchPhotoMetadata('invalid research JPEG structure') from exc


def check_research_image(raw):
    if raw.startswith(b'\xff\xd8'):
        return check_research_jpeg(raw)
    # Legacy recognition also accepts opaque PNG. Only pixel structure and
    # fixed-size standard rendering fields are needed for this upload.
    sizes = {b'sRGB': 1, b'gAMA': 4, b'pHYs': 9, b'cHRM': 32}

    def rendering_chunk(kind, data):
        _require(kind in {b'IHDR', b'PLTE', b'IDAT', b'IEND'} or sizes.get(kind) == len(data))

    try:
        png_metadata(raw, inspect_chunk=rendering_chunk)
    except InvalidImage as exc:
        raise ResearchPhotoMetadata('unsupported research image structure') from exc
