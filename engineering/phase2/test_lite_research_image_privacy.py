"""All test data are generated, no capture files or cloud resources."""
import io
import struct
import unittest
from PIL import Image, PngImagePlugin
from lite_research_image_privacy import ResearchPhotoMetadata, check_research_image


def photo(fmt='JPEG', **kwargs):
    out = io.BytesIO()
    Image.new('RGB', (16, 16), 'white').save(out, format=fmt, **kwargs)
    return out.getvalue()


def app(marker, data):
    return b'\xff' + bytes([marker]) + struct.pack('>H', len(data) + 2) + data


class ResearchImagePrivacyTests(unittest.TestCase):
    def test_untagged_jpeg_and_progressive_jpeg(self):
        for progressive in (True, False):
            check_research_image(photo(progressive=progressive))

    def test_rgb_rendering_tags_but_not_author_camera_time_or_gps(self):
        safe = Image.Exif()
        safe[274], safe[296] = 1, 2
        check_research_image(photo(exif=safe))
        for tag, value in [(315, 'QA'), (271, 'camera'), (36867, 'sample'), (34853, {1: 'N'})]:
            with self.subTest(tag=tag), self.assertRaises(ResearchPhotoMetadata):
                exif = Image.Exif()
                exif[tag] = value
                check_research_image(photo(exif=exif))

    def test_extra_comment_xmp_iptc_or_fake_icc_also_detected_after_scan(self):
        jpeg = photo()
        for marker, payload in [(0xfe, b'comment'), (0xe1, b'http://ns.adobe.com/xap/1.0/\0QA'),
                                (0xed, b'Photoshop 3.0\0QA'), (0xe2, b'ICC_PROFILE\0\x01\x01QA')]:
            with self.subTest(marker=marker), self.assertRaises(ResearchPhotoMetadata):
                check_research_image(jpeg[:-2] + app(marker, payload) + jpeg[-2:])

    def test_secondary_tiff_directory_and_trailing_payload_refused(self):
        for following, extra in [(14, bytes(6)), (0, b'QA')]:
            tiff = struct.pack('<2sHIHI', b'II', 42, 8, 0, following) + extra
            jpg = photo()
            with self.subTest(following=following), self.assertRaises(ResearchPhotoMetadata):
                check_research_image(jpg[:2] + app(0xe1, b'Exif\0\0' + tiff) + jpg[2:])

    def test_opaque_png_preserves_scale_but_rejects_free_text_and_other_formats(self):
        check_research_image(photo('PNG', dpi=(72,72)))
        text = PngImagePlugin.PngInfo()
        text.add_text('Author', 'QA')
        for payload in (photo('PNG', pnginfo=text), photo('TIFF')):
            with self.assertRaises(ResearchPhotoMetadata):
                check_research_image(payload)
