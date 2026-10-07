"""Binding tests; not proof of genuine Apple attestation or durable replay protection."""
import hashlib
import base64
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
from lite_attest_request import client_data, client_data_hash, MAX_BODY


def request():
    return dict(audience='woundlite-production', installation='synthetic-install',
                key_id=bytes(range(32)), challenge=bytes(range(32,64)),
                request_id='00000000-0000-0000-0000-000000000001', method='POST',
                path='/api/v1/lite/annotation/revision', content_type='application/json',
                body=b'{"revision":1}')


class RequestBindingTests(unittest.TestCase):
    def test_shared_swift_fixtures_remain_current(self):
        path = Path(__file__).resolve().parents[2] / 'iOS/WoundLiteTests/Fixtures/lite_attest_request.json'
        cases = json.loads(path.read_text())
        self.assertEqual(len(cases), 3)
        for row in cases:
            payload = {k: row[k] for k in request()}
            for key in ('key_id', 'challenge', 'body'):
                payload[key] = base64.b64decode(payload[key], validate=True)
            self.assertEqual(base64.b64encode(client_data(**payload)).decode(), row['expected_client_data'])
            self.assertEqual(client_data_hash(**payload).hex(), row['expected_sha256'])

    def test_every_binding_field_changes_signed_data(self):
        base=request();original=client_data_hash(**base)
        variants=dict(audience='woundlite-staging', installation='other-install',
                      key_id=b'k'*32, challenge=b'n'*32,
                      request_id='00000000-0000-0000-0000-000000000002',
                      path='/api/v1/lite/segment', content_type='application/json; charset=utf-8',
                      body=b'{"revision":2}')
        for field,value in variants.items():
            with self.subTest(field=field):
                self.assertNotEqual(original,client_data_hash(**dict(base,**{field:value})))

    def test_json_whitespace_and_multipart_boundary_are_bound_exactly(self):
        base=request()
        self.assertNotEqual(client_data_hash(**base),client_data_hash(**dict(base,body=b'{ "revision":1}')))
        one=dict(base,path='/api/v1/lite/segment',content_type='multipart/form-data; boundary=first',body=b'--first\r\n')
        two=dict(one,content_type='multipart/form-data; boundary=second')
        self.assertNotEqual(client_data_hash(**one),client_data_hash(**two))

    def test_delete_is_bound_to_owner_and_has_no_body(self):
        base=dict(request(),method='DELETE',path='/api/v1/lite/data/synthetic-install',content_type='',body=b'')
        self.assertTrue(client_data(**base))
        for changes in [dict(path='/api/v1/lite/data/other'),dict(body=b'{}'),dict(method='GET')]:
            with self.assertRaises(ValueError):client_data(**dict(base,**changes))

    def test_noncanonical_path_and_header_injection_rejected(self):
        for field,values in {'path':['/api/v1/lite/segment?x=1','/api/v1/lite/segment/','/api/v1/lite/%73egment','/api/v1/lite/../segment'],
                             'content_type':['application/json\r\nInjected: yes','文字'],
                             'installation':['../other','synthetic-install\n',''],
                             'audience':['','production\x00staging'],
                             'request_id':['arbitrary','00000000-0000-0000-0000-000000000001\n']}.items():
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):client_data(**dict(request(),**{field:value}))

    def test_byte_lengths_and_types_are_strict(self):
        for field,values in {'key_id':[b'k'*31,b'k'*33,'k'*32], 'challenge':[b'n'*31,b'n'*33,None],
                             'body':[b'x'*(MAX_BODY+1),'body']}.items():
            for value in values:
                with self.subTest(field=field),self.assertRaises(ValueError):client_data(**dict(request(),**{field:value}))

    def test_length_framing_avoids_concatenation_ambiguity(self):
        a=dict(request(),audience='ab',installation='c');b=dict(request(),audience='a',installation='bc')
        self.assertNotEqual(client_data(**a),client_data(**b))
        self.assertEqual(client_data_hash(**a),hashlib.sha256(client_data(**a)).digest())

if __name__=='__main__':unittest.main()
