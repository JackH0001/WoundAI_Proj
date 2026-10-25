"""Cross-language synthetic fixtures from actual signed Flask responses."""
from pathlib import Path
import argparse,base64,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
from test_lite_service_http import ServiceHTTPTests
from test_lite_attest_request import request
from lite_attest_request import client_data,client_data_hash

def fixture():
    h=ServiceHTTPTests();h.setUp()
    try:
        prepared=h.json_request('prepare');done=h.run_request()
        assert prepared.status_code==done.status_code==200
        bindings=[]
        for action in ('prepare','run','status'):
            value=dict(request(),path='/api/v1/lite/service/'+action)
            bindings.append({**{k:base64.b64encode(v).decode() if isinstance(v,bytes) else v for k,v in value.items()},
                'expected_client_data':base64.b64encode(client_data(**value)).decode(),
                'expected_sha256':client_data_hash(**value).hex()})
        # Registration creates a random installation each run. Native transport
        # fixtures use a fixed synthetic owner; response fields are unchanged.
        return dict(schema='lite-native-service-fixture/1',installation='synthetic-native-service',
            capture_id=f'{1:032x}',jpeg=base64.b64encode(h.f.jpeg).decode(),image_sha256=h.image_hash,
            prepare=prepared.json,completed=done.json,signature_bindings=bindings)
    finally:h.doCleanups()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(fixture(),ensure_ascii=False,sort_keys=True,indent=2)+'\n')
