"""Executable catalog contract: no automatic diagnosis or missing-value scoring."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'Backend/Flask'))
from medical_wound_profile import validate_profile, assessment_requirements
from medical_wound_catalog import CATALOG

CASES = json.loads((ROOT/'engineering/contracts/medical_wound_profile_cases.json').read_text(encoding='utf-8'))

class ProfileContract(unittest.TestCase):
    def test_shared_golden_cases(self):
        for case in CASES:
            with self.subTest(case=case['name']):
                self.assertIs(validate_profile(case['profile']), case['valid'])
    def test_every_catalog_option_has_unique_code_and_references(self):
        for key in ('regions','sites','lateralities','surfaces','etiologies','factors','instruments'):
            values=CATALOG[key]
            self.assertEqual(len(values),len({v['code'] for v in values}))
            self.assertTrue(all(v['label'].strip() for v in values))
        for s in CATALOG['sites']:
            self.assertIn(s['region'],{x['code'] for x in CATALOG['regions']})
            self.assertTrue(set(s['lateralities']) <= {x['code'] for x in CATALOG['lateralities']})
            self.assertTrue(set(s['surfaces']) <= {x['code'] for x in CATALOG['surfaces']})
        for e in CATALOG['etiologies']:
            self.assertTrue(set(e['instruments']) <= {x['code'] for x in CATALOG['instruments']})
    def test_diabetes_does_not_change_pressure_injury_etiology(self):
        profile=copy.deepcopy(CASES[0]['profile'])
        before=copy.deepcopy(profile)
        items=assessment_requirements(profile)
        self.assertEqual([i['code'] for i in items],['pressure_stage','push'])
        self.assertEqual(profile,before)
    def test_unknown_etiology_does_not_get_generic_push(self):
        profile=dict(CASES[0]['profile'],etiology='unknown')
        self.assertEqual(assessment_requirements(profile),[])
    def test_no_numeric_scores_without_clinical_inputs(self):
        for case in CASES:
            if case['valid']:
                for item in assessment_requirements(case['profile']):
                    self.assertEqual(item['status'],'requirements_only')
                    self.assertNotIn('total',item)
                    self.assertNotIn('subscores',item)
    def test_requirements_reject_invalid_profile(self):
        with self.assertRaises(ValueError):assessment_requirements({})
    def test_generated_catalogs_match_source(self):
        subprocess.run([sys.executable,'-B',str(ROOT/'tools/generate_medical_wound_catalog.py'),'--check'],check=True,cwd=ROOT)

import test_native_wound_mask as fixture
import api_flywheel as fw
from unittest.mock import patch

class ProfileHTTP(unittest.TestCase):
    setUp=fixture.NativeMasks.setUp
    tearDown=fixture.NativeMasks.tearDown
    payload=fixture.NativeMasks.payload
    post=fixture.NativeMasks.post
    rows=fixture.NativeMasks.rows
    def test_saved_and_duplicate_receipts_preserve_profile(self):
        body=dict(self.payload(),wound_profile=CASES[0]['profile'])
        a=self.post(body);b=self.post(body)
        self.assertEqual(a.status_code,200,a.json)
        self.assertEqual(b.json['status'],'duplicate_skipped')
        self.assertEqual(a.json['wound_profile'],body['wound_profile'])
        self.assertEqual(b.json['wound_profile'],a.json['wound_profile'])
        self.assertEqual(len(self.rows()),1)
        self.assertEqual(self.rows()[0]['wound_profile'],body['wound_profile'])
    def test_changed_profile_creates_revision_without_replacing_masks(self):
        body=dict(self.payload(),wound_profile=CASES[0]['profile'])
        a=self.post(body)
        body['wound_profile']=dict(body['wound_profile'],etiology='diabetic_foot')
        b=self.post(body)
        self.assertEqual(b.status_code,200,b.json)
        self.assertNotEqual(a.json['annotation_receipt_id'],b.json['annotation_receipt_id'])
        self.assertEqual(len(self.rows()),2)
        self.assertEqual(self.rows()[0]['wound_profile']['etiology'],'pressure')
        self.assertEqual(self.rows()[1]['wound_profile']['etiology'],'diabetic_foot')
        self.assertEqual(a.json['wound_mask_sha256'],b.json['wound_mask_sha256'])
    def test_factor_order_not_a_new_revision(self):
        body=dict(self.payload(),wound_profile=dict(CASES[0]['profile'],factors=['diabetes','neuropathy']))
        a=self.post(body);body['wound_profile']['factors'].reverse();b=self.post(body)
        self.assertEqual(a.json['annotation_receipt_id'],b.json['annotation_receipt_id'])
        self.assertEqual(b.json['status'],'duplicate_skipped')
    def test_invalid_profile_rejected_before_assets(self):
        for c in CASES:
            if c['valid']:continue
            with self.subTest(name=c['name']),patch.object(fw,'store_revision_asset') as save:
                response=self.post(dict(self.payload(),wound_profile=c['profile']))
                self.assertEqual(response.status_code,400,response.json);save.assert_not_called()
        self.assertEqual(self.rows(),[])
    def test_manifest_retains_profile_and_rejects_corrupt_profile(self):
        body=dict(self.payload(),wound_profile=CASES[0]['profile'])
        self.post(body)
        response=self.client.get('/api/v1/dataset/manifest?kind=wound&source=sample',headers=self.headers['admin'])
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['items'][0]['wound_profile'],body['wound_profile'])
        rows=self.rows();rows[0]['wound_profile']['surface']='palmar'
        original=fw.read_jsonl
        with patch.object(fw,'read_jsonl',side_effect=lambda path,**kw:((rows,0) if kw.get('with_bad') else rows) if path==fw.QUEUE else original(path,**kw)):
            result=self.client.get('/api/v1/dataset/manifest?kind=wound&source=sample',headers=self.headers['admin'])
        self.assertEqual(result.json['items'],[])
    def test_legacy_request_has_no_inferred_profile(self):
        a=self.post(self.payload())
        self.assertEqual(a.status_code,200,a.json)
        self.assertNotIn('wound_profile',a.json)
        self.assertNotIn('wound_profile',self.rows()[0])

if __name__=='__main__':unittest.main()
