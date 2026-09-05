"""Regression checks for the pre-clinical local queue archive tool.

The tool must neither escape ``archive/`` through a supplied label nor append a
legacy raw JSON audit line after the v4 chain has been introduced.
"""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Backend" / "Flask"))
import api_flywheel as fw


def load_archive_tool():
    path = ROOT / "engineering" / "phase2" / "archive_flywheel_queue.py"
    spec = importlib.util.spec_from_file_location("archive_flywheel_queue_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ArchiveToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="archive-tool-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "images").mkdir()
        (self.root / "quarantine").mkdir()
        (self.root / "staging").mkdir()
        (self.root / "images" / "sample.jpg").write_bytes(b"synthetic")
        (self.root / "retrain_queue.jsonl").write_text(
            json.dumps({"code": "WD-SYNTHETIC", "source": "phantom"}) + "\n",
            encoding="utf-8")
        (self.root / "withdrawn.jsonl").write_text("", encoding="utf-8")
        self.tool = load_archive_tool()

    def invoke(self, *args):
        argv = ["archive_flywheel_queue.py", "--flywheel-dir", str(self.root), *args]
        with patch.object(sys, "argv", argv), patch.dict(os.environ, {
            "WOUNDAI_STORE": "local",
            "WOUNDAI_FLYWHEEL_DIR": str(self.root),
        }, clear=False):
            # The production CLI imports api_flywheel only after setting the
            # requested root.  This test module imported it for verification,
            # so mirror a fresh CLI process rather than reusing that default.
            importlib.reload(fw)
            return self.tool.main()

    def test_unsafe_label_is_rejected_before_any_state_change(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        self.assertEqual(2, self.invoke("--label", "..", "--operator", "maintenance",
                                        "--authorization-ref", "synthetic approval"))
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        self.assertEqual(before, after)

    def test_archive_uses_versioned_intent_and_outcome_chain(self):
        self.assertEqual(0, self.invoke("--label", "pre_clinical_synthetic",
                                        "--operator", "maintenance",
                                        "--authorization-ref", "synthetic approval"))
        rows = [json.loads(line) for line in (self.root / "audit.jsonl").read_text(
            encoding="utf-8").splitlines()]
        self.assertEqual(["queue_archive_intent", "queue_archived"],
                         [row["action"] for row in rows])
        self.assertTrue(all(row.get("chain_v") == fw.CHAIN_V and row.get("hash")
                            for row in rows))
        ok, issues, _ = fw.verify_audit_chain(str(self.root / "audit.jsonl"))
        self.assertTrue(ok, issues)
        self.assertEqual("", (self.root / "retrain_queue.jsonl").read_text(encoding="utf-8"))
        self.assertTrue((self.root / "archive" / "pre_clinical_synthetic" / "NOTE.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
