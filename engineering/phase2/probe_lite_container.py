"""Offline image probe; run inside the built image with --network=none.

Synthetic storage and disabled security initialization are deliberate: this is
Linux packaging/model validation, not Apple/GCS deployment acceptance.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,default=Path('/app'))
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    args=parser.parse_args();context=args.context.resolve()
    manifest_data=(context/'build-context-manifest.json').read_bytes()
    if hashlib.sha256(manifest_data).hexdigest()!=args.manifest_sha256:
        raise ValueError('source manifest does not match approved build input')
    manifest=json.loads(manifest_data)
    for name,row in manifest['files'].items():
        file=context/name
        if not file.resolve().is_relative_to(context) or not file.is_file():raise ValueError('invalid manifest path')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('image source digest mismatch: '+name)
    sys.path.insert(0,str(context))
    import numpy as np
    import onnxruntime as ort
    import runtime_golden
    golden=runtime_golden.verify()
    # Prevent credentials or a profile inherited from a workstation affecting the probe.
    inherited_path=os.environ.get('PATH','')
    os.environ.clear();os.environ['PATH']=inherited_path
    with tempfile.TemporaryDirectory(prefix='lite-container-probe-') as folder:
        os.chdir(folder)
        os.environ.update(WOUNDAI_SERVICE_PROFILE='lite',WOUNDAI_ENABLE_LITE_API='1',WOUNDAI_STORE='gcs',
            WOUNDAI_GCS_PREFIX='lite-public-v1',WOUNDAI_GCS_BUCKET='synthetic-media',WOUNDAI_LITE_SECURITY_BUCKET='synthetic-security',
            WOUNDAI_RUNTIME_DIR=folder,LITE_IP_SALT='synthetic-fixture-only-not-a-secret',WOUNDAI_REQUIRE_FUNCTIONAL_TESTS='1')
        import lite_attest_config
        with patch.object(lite_attest_config,'build_lite_security',return_value=(None,None,None)),patch('google.cloud.storage.Client',side_effect=AssertionError('network cloud forbidden')):
            import app
            if Path(app.__file__).resolve()!=context/'app.py':raise ValueError('wrong app import')
            assert app.analysis_service._model_backend=='onnxruntime'
            x=app._apply_ssot_preproc(np.zeros((2,2,3),np.uint8),'student')
            np.testing.assert_allclose(x[0,0],[-0.485/0.229,-0.456/0.224,-0.406/0.225],rtol=1e-6)
            import api_lite
            # Exercise the installed OpenCV XML and native detector, not a mock.
            # Blank synthetic input checks availability, not detection sensitivity.
            assert api_lite.FACE_REJECT is True
            assert api_lite._has_face(np.zeros((128,128,3),np.uint8)) == (False, "0")
            image=np.zeros((64,64,3),np.uint8);image[16:48,16:48]=[190,65,80]
            mask,info=app.segment_for_lite(image)
            assert mask.shape==(64,64) and np.isfinite(mask).all()
            model_results={}
            for name in ('student_fp16.onnx','a_unet.onnx','unetpp.onnx'):
                model=ort.InferenceSession(str(context/'models'/name),providers=['CPUExecutionProvider'])
                inp=model.get_inputs()[0];shape=[v if type(v) is int else 1 for v in inp.shape]
                arrays=model.run(None,{inp.name:np.zeros(shape,np.float32)})
                assert all(np.isfinite(a).all() for a in arrays)
                model_results[name]={'input':inp.shape,'output':[list(a.shape) for a in arrays],'finite':True}
            client=app.app.test_client()
            assert client.get('/console').status_code==404
            assert client.post('/api/auth/login',json={}).status_code==404
            # Synthetic security is intentionally unavailable, and must not allow upload.
            assert client.post('/api/v1/lite/segment',data=b'unsigned').status_code==503
            health=client.get('/api/health')
            assert health.status_code==503 and health.json['services']['segmentation_model'] is True
        from store import LocalStore
        from lite_raw_depth_store import save_capture,capture_key,unpack,erase_installation_objects
        fixture=json.loads(args.fixture.read_text())
        decode=lambda key:base64.b64decode(fixture[key],validate=True)
        store=LocalStore(Path(folder)/'media')
        receipt=save_capture(store,'synthetic-install','native-swift-frame',decode('metadata'),decode('rawDepth'),decode('jpeg'))
        assert receipt==fixture['receipt']
        raw=unpack(store.get_blob(capture_key('synthetic-install','native-swift-frame')))
        assert raw['raw_depth']==decode('rawDepth') and raw['jpeg']==decode('jpeg')
        assert erase_installation_objects(store,'synthetic-install')==1
        assert not list(store.list_keys('lite_raw/synthetic-install/'))
        print(json.dumps({'status':'container_probe_pass','source_manifest_sha256':args.manifest_sha256,
            'face_detector_blank_smoke':True,'canonical_golden':golden,'models':model_results,'raw_receipt_matches':True,'raw_cleanup':True,
            'security_gate':'unconfigured rejected','limits':['synthetic inputs','temporary LocalStore','Apple/GCS not tested','no accuracy claim']},sort_keys=True))

if __name__=='__main__':main()
