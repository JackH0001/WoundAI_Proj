"""Offline draft format: structural integrity, never research authorization."""
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import unittest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend'/'Flask'))
from lite_raw_depth_contract import validate, parse_multipart
from werkzeug.datastructures import MultiDict, FileStorage

class RawDepthTests(unittest.TestCase):
    def setUp(self):
        b=io.BytesIO();Image.new('RGB',(64,64),'blue').save(b,'JPEG');self.jpeg=b.getvalue()
        bits=[0,0x80000000,0x7fc01234,0x7f800000,0x3e99aabb]+[0x3e99999a]*59
        self.raw=struct.pack('<64I',*bits)
        self.m=dict(schema='lite.rgbd/1',format='float32_le',unit='m',width=8,height=8,
          rgb_width=64,rgb_height=64,rgb_sha256=hashlib.sha256(self.jpeg).hexdigest(),
          depth_sha256=hashlib.sha256(self.raw).hexdigest(),
          intrinsics=dict(fx=50,fy=50,cx=32,cy=32,reference_width=64,reference_height=64),
          source_exif_orientation=1,accuracy='absolute',filtered=False,confidence_kind='validity_only',
          pose='unavailable',capture_time='unavailable',registration='not_verified')
    def run_contract(self):return validate(json.dumps(self.m).encode(),self.raw,self.jpeg)
    def parts(self):
        return MultiDict([('raw_depth_metadata',json.dumps(self.m))]), MultiDict([
            ('image',FileStorage(stream=io.BytesIO(self.jpeg),filename='capture.jpg')),
            ('raw_depth',FileStorage(stream=io.BytesIO(self.raw),filename='depth.f32le'))])
    def test_binary_multipart_preserves_raw_bits(self):
        form,files=self.parts();self.assertEqual(parse_multipart(form,files)['raw_depth'],self.raw)
    def test_duplicate_parts_rejected(self):
        for key in ('image','raw_depth','raw_depth_metadata'):
            form,files=self.parts()
            if key in files:files.add(key,files[key])
            else:form.add(key,form[key])
            with self.assertRaises(ValueError):parse_multipart(form,files)
    def test_missing_or_extra_parts_rejected(self):
        form,files=self.parts();files.pop('raw_depth')
        with self.assertRaises(ValueError):parse_multipart(form,files)
        form,files=self.parts();form['unexpected']='data'
        with self.assertRaises(ValueError):parse_multipart(form,files)
    def test_oversized_stream_read_is_bounded(self):
        class OversizedStream:
            def read(self,n):
                self.requested=n
                return b'x'*n
        form,files=self.parts();stream=OversizedStream();files['raw_depth']=FileStorage(stream=stream,filename='depth.f32le')
        with self.assertRaises(ValueError):parse_multipart(form,files)
        self.assertEqual(stream.requested,1024*1024*4+1)
    def test_exact_bits_and_invalid_samples_preserved(self):
        r=self.run_contract();self.assertEqual(r['raw_depth'],self.raw)
        self.assertEqual(r['validity_mask'][:5],bytes([0,0,0,0,255]))
        self.assertEqual(r['training_admission'],'not_evaluated')
    def test_float32_range_boundary_matches_swift(self):
        self.raw=struct.pack('<64f',.05,.0501,60,59.999,*([.3]*60))
        self.m['depth_sha256']=hashlib.sha256(self.raw).hexdigest()
        self.assertEqual(self.run_contract()['validity_mask'][:4],bytes([0,255,0,255]))
    def test_same_dimensions_wrong_jpeg_rejected(self):
        b=io.BytesIO();Image.new('RGB',(64,64),'red').save(b,'JPEG');self.jpeg=b.getvalue()
        with self.assertRaises(ValueError):self.run_contract()
    def test_truncated_and_extra_samples_rejected_even_with_matching_hash(self):
        for data in (self.raw[:-1],self.raw+b'0000'):
            self.m['depth_sha256']=hashlib.sha256(data).hexdigest()
            with self.assertRaises(ValueError):validate(json.dumps(self.m).encode(),data,self.jpeg)
    def test_unknown_units_or_fabricated_semantics_rejected(self):
        for k,v in [('format','float32_be'),('unit','mm'),('schema','lite.rgbd/2'),('confidence_kind','sensor'),('pose',[]),('registration','verified')]:
            with self.subTest(field=k):
                m=dict(self.m);m[k]=v
                with self.assertRaises(ValueError):validate(json.dumps(m).encode(),self.raw,self.jpeg)
    def test_boolean_dimensions_and_nonfinite_intrinsics_rejected(self):
        self.m['width']=True
        with self.assertRaises(ValueError):self.run_contract()
        self.m['width']=8;self.m['intrinsics']['fx']=float('nan')
        with self.assertRaises(ValueError):self.run_contract()
    def test_duplicate_fields_rejected(self):
        data=json.dumps(self.m).encode();data=b'{"width":8,'+data[1:]
        with self.assertRaises(ValueError):validate(data,self.raw,self.jpeg)
    def test_actual_jpeg_dimensions_checked(self):
        self.m['rgb_width']=32;self.m['rgb_height']=32
        with self.assertRaises(ValueError):self.run_contract()
    def test_byte_corruption_rejected(self):
        self.raw=bytes([1])+self.raw[1:]
        with self.assertRaises(ValueError):self.run_contract()
    def test_metadata_and_decode_limits(self):
        with self.assertRaises(ValueError):validate(b' '*16385,self.raw,self.jpeg)
        self.m['intrinsics']['reference_height']=32
        with self.assertRaises(ValueError):self.run_contract()

if __name__=='__main__':unittest.main()
