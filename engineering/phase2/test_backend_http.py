# -*- coding: utf-8 -*-
"""Synthetic loopback HTTP validation for the P0-4 consent contract.

Start with tools/windows/run_backend_http_test.py. This client refuses remote
URLs, proxies, redirects and servers without a matching per-run response marker.
Only generated synthetic pixels are accepted, never an arbitrary patient photo.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import uuid
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_api_contract import validate


class TestRefused(RuntimeError):
    pass


def loopback_origin(url):
    """Accept only an unambiguous literal loopback origin; never resolve DNS."""
    try:
        parsed = urlsplit(url)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise TestRefused("URL must contain a literal loopback address") from exc
    if (parsed.scheme != "http" or not address.is_loopback
            or parsed.hostname not in ("127.0.0.1", "::1")
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or port is None or not 1 <= port <= 65535):
        raise TestRefused("Only http://127.0.0.1:PORT or http://[::1]:PORT is allowed")
    host = "[::1]" if address.version == 6 else "127.0.0.1"
    return "http://%s:%d" % (host, port)


class LocalTestClient:
    def __init__(self, url, run_id):
        self.origin = loopback_origin(url)
        if not re.fullmatch(r"[0-9a-f]{32,64}", run_id or ""):
            raise TestRefused("A random 32-64 lowercase hex test run ID is required")
        self.run_id = run_id
        self.session = requests.Session()
        self.session.trust_env = False
        self.verified = False

    def request(self, method, path, **kwargs):
        if not path.startswith("/api/") or "://" in path or "\\" in path:
            raise TestRefused("Request path must stay under the fixed local API origin")
        if not self.verified and (method != "GET" or path != "/api/health"):
            raise TestRefused("Verify the isolated server before sending credentials or writes")
        response = self.session.request(method, self.origin + path,
                                        allow_redirects=False, timeout=120, **kwargs)
        if 300 <= response.status_code < 400 or response.history:
            raise TestRefused("Redirects are forbidden during a write-capable test")
        if response.headers.get("X-WoundAI-Local-Test-Run") != self.run_id:
            raise TestRefused("Server did not prove the expected isolated test run")
        return response

    def verify_server(self):
        response = self.request("GET", "/api/health")
        if response.status_code != 200:
            raise TestRefused("Isolated backend health check failed")
        self.verified = True


def synthetic_jpeg(run_id):
    """Make pixels unique, then encode JPEG; never append EOI garbage."""
    import cv2
    import numpy as np
    pixels = np.full((256, 320, 3), 230, dtype=np.uint8)
    cv2.ellipse(pixels, (160, 128), (76, 52), 0, 0, 360, (45, 45, 200), -1)
    for i, value in enumerate(bytes.fromhex(run_id)):
        x = 5 + i * 4
        pixels[5:17, x:x + 4] = (value, 255 - value, (value * 7) % 256)
    success, encoded = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not success:
        raise TestRefused("Synthetic JPEG encoding failed")
    sys.path.insert(0, str(HERE.parents[1] / "Backend" / "Flask"))
    from image_canonical import canonicalize
    payload = encoded.tobytes()
    canonical = canonicalize(payload)
    if canonical.data != payload or canonical.pixels.shape != pixels.shape:
        raise TestRefused("Synthetic JPEG did not satisfy the exact canonical contract")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--synthetic", action="store_true", required=True)
    parser.add_argument("--test-run-id", required=True)
    parser.add_argument("--user", default="admin")
    args = parser.parse_args(argv)
    admin_password = os.environ.pop("WOUNDAI_HTTP_TEST_PASSWORD", "")
    if len(admin_password) < 32:
        raise TestRefused("A child-only synthetic HTTP password is required")
    client = LocalTestClient(args.url, args.test_run_id)
    payload = synthetic_jpeg(args.test_run_id)
    client.verify_server()
    failures = []

    def check(label, condition):
        print(("PASS " if condition else "FAIL ") + label)
        if not condition:
            failures.append(label)
        return condition

    def post(path, **kwargs):
        return client.request("POST", path, **kwargs)

    def login(user, password):
        response = post("/api/auth/login", json={"username": user, "password": password})
        if response.status_code != 200 or not response.json().get("access_token"):
            raise TestRefused("Isolated login failed: HTTP %s" % response.status_code)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    admin_headers = login(args.user, admin_password)
    physician = "http-test-" + args.test_run_id[:12]
    password = uuid.uuid4().hex + uuid.uuid4().hex
    response = post("/api/v1/users", headers=admin_headers,
                    json={"user": physician, "role": "physician", "password": password,
                          "display_name": "Synthetic isolated HTTP test"})
    if response.status_code != 200:
        raise TestRefused("Could not create isolated physician: HTTP %s" % response.status_code)
    headers = login(physician, password)
    code = "WD-T" + args.test_run_id[:12].upper()

    def classify(receipt=None):
        form = {"seg": "color"}
        if receipt:
            form["care_receipt"] = receipt
        response = post("/api/v1/classify", headers=headers,
                        files={"image": ("synthetic.jpg", payload, "image/jpeg")}, data=form)
        if response.status_code != 200:
            raise TestRefused("Synthetic classify failed: HTTP %s" % response.status_code)
        result = response.json()
        valid, issues = validate(result)
        check("classify response schema", valid)
        if not valid:
            print(issues)
        return result

    unconsented = classify()
    check("no care receipt: analysis only, no image identifier",
          unconsented.get("persisted") is False and unconsented.get("image_id") is None
          and unconsented.get("persistence_reason") == "care_receipt_required")
    response = post("/api/v1/consent/care/attest", headers=headers, json={"code": code})
    if response.status_code != 200 or not response.json().get("care_receipt"):
        raise TestRefused("Care attestation failed: HTTP %s" % response.status_code)
    result = classify(response.json()["care_receipt"])
    iid, iw, ih = result.get("image_id"), result.get("image_w"), result.get("image_h")
    if not check("care receipt stages the canonical image",
                 bool(iid) and iw == 320 and ih == 256 and result.get("persisted") is True
                 and result.get("persistence_reason") == "staged"):
        return 1
    check("synthetic route uses phantom colour segmentation",
          result.get("phantom_mode") is True
          and str(result.get("stage2_segment", {}).get("route", "")).startswith("phantom_color"))
    polygon = [[100, 90], [210, 90], [210, 170], [100, 170]]
    annotation = {"code": code, "gt_polygon": polygon, "exudate": 2,
                  "doctor_verified": True, "deidentified": True, "consent_train": True,
                  "image_id": iid, "image_w": iw, "image_h": ih, "source": "phantom",
                  "route": result["stage2_segment"]["route"],
                  "care_note": "synthetic isolated test " + args.test_run_id}

    def annotate(body, label, expected, status=None):
        response = post("/api/v1/annotation", headers=headers, json=body)
        actual_status = response.json().get("status") if response.status_code == 200 else None
        return check(label, response.status_code == expected
                     and (status is None or actual_status == status))

    def stats():
        response = client.request("GET", "/api/v1/flywheel/stats", headers=headers)
        if response.status_code != 200:
            raise TestRefused("Flywheel statistics unavailable")
        return response.json()

    initial = stats()
    annotate({**annotation, "consent_train": False}, "training consent denied before promotion", 400)
    check("denied training consent adds no trainable sample",
          stats().get("trainable", 0) == initial.get("trainable", 0))
    annotate({k: v for k, v in annotation.items() if k != "image_id"}, "orphan annotation rejected", 400)
    annotate({**annotation, "image_id": "d" * 40}, "missing image rejected", 400)
    annotate({**annotation, "gt_polygon": [[0, 0], [iw + 500, 0], [0, 10]]}, "out-of-bounds rejected", 400)
    annotate({**annotation, "exudate": 9}, "invalid exudate rejected", 400)
    annotate(annotation, "training consent promotes and enqueues", 200, "enqueued")
    annotate(annotation, "exact repeat is idempotent", 200, "duplicate_skipped")
    check("exactly one new trainable sample", stats().get("trainable", 0) == initial.get("trainable", 0) + 1)
    annotate({**annotation, "code": "WD-T" + uuid.uuid4().hex[:12].upper()},
             "promoted image cannot be rebound to another case", 400)
    response = post("/api/v1/consent/withdraw", headers=headers, json={"code": code})
    check("withdraw accepted", response.status_code == 200)
    withdrawn = stats()
    check("withdraw removes this sample from training",
          withdrawn.get("trainable", 0) == initial.get("trainable", 0)
          and withdrawn.get("withdrawn", 0) == initial.get("withdrawn", 0) + 1)
    annotate(annotation, "withdrawn annotation cannot be resubmitted", 400)
    response = post("/api/v1/consent/restore", headers=headers, json={"code": code})
    check("explicit renewed consent accepted", response.status_code == 200)
    annotate({**annotation, "gt_polygon": polygon[::-1]}, "renewed consent permits annotation", 200)
    print("HTTP synthetic contract: %s (%d failures)" % ("PASS" if not failures else "FAIL", len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TestRefused, requests.RequestException) as exc:
        print("REFUSE/FAIL:", exc)
        raise SystemExit(2)
