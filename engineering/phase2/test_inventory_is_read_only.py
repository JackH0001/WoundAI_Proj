#!/usr/bin/env python3
"""The main-bucket inventory must only be able to look.

A tool that enumerates objects for deletion is one edit away from deleting them.
The protection is not a review habit, it is that the mutating verbs are not in
the file and a red test says so if they come back.

The behavioural tests below run the real argument handling and classification
with no credentials and no network: the Cloud Storage client is imported inside
main(), after the refusals, precisely so these can run anywhere.
"""
import ast
import io
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "engineering" / "phase2" / "inventory_main_bucket.py"
WORKFLOW = ROOT / ".github" / "workflows" / "p0-4-audit.yml"

import sys
sys.path.insert(0, str(TOOL.parent))
import inventory_main_bucket as inv  # noqa: E402

# Every Cloud Storage call that changes something. `delete` covers both
# Blob.delete and Bucket.delete; `compose` and `rewrite` can overwrite an object
# just as effectively as an upload.
MUTATING = (
    "delete", "upload_from_string", "upload_from_file", "upload_from_filename",
    "compose", "rewrite", "copy_blob", "patch", "update", "make_public",
    "create_bucket", "set_iam_policy", "add_lifecycle_rule",
)


class InventoryIsReadOnlyTests(unittest.TestCase):

    def test_no_mutating_cloud_storage_call_appears_in_the_tool(self):
        tree = ast.parse(TOOL.read_text(encoding="utf-8-sig"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in MUTATING:
                    found.append((node.func.attr, node.lineno))
        self.assertEqual(
            found, [],
            "the inventory tool must only read; found %s" % found)

    def test_the_only_thing_it_writes_is_the_local_manifest(self):
        tree = ast.parse(TOOL.read_text(encoding="utf-8-sig"))
        opens = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
        self.assertEqual(
            len(opens), 1,
            "expected exactly one open() -- the manifest; found %d" % len(opens))
        mode = [kw.value.value for kw in opens[0].keywords if kw.arg is None] or \
               [a.value for a in opens[0].args[1:2] if isinstance(a, ast.Constant)]
        self.assertEqual(mode, ["w"], "the manifest is the only write, in text mode")

    def test_an_audit_bucket_is_refused_by_name(self):
        for name in ("woundai-flywheel-jackh001-audit",
                     "woundai-flywheel-jackh001-audit-epoch-20260905",
                     "something-audit"):
            with self.assertRaises(SystemExit, msg=name) as caught:
                inv.refuse_audit_bucket(name)
            self.assertIn("audit", str(caught.exception))

    def test_the_main_bucket_is_not_refused(self):
        inv.refuse_audit_bucket("woundai-flywheel-jackh001")  # must not raise

    def test_protected_keys_can_never_become_candidates(self):
        for key in ("audit.jsonl", "audit.jsonl/0000000001", "receipts",
                    "receipts/promotion/x.json"):
            self.assertTrue(inv.is_protected(key), key)
        for key in ("images/abc.jpg", "retrain_queue.jsonl", "tissue_masks/a.png"):
            self.assertFalse(inv.is_protected(key), key)

    def test_a_window_without_a_timezone_is_refused(self):
        with self.assertRaises(SystemExit):
            inv.utc("2026-09-06T00:00:00", "--since")
        self.assertEqual(
            inv.utc("2026-09-06T00:00:00Z", "--since"),
            datetime(2026, 9, 6, tzinfo=timezone.utc))

    def test_reference_scan_errs_towards_keeping_objects(self):
        body = '\n'.join([
            '{"image_id": "a1b2c3d4e5f6a1b2", "note": "plain"}',
            '{"nested": {"deep": ["b7c8d9e0f1a2b3c4"]}}',
            'this line is not json but mentions images/c9d0e1f2.jpg',
        ])
        tokens = inv.referenced_tokens(body)
        self.assertIn("a1b2c3d4e5f6a1b2", tokens)
        self.assertIn("b7c8d9e0f1a2b3c4", tokens, "nested values must still count")
        self.assertIn("images/c9d0e1f2.jpg", tokens,
                      "an unparseable line must not make its objects look unreferenced")

    def test_workflow_runs_this_test_on_both_events(self):
        # The paths filter is per event. Checking the file as one blob would pass
        # while the push trigger had lost the path and only pull_request kept it --
        # a mutation of exactly that shape got through an earlier draft.
        # The extractor is imported rather than copied: a second implementation of
        # the same parsing is the parallel-list mistake in another costume.
        import test_backend_url_parity as urlparity
        self.assertIn(
            "python -B engineering/phase2/test_inventory_is_read_only.py",
            WORKFLOW.read_text(encoding="utf-8-sig"),
            "p0-4-audit.yml does not invoke this guard")
        for event in ("push", "pull_request"):
            entries = urlparity.workflow_trigger_paths(event)
            for path in ("engineering/phase2/inventory_main_bucket.py",
                         "engineering/phase2/test_inventory_is_read_only.py"):
                self.assertIn(
                    path, entries,
                    "p0-4-audit.yml `%s` does not trigger on %s, so a change to it "
                    "would merge without this guard running." % (event, path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
