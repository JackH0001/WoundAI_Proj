"""Synthetic Cloud Run v2 readbacks for the dedicated owner registration phase."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_lite_owner_enrollment_revision import Mismatch, check


def fixture(now):
    project = "synthetic-lite"
    number = "987654321"
    short = "woundai-lite-native-sandbox-enroll"
    service_name = f"projects/{project}/locations/asia-east1/services/woundai-lite-native-sandbox"
    origin = f"https://woundai-lite-native-sandbox-{number}.asia-east1.run.app"
    revision_name = f"{service_name}/revisions/{short}"
    scope = dict(schema="lite-owner-enrollment-scope/1", region="asia-east1", starts=now, until=now+600,
                 image="a"*64, source="b"*40, native_sha256="c"*64, plan_sha256="d"*64,
                 project=project, project_number=number, build="60",
                 service_account=f"enroll-only@{project}.iam.gserviceaccount.com")
    scope_raw = json.dumps(scope).encode()
    files = {key: dict(path=f"/etc/lite/{key}/value", version=f"projects/{project}/secrets/test-{key}/versions/1")
             for key in ("native", "enrollment")}
    release = dict(schema="lite-owner-enrollment-revision/1", scope_sha256=hashlib.sha256(scope_raw).hexdigest(),
                   revision=revision_name, files=files)
    variables = dict(LITE_INTERNAL_OWNER_MODE="owner-enrollment", LITE_NATIVE_CONFIG=files["native"]["path"],
                     LITE_NATIVE_CONFIG_SHA256=scope["native_sha256"], LITE_NATIVE_SOURCE=scope["source"],
                     LITE_NATIVE_IMAGE_SHA256=scope["image"], LITE_OWNER_ENROLLMENT_PLAN=files["enrollment"]["path"],
                     LITE_OWNER_ENROLLMENT_PLAN_SHA256=scope["plan_sha256"])
    container = dict(image=f"asia-east1-docker.pkg.dev/{project}/woundai-lite-candidate/native-sandbox@sha256:{scope['image']}",
                     env=[dict(name=k, value=v) for k, v in variables.items()],
                     volumeMounts=[dict(name=k, mountPath=f"/etc/lite/{k}") for k in files])
    revision = dict(name=revision_name, generation="1", observedGeneration="1",
                    conditions=[dict(type="Ready", state="CONDITION_SUCCEEDED")], serviceAccount=scope["service_account"],
                    containers=[container], volumes=[dict(name=k, secret=dict(secret="test-"+k,
                                                     items=[dict(path="value",version="1")])) for k in files])
    target = dict(type="TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", revision=short, percent=100)
    service = dict(name=service_name, generation="2", observedGeneration="2", uri=origin,
                   terminalCondition=dict(state="CONDITION_SUCCEEDED"), ingress="INGRESS_TRAFFIC_ALL",
                   traffic=[target.copy()], trafficStatuses=[target.copy()])
    return release, scope_raw, revision, service


class EnrollmentReadbackTests(unittest.TestCase):
    def setUp(self):
        self.now = 20000
        self.inputs = fixture(self.now)
        self.release, self.raw, self.rev, self.service = self.inputs
        self.container = self.rev["containers"][0]

    def verify(self, at=None):
        return check(*self.inputs, clock=lambda:self.now if at is None else at)

    def reject_changes(self, obj, changes):
        for key, value in changes.items():
            with self.subTest(field=key):
                old = copy.deepcopy(obj)
                obj[key] = value
                try:
                    with self.assertRaises(Mismatch):
                        self.verify()
                finally:
                    obj.clear()
                    obj.update(old)

    def test_ready_revision_inside_window(self):
        r = self.verify()
        self.assertEqual((r["build"], r["seconds_remaining"], r["serving"]["percent"]), ("60",600,100))
        self.assertNotIn("service_account",r)

    def test_closed_before_start_and_at_exact_end(self):
        for t in (self.now-1, self.now+600, self.now+601):
            with self.subTest(t=t), self.assertRaisesRegex(Mismatch,"interval"):
                self.verify(t)
        self.assertEqual(self.verify(self.now+599)["seconds_remaining"],1)

    def test_changed_scope_revision_and_dedicated_identity(self):
        self.reject_changes(self.release, dict(schema="other", scope_sha256="f"*64, revision=self.rev["name"]+"2"))
        self.reject_changes(self.rev, dict(serviceAccount="business", deleteTime="now", reconciling=True,
                                           generation="2", conditions=[], observedGeneration=None))

    def test_wrong_image_command_and_extra_container(self):
        self.reject_changes(self.container, dict(image="native:latest", command=["other"], args=["other"]))
        self.rev["containers"].append({"name":"sidecar"})
        with self.assertRaises(Mismatch):
            self.verify()

    def test_extra_secret_source_and_overridden_environment_rejected(self):
        env = self.container["env"]
        for extra in (dict(name="LITE_NATIVE_SECRET_DIR",value="/etc/keys"), dict(name="OTHER",value="on"),
                      dict(name="LITE_RESEARCH_CAPTURE_ENABLED",value="1"),env[0].copy()):
            env.append(extra)
            with self.subTest(extra=extra["name"]), self.assertRaises(Mismatch):
                self.verify()
            env.pop()
        self.reject_changes(env[0], dict(value="owner-review",valueSource={"secretKeyRef":{}}))
        env.append(dict(name="LITE_RESEARCH_CAPTURE_ENABLED",value="0"))
        self.verify()

    def test_only_two_expected_versions_and_mounts(self):
        volume = self.rev["volumes"][0]
        self.reject_changes(volume["secret"], dict(secret="projects/other/secrets/test-native",
             items=[dict(path="value", version="latest")]))
        self.container["volumeMounts"][0]["subPath"] = "other"
        with self.assertRaises(Mismatch):
            self.verify()
        del self.container["volumeMounts"][0]["subPath"]
        self.rev["volumes"].append(dict(name="extra",secret=dict(secret="extra")))
        with self.assertRaises(Mismatch):
            self.verify()

    def test_mounts_swapped_or_nested_rejected(self):
        self.reject_changes(self.container["volumeMounts"][0],dict(mountPath="/etc/lite/enrollment"))
        self.release["files"]["enrollment"]["path"] = "/etc/lite/native/child/value"
        with self.assertRaisesRegex(Mismatch,"overlap"):
            self.verify()

    def test_wrong_traffic_pending_tag_or_missing_observation_rejected(self):
        self.reject_changes(self.service,dict(observedGeneration="1",trafficStatuses=[],uri="https://elsewhere.invalid"))
        self.reject_changes(self.service["trafficStatuses"][0],dict(revision="previous",percent=50))
        self.service["traffic"].append(dict(type="TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",revision="old",tag="old"))
        with self.assertRaisesRegex(Mismatch,"tagged"):
            self.verify()

    def test_required_explicit_versions_and_separate_secrets(self):
        self.release["files"]["native"]["version"] = self.release["files"]["enrollment"]["version"]
        with self.assertRaisesRegex(Mismatch,"separate"):
            self.verify()

    def probe(self):
        return dict(method="POST",url=self.service["uri"]+"/api/v1/lite/attest/challenge",
                    scope_sha256=self.release["scope_sha256"],service_generation=self.service["generation"],
                    observed_at=self.now,status=403,body={"error":"lite_owner_enrollment_closed"},
                    content_type="application/json; charset=utf-8",cache_control="private, no-store")

    def test_probe_observation_is_distinct_from_metadata(self):
        self.assertNotIn("observed_enrollment_http", self.verify())
        result = check(*self.inputs, clock=lambda:self.now, probe=self.probe())
        self.assertEqual(result["observed_enrollment_http"], {"status":403,"observed_at":self.now})

    def test_generic_403_or_missing_backend_never_confirms_connectivity(self):
        for change in (dict(status=404),dict(status=503),dict(content_type="text/html"),dict(body={"error":"forbidden"}),
                       dict(cache_control="public"),dict(body={"error":"lite_owner_enrollment_closed","extra":1})):
            with self.subTest(change=change), self.assertRaises(Mismatch):
                check(*self.inputs, clock=lambda:self.now, probe=dict(self.probe(),**change))

    def test_stale_or_wrong_target_probe_is_rejected(self):
        for change in (dict(observed_at=self.now-1),dict(observed_at=self.now+1),dict(scope_sha256="0"*64),
                       dict(service_generation="1"),dict(method="GET"),dict(url=self.service["uri"]+"/")):
            with self.subTest(change=change), self.assertRaises(Mismatch):
                check(*self.inputs, clock=lambda:self.now, probe=dict(self.probe(),**change))

    def test_cli_pins_release_and_checks_private_scope_without_printing_input(self):
        now = self.now
        release, raw, revision, service = fixture(now)
        scope = json.loads(raw)
        scope["synthetic_private_note"] = "kept-out-of-output"
        raw = json.dumps(scope).encode()
        release["scope_sha256"] = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp)/str(i) for i in range(4)]
            for path, payload in zip(paths, (json.dumps(release).encode(),raw,json.dumps(revision).encode(),json.dumps(service).encode())):
                path.write_bytes(payload)
            sha = hashlib.sha256(paths[0].read_bytes()).hexdigest()
            checker = Path(__file__).with_name("check_lite_owner_enrollment_revision.py")
            # Run the actual CLI at a fixed instant, including on a CI worker near UTC midnight.
            bootstrap = ("import runpy, sys, time; time.time=lambda:20000;"
                         "sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:];"
                         "runpy.run_path(sys.argv[0],run_name='__main__')")
            cmd = [sys.executable, "-B", "-c", bootstrap, str(checker.parent), str(checker), *paths, "--release-sha256"]
            ok = subprocess.run(cmd+[sha],capture_output=True,check=True)
            self.assertNotIn(b"kept-out-of-output",ok.stdout)
            self.assertEqual(json.loads(ok.stdout)["build"],"60")
            failed = subprocess.run(cmd+["0"*64],capture_output=True)
            self.assertEqual((failed.returncode, failed.stdout), (1,b""))
