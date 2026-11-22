import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_lite_native_revision import Mismatch, check, parse


def example():
    project = "synthetic-lite"
    number = "987654321"
    digest = "a" * 64
    source = "b" * 40
    image = f"asia-east1-docker.pkg.dev/{project}/test/native@sha256:{digest}"
    revision = f"projects/{project}/locations/asia-east1/services/woundai-lite-native-sandbox/revisions/test-01"
    account = f"test-native@{project}.iam.gserviceaccount.com"
    files = {"config": "/etc/lite/config/settings.json", **{
        role: f"/etc/lite/inputs/{role}/value" for role in ("api", "oauth", "identity", "root-1")}}
    settings = {
        "schema": "lite-native-startup/3", "enabled": True,
        "deployment": dict(project=project, project_number=number, image=digest, source=source, service_account=account),
        "app": dict(bundle="com.woundai.lite", app_id=6825208065, version="54", team="TESTTEAM01",
                    attest_audience="woundlite-native-sandbox-v1"),
        "apple": dict(product="com.woundai.lite.plus.monthly", environment="Sandbox", roots=[{"file": "value"}]),
        "secrets": {name: {"file": "value"} for name in ("api", "oauth", "identity")},
    }
    raw = json.dumps(settings).encode()
    plan = dict(build="54", team="TESTTEAM01", revision=revision, settings_sha256=hashlib.sha256(raw).hexdigest(),
                image=image, secret_versions={k: f"projects/{project}/secrets/test-{k}/versions/2" for k in files})
    variables = dict(LITE_NATIVE_CONFIG=files["config"], LITE_NATIVE_SECRET_DIR="/etc/lite/inputs",
                     LITE_NATIVE_CONFIG_SHA256=plan["settings_sha256"], LITE_NATIVE_SOURCE=source, LITE_NATIVE_IMAGE_SHA256=digest)
    spec = dict(name=revision, serviceAccount=account, containers=[dict(image=image,
                env=[dict(name=k,value=v) for k,v in variables.items()], volumeMounts=[])], volumes=[])
    for role, path in files.items():
        folder, filename = path.rsplit("/", 1)
        spec["containers"][0]["volumeMounts"].append(dict(name=role, mountPath=folder))
        spec["volumes"].append(dict(name=role, secret=dict(secret="test-"+role, items=[dict(version="2", path=filename)])))
    return plan, spec, raw


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.rev, self.raw = example()
        self.container = self.rev["containers"][0]

    def verify(self):
        return check(self.plan, self.rev, self.raw)

    def test_matching_configuration_with_reordered_rows_and_qualified_reference(self):
        self.rev["volumes"][0]["secret"]["secret"] = "projects/987654321/secrets/test-config"
        self.rev["volumes"].reverse()
        self.container["env"].reverse()
        self.assertEqual(self.verify()["matched_roles"], ["api", "config", "identity", "oauth", "root-1"])

    def test_source_build_image_configuration_and_service_identity(self):
        variants = (
            (self.plan,"build","46"), (self.plan,"team","TESTTEAM02"), (self.plan,"revision",self.plan["revision"]+"1"),
            (self.plan,"settings_sha256","0"*64), (self.plan,"image",self.plan["image"]+"0"),
            (self.rev,"serviceAccount","other"), (self.container,"image","native:latest"),
            (self.container["env"][-2],"value","1"*40), (self.container["env"][-1],"value","2"*64))
        for obj, key, new in variants:
            with self.subTest(key=key):
                old = obj[key]
                obj[key] = new
                with self.assertRaises(Mismatch):
                    self.verify()
                obj[key] = old

    def test_internal_and_enrollment_modes(self):
        for k in ["LITE_INTERNAL_OWNER_MODE","LITE_OWNER_ENROLLMENT_PLAN","LITE_INTERNAL_INSTALLATION_SHA256"]:
            with self.subTest(k=k):
                self.container["env"].append(dict(name=k,value="owner-only"))
                with self.assertRaisesRegex(Mismatch,"owner-only"):
                    self.verify()
                self.container["env"].pop()

    def test_key_role_and_version(self):
        sec = self.rev["volumes"][1]["secret"]
        original = copy.deepcopy(sec)
        for name, version in [("test-oauth","2"),("test-api","3"),("test-api","latest"),("projects/other/secrets/test-api","2")]:
            with self.subTest(name=name,version=version):
                sec.update(secret=name,items=[dict(version=version,path="value")])
                with self.assertRaises(Mismatch):
                    self.verify()
        sec.update(original)
        self.verify()

    def test_mounts_and_filenames(self):
        mount = self.container["volumeMounts"][2]
        for path in ["/etc/lite/inputs/api","/etc/lite","/elsewhere","/etc/lite/inputs/oauth/../oauth"]:
            with self.subTest(path=path):
                mount["mountPath"] = path
                with self.assertRaises(Mismatch):
                    self.verify()
        mount["mountPath"] = "/etc/lite/inputs/oauth"
        self.rev["volumes"][2]["secret"]["items"][0]["path"] = "other"
        with self.assertRaisesRegex(Mismatch,"filename"):
            self.verify()

    def test_sidecar_command_override_and_env_secret(self):
        self.rev["containers"].append({"image":"synthetic"})
        with self.assertRaises(Mismatch):
            self.verify()
        self.rev["containers"].pop()
        self.container["command"] = ["other"]
        with self.assertRaises(Mismatch):
            self.verify()
        self.container.pop("command")
        self.container["env"].append(dict(name="EXTRA",valueSource={"secretKeyRef":{}}))
        with self.assertRaises(Mismatch):
            self.verify()

    def test_shadow_and_subpath(self):
        self.rev["volumes"].append(dict(name="scratch",emptyDir={}))
        mounts = self.container["volumeMounts"]
        mounts.append(dict(name="scratch",mountPath="/etc/lite/inputs/api/value"))
        with self.assertRaisesRegex(Mismatch,"overlap"):
            self.verify()
        mounts.pop()
        mounts[1]["subPath"] = "other"
        with self.assertRaisesRegex(Mismatch,"subpath"):
            self.verify()

    def test_separate_research_link_requires_reviewed_mount(self):
        self.rev["volumes"].append(dict(name="link",secret=dict(secret="test-link",items=[dict(path="issuer",version="3")])))
        self.container["volumeMounts"].append(dict(name="link",mountPath="/etc/lite/link"))
        with self.assertRaisesRegex(Mismatch,"mount set"):
            self.verify()
        self.plan["additional_secret_files"] = {"/etc/lite/link/issuer":"projects/synthetic-lite/secrets/test-link/versions/3"}
        self.assertIn("additional-1", self.verify()["matched_roles"])

    def test_duplicate_or_incomplete_approved_evidence(self):
        with self.assertRaises(Mismatch):
            parse('{"version":"1","version":"2"}')
        self.container["env"].append(self.container["env"][0].copy())
        with self.assertRaises(Mismatch):
            self.verify()
        self.container["env"].pop()
        del self.plan["secret_versions"]["identity"]
        with self.assertRaises(Mismatch):
            self.verify()

    def test_two_apple_roles_cannot_share_a_secret_even_on_distinct_versions(self):
        self.plan["secret_versions"]["oauth"] = self.plan["secret_versions"]["api"].replace("/2", "/9")
        self.rev["volumes"][2]["secret"].update(secret="test-api", items=[dict(path="value",version="9")])
        with self.assertRaisesRegex(Mismatch,"separate secrets"):
            self.verify()

    def test_unused_secret_volume_cannot_be_added(self):
        self.rev["volumes"].append(dict(name="extra",secret=dict(secret="test-unused")))
        with self.assertRaisesRegex(Mismatch,"unused"):
            self.verify()

    def test_cli_and_missing_resource(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [Path(d)/s for s in ("plan","revision","settings")]
            command = [sys.executable, "-B", str(Path(__file__).with_name("check_lite_native_revision.py")), *paths]
            paths[0].write_text(json.dumps(self.plan))
            paths[1].write_text(json.dumps(self.rev))
            paths[2].write_bytes(self.raw)
            ok = subprocess.run(command, capture_output=True, check=True)
            self.assertEqual(json.loads(ok.stdout)["build"], "54")
            paths[1].write_text('{"error":{"code":404,"message":"private diagnostic text"}}')
            failed = subprocess.run(command, capture_output=True)
            self.assertEqual(failed.returncode, 1)
            self.assertFalse(failed.stdout)
            self.assertNotIn(b"private diagnostic text",failed.stderr)
