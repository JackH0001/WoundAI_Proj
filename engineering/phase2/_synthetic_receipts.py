"""Temporary legacy fixtures ONLY. Not an owner approval or migration tool."""
import json
from pathlib import Path
import tempfile


def ratify_synthetic(root, image_ids):
    root = Path(root).resolve()
    if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("synthetic receipts may only be written under the test temp directory")
    path = root / "receipts" / "legacy_ratification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(path.read_text(encoding="utf-8"))["image_ids"] if path.exists() else []
    path.write_text(json.dumps({"schema": "woundai.legacy-ratification/1",
                               "approved_by": "SYNTHETIC TEST FIXTURE - NOT AN OWNER APPROVAL",
                               "synthetic": True, "image_ids": sorted(set(old) | set(image_ids))}), encoding="utf-8")
