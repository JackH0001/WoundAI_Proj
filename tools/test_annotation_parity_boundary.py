"""Receipt statuses must not be mistaken for annotation request fields."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFT = 'iOS/WoundMeasurementApp/Core/BackendClient.swift'
FILES = [
    'tools/parity_check.py', 'tools/mobile_release_contract.py', 'docs/PARITY.md',
    'engineering/phase2/test_backend_url_parity.py',
    'iOS/WoundMeasurementApp/Core/AppSettings.swift', 'Android/app/build.gradle',
    'docs/admin_operations.md', '.github/workflows/p0-4-audit.yml',
    'Android/version.properties', 'iOS/project.yml', SWIFT,
    'Android/app/src/main/java/com/woundmeasurement/app/pipeline/BackendClient.kt',
    'iOS/WoundMeasurementApp/InstitutionInfo.plist',
    'Android/app/src/main/java/com/woundmeasurement/app/institution/InstitutionSession.kt',
    'Backend/AccessManager/authentication.py',
    'Backend/AccessManager/accounts.py',
    'Backend/AccessManager/sessions.py',
    'Backend/AccessManager/web.py',
    'Backend/AccessManager/static/members.js',
]

class AnnotationParityBoundaryTests(unittest.TestCase):
    def run_copy(self, edit):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in FILES:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, target)
            source = root / SWIFT
            source.write_text(edit(source.read_text()))
            return subprocess.run([sys.executable, '-B', str(root/'tools/parity_check.py')],
                                  capture_output=True, text=True)

    def test_current_payload_has_no_undeclared_difference(self):
        result = self.run_copy(lambda s: s)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_response_status_is_not_request_field(self):
        result = self.run_copy(lambda s: s.replace('case "enqueued":', 'case "future_response_status":'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_new_request_field_is_still_detected(self):
        result = self.run_copy(lambda s: s.replace('return try JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])',
            'obj["unexpected_research_field"] = true\n        return try JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpected_research_field', result.stdout)

    def test_lost_payload_boundary_is_rejected(self):
        result = self.run_copy(lambda s: s.replace('func submitAnnotationPayload(', 'func renamedPayloadSender('))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('annotation_payload_boundary_missing', result.stdout)

if __name__ == '__main__':
    unittest.main(verbosity=2)
