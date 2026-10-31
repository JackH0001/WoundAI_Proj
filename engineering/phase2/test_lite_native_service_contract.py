import hashlib,json,unittest
from pathlib import Path
from lite_native_service_fixture import fixture,ROOT

class NativeServiceContractTests(unittest.TestCase):
    def test_checked_in_swift_fixture_matches_actual_signed_flask_responses(self):
        saved=json.loads((ROOT/'iOS/WoundLiteTests/Fixtures/lite_service_operation.json').read_text())
        self.assertEqual(saved,fixture())

    def test_canonical_result_text_matches_receipt_without_float_reserialization(self):
        done=fixture()['completed']
        self.assertEqual(hashlib.sha256(done['result_json'].encode()).hexdigest(),done['receipt']['result_sha256'])
        self.assertEqual(json.loads(done['result_json']),done['result'])
        self.assertFalse(done['research_stored'])

if __name__=='__main__':unittest.main()
