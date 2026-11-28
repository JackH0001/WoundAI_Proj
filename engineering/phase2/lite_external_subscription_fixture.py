"""Signed HTTP acceptance using fresh installations and the requested release's app identity."""
import base64
import copy
import hashlib
import io
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image

from lite_attest_assertion import AssertionPolicy
from lite_attest_http import CHALLENGE_PATH, REGISTER_PATH
from lite_attest_state import SQLiteStateStore
from lite_native_startup import RuntimeKMSSession
from lite_rotating_grant_keys import SHARED_SCHEME
from test_lite_native_backend import NativeBackendTests, rows
from test_lite_rotating_grant_keys import MultiVersionKMS
from test_lite_signed_client import b64


def verify_external_subscription(case, *, team, bundle, build, audience):
    """Run on a fresh FirstInstallTests fixture. All provider material is synthetic."""
    h = case.h
    stores = h.all_stores
    h.isolate_secret_mounts()
    h.config["app"].update(team=team, bundle=bundle, version=build, attest_audience=audience)
    h.config["schema"] = "lite-native-startup/3"
    key = h.config["deployment"]["keyring"] + "/cryptoKeys/test-shared"
    version = key + "/cryptoKeyVersions/1"
    h.config["grant_keys"] = dict(scheme=SHARED_SCHEME, crypto_key=key, write_version=version,
                                  read_versions=[version])
    h.kms = MultiVersionKMS()
    h.kms.provision(version)
    h.providers.kms = RuntimeKMSSession(h.kms)
    stores["security"] = SQLiteStateStore(h.secret_dir.parent / "fresh-devices.db")
    case.assertEqual(rows(stores["security"]), [])
    case.controller.apply(case.approval("publish"))
    backend = h.compose()
    case.assertEqual(backend.security.admission.policy.allowed_bundle_versions, {build})
    case.assertEqual(backend.security.admission.policy.app_id, team + "." + bundle)
    case.assertEqual(backend.security.admission.environment, "production")
    case.assertEqual(backend.purchases.authority.policy.environment, "Sandbox")
    case.assertEqual(backend.purchases.authority.policy.product, "com.woundai.lite.plus.monthly")
    image = io.BytesIO()
    Image.new("RGB", (64, 64), (160, 85, 100)).save(image, format="JPEG")
    fixture = SimpleNamespace(jpeg=image.getvalue())

    def sign_in(subject, serial):
        device = NativeBackendTests()
        device.h, device.backend = h, backend
        device.client = backend.create_app().test_client()
        device.f = fixture
        device.image_hash = hashlib.sha256(fixture.jpeg).hexdigest()
        device.audience, device.bundle_version = audience, build
        device.counter, device.login_serial = 0, serial
        # Keep the local certificate authorities. Each installation has a new private key.
        e = device.e = copy.copy(h.e)
        e.r, e.cms = copy.copy(h.e.r), copy.copy(h.e.cms)
        e.r.key = ec.generate_private_key(ec.SECP256R1())
        e.r.point = e.r.key.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint)
        e.r.policy = AssertionPolicy(team + "." + bundle, frozenset({2}), frozenset({build}))
        e.r.bundle_version = build
        e.cms.app_id = e.r.policy.app_id
        e.key_id = e.r.key_id = hashlib.sha256(e.r.point).digest()
        device.key = b64(e.key_id)
        challenge = device.client.post(CHALLENGE_PATH, json=dict(key_id=device.key, purpose="register"))
        case.assertEqual(challenge.status_code, 200, challenge.json)
        case.assertEqual(challenge.json["audience"], audience)
        proof = e.make_proof(challenge=base64.b64decode(challenge.json["challenge"], validate=True))
        registered = device.client.post(REGISTER_PATH, json=dict(key_id=device.key,
            challenge_id=challenge.json["challenge_id"], attestation=b64(proof)))
        case.assertEqual(registered.status_code, 200, registered.json)
        device.owner = registered.json["installation"]
        device.login(subject)
        return device

    def balance(device):
        result = device.post("service/balance", account_sha256=device.login_result["account_sha256"])
        case.assertEqual(result.status_code, 200, result.json)
        return [result.json[k] for k in ("limit", "used", "pending", "remaining")]

    def context(device, op):
        result = device.post("purchase/context", operation=op)
        case.assertEqual(result.status_code, 200, result.json)
        return result.json["app_account_token"]

    first = sign_in("synthetic-external-A", 100)
    other = sign_in("synthetic-external-B", 200)
    case.assertNotEqual(first.login_result["account_sha256"], other.login_result["account_sha256"])
    case.assertEqual(balance(first), [2, 0, 0, 2])
    case.assertEqual(balance(other), [2, 0, 0, 2])

    case.assertEqual(rows(stores["quota"]), [])
    case.assertEqual(h.model_calls, 0)

    original_result = first.complete(1).json
    first.complete(2)
    other.complete(1)
    case.assertEqual(first.json_request("prepare", 3).status_code, 429)
    apple = h.release.n.a
    op = "a" * 32
    purchase_token = context(first, op)
    apple.payload["appAccountToken"] = purchase_token
    transaction = apple.sign(apple.payload)

    def confirm(device, operation):
        return device.post("purchase/confirm", operation=operation, signed_transaction=transaction)

    purchased = confirm(first, op)
    case.assertEqual(purchased.status_code, 200, purchased.json)
    case.assertEqual(balance(first), [10, 2, 0, 8])
    other_op = "b" * 32
    case.assertNotEqual(context(other, other_op), purchase_token)
    case.assertEqual(confirm(other, other_op).status_code, 409)
    case.assertEqual(balance(other), [2, 1, 0, 1])
    audit = rows(stores["purchases_audit"])
    case.assertEqual(len(audit), 1)

    backend = h.compose()
    for device in (first, other):
        device.backend = backend
        device.client = backend.create_app().test_client()
    restored = sign_in("synthetic-external-A", 300)
    case.assertEqual(len({d.owner for d in (first, other, restored)}), 3)
    case.assertEqual(restored.login_result["account_sha256"], first.login_result["account_sha256"])
    restore_op = "c" * 32
    case.assertEqual(context(restored, restore_op), purchase_token)
    for device, operation in ((restored, restore_op), (first, op)):
        reply = confirm(device, operation)
        case.assertEqual(reply.status_code, 200, reply.json)
        case.assertEqual(reply.json, purchased.json)
    case.assertEqual(rows(stores["purchases_audit"]), audit)
    replay = first.run_request(1)
    case.assertEqual(replay.status_code, 200, replay.json)
    case.assertEqual(replay.json, original_result)
    case.assertEqual(balance(restored), [10, 2, 0, 8])
    restored.complete(4)
    case.assertEqual(balance(first), [10, 3, 0, 7])
    case.assertEqual(balance(other), [2, 1, 0, 1])
    case.assertEqual(rows(stores["privacy"]), [])
    case.assertEqual(h.model_calls, 4)
    return dict(build=build, installations=3, accounts=2, model_calls=h.model_calls,
                purchase_receipts=len(audit), purchase_replay_same_receipt=True,
                restore_after_runtime_restart=True, research_privacy_rows=0,
                quota_fields=["limit", "used", "pending", "remaining"],
                paid_after_restore_and_one_more_use=balance(first), free_after_restore=balance(other))
