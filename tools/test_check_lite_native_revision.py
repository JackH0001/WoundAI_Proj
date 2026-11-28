import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_lite_native_revision import Mismatch, check, check_owner, parse


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
    plan = dict(build="54", team="TESTTEAM01", worker_startup="http-worker/1", revision=revision, settings_sha256=hashlib.sha256(raw).hexdigest(),
                image=image, secret_versions={k: f"projects/{project}/secrets/test-{k}/versions/2" for k in files})
    variables = dict(LITE_NATIVE_CONFIG=files["config"], LITE_NATIVE_SECRET_DIR="/etc/lite/inputs",
                     LITE_NATIVE_CONFIG_SHA256=plan["settings_sha256"], LITE_NATIVE_SOURCE=source, LITE_NATIVE_IMAGE_SHA256=digest)
    spec = dict(name=revision, serviceAccount=account, containers=[dict(image=image, startupProbe={"httpGet": {"path": "/startupz"}},
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

    def test_startup_probe_requires_the_exact_worker_and_http_port(self):
        for probe in (None, {"tcpSocket": {"port": 8080}}, {"grpc": {"port": 8080}},
                      {"httpGet": {"path": "/"}}, {"httpGet": {"path": "/startupz", "port": 8888}},
                      {"httpGet": {"path": "/startupz", "httpHeaders": [{"name": "Test", "value": "1"}]}},
                      {"tcpSocket": {}, "httpGet": {"path": "/startupz"}}):
            with self.subTest(probe=probe):
                self.container["startupProbe"] = probe
                with self.assertRaisesRegex(Mismatch, "HTTP startup probe"):
                    self.verify()
        self.container["startupProbe"] = {"httpGet": {"path": "/startupz", "port": 8080, "httpHeaders": []}}
        self.container["ports"] = [{"name": "http1", "containerPort": 8080}]
        self.verify()
        for port in ({"name": "h2c", "containerPort": 8080}, {"containerPort": 8000}):
            with self.subTest(port=port):
                self.container["ports"] = [port]
                with self.assertRaisesRegex(Mismatch, "HTTP/1 on port"):
                    self.verify()

    def test_historical_check_is_explicit_and_cannot_downgrade_new_contract(self):
        self.container["startupProbe"] = {"tcpSocket": {"port": 8080}}
        with self.assertRaisesRegex(Mismatch, "HTTP startup probe"):
            check(self.plan, self.rev, self.raw, historical=True)
        del self.plan["worker_startup"]
        with self.assertRaisesRegex(Mismatch, "contract required"):
            self.verify()
        result = check(self.plan, self.rev, self.raw, historical=True)
        self.assertEqual(result["worker_startup"], "unverified-historical")

    def test_deployment_probe_template_matches_provider_readback(self):
        file = Path(__file__).resolve().parents[1] / "deployment" / "lite-native-startup-probe.json"
        self.container["startupProbe"] = json.loads(file.read_bytes())
        self.assertEqual(self.verify()["worker_startup"], "http-worker/1-configured")

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

    def test_additional_secret_requires_reviewed_mount(self):
        self.rev["volumes"].append(dict(name="link",secret=dict(secret="test-link",items=[dict(path="issuer",version="3")])))
        self.container["volumeMounts"].append(dict(name="link",mountPath="/etc/lite/link"))
        with self.assertRaisesRegex(Mismatch,"mount set"):
            self.verify()
        self.plan["additional_secret_files"] = {"/etc/lite/link/issuer":"projects/synthetic-lite/secrets/test-link/versions/3"}
        self.assertIn("additional-1", self.verify()["matched_roles"])


    def research_plan(self):
        path = "/etc/lite/link/issuer"
        pin = "c" * 64
        self.rev["volumes"].append(dict(name="link", secret=dict(secret="test-link", items=[dict(path="issuer", version="3")])))
        self.container["volumeMounts"].append(dict(name="link", mountPath="/etc/lite/link"))
        self.plan["additional_secret_files"] = {path: "projects/synthetic-lite/secrets/test-link/versions/3"}
        self.plan["research_link"] = dict(file=path, sha256=pin, capture_enabled=True)
        self.container["env"].extend([
            dict(name="LITE_RESEARCH_LINK_FILE", value=path),
            dict(name="LITE_RESEARCH_LINK_SHA256", value=pin),
            dict(name="LITE_RESEARCH_CAPTURE_ENABLED", value="1"),
        ])

    def test_research_link_and_capture_match_review(self):
        self.research_plan()
        self.assertTrue(self.verify()["research_link"]["capture_enabled"])
        self.plan["research_link"]["capture_enabled"] = False
        self.container["env"].pop()
        self.assertFalse(self.verify()["research_link"]["capture_enabled"])

    def test_research_settings_must_reach_the_reviewed_file(self):
        self.research_plan()
        entries = {e["name"]:e for e in self.container["env"]}
        for key, wrong in (("LITE_RESEARCH_LINK_FILE", "/etc/other/issuer"),
                           ("LITE_RESEARCH_LINK_SHA256", "d"*64),
                           ("LITE_RESEARCH_CAPTURE_ENABLED", "0")):
            with self.subTest(key=key):
                value = entries[key]["value"]
                entries[key]["value"] = wrong
                with self.assertRaises(Mismatch):
                    self.verify()
                entries[key]["value"] = value
        self.container["env"].remove(entries["LITE_RESEARCH_LINK_FILE"])
        with self.assertRaises(Mismatch):
            self.verify()

    def test_reviewed_research_file_must_have_a_pinned_volume(self):
        self.research_plan()
        self.plan["additional_secret_files"] = {}
        with self.assertRaises(Mismatch):
            self.verify()

    def test_research_issuer_version_must_match_and_capture_flag_is_boolean(self):
        self.research_plan()
        volume = self.rev["volumes"][-1]["secret"]
        volume["items"][0]["version"] = "4"
        with self.assertRaisesRegex(Mismatch, "secret version"):
            self.verify()
        volume["items"][0]["version"] = "3"
        self.plan["research_link"]["capture_enabled"] = 1
        with self.assertRaisesRegex(Mismatch, "capture setting"):
            self.verify()

    def test_null_research_plan_is_incomplete(self):
        self.plan["research_link"] = None
        with self.assertRaises(Mismatch):
            self.verify()

    def test_research_settings_without_review_are_rejected(self):
        self.research_plan()
        del self.plan["research_link"]
        with self.assertRaises(Mismatch):
            self.verify()

    def test_disabled_research_without_link_keeps_existing_plans_usable(self):
        self.container["env"].append(dict(name="LITE_RESEARCH_CAPTURE_ENABLED", value="0"))
        self.assertNotIn("research_link", self.verify())

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


class ServingFixture:
    def setUp(self):
        self.plan, self.rev, self.raw = example()
        self.revision = self.rev["name"]
        self.origin = "https://woundai-lite-native-sandbox-987654321.asia-east1.run.app"
        self.plan["serving_origin"] = self.origin
        self.target = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": "test-01", "percent": 100}
        self.service = {"name": self.revision.rsplit("/revisions/", 1)[0], "generation": "15",
                        "observedGeneration": "15", "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
                        "latestReadyRevision": self.revision, "ingress": "INGRESS_TRAFFIC_ALL",
                        "uri": "https://synthetic-alias.a.run.app", "urls": [self.origin],
                        "traffic": [self.target.copy()], "trafficStatuses": [self.target.copy()]}


class ServingTests(ServingFixture, unittest.TestCase):
    def verify(self):
        return check(self.plan, self.rev, self.raw, service=self.service)["serving"]

    def test_stable_origin_uses_reviewed_revision(self):
        result = self.verify()
        self.assertEqual(result["origin"], self.origin)
        self.assertEqual(result["generation"], "15")
        self.assertEqual(result["percent"], 100)

    def test_revision_only_cannot_certify_plan_with_serving_origin(self):
        with self.assertRaisesRegex(Mismatch, "service readback required"):
            check(self.plan, self.rev, self.raw)

    def test_positive_old_traffic_fails_even_when_new_revision_ready(self):
        old = dict(self.target, revision="old")
        for field in ("traffic", "trafficStatuses"):
            self.service[field] = [old]
            with self.subTest(field=field), self.assertRaises(Mismatch):
                self.verify()
            self.service[field] = [self.target]

    def test_split_or_missing_observation_is_rejected(self):
        for rows in ([], [dict(self.target, percent=99)],
                     [dict(self.target, percent=50), dict(self.target, revision="old", percent=50)],
                     [dict(self.target, percent=True)], [dict(self.target, percent=100.0)],
                     [dict(self.target, percent=-1), dict(self.target, percent=101)]):
            self.service["trafficStatuses"] = rows
            with self.subTest(rows=rows), self.assertRaises(Mismatch):
                self.verify()

    def test_implicit_and_explicit_latest_require_matching_observed_revision(self):
        latest = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}
        self.service["trafficStatuses"] = [dict(latest, revision="test-01")]
        for desired in (None, [], [latest]):
            if desired is None:
                self.service.pop("traffic", None)
            else:
                self.service["traffic"] = desired
            self.assertEqual(self.verify()["percent"], 100)
        self.service["latestReadyRevision"] = self.revision + "-other"
        with self.assertRaises(Mismatch):
            self.verify()
        self.service["latestReadyRevision"] = self.revision
        for target in (latest, dict(latest, revision="old")):
            self.service["trafficStatuses"] = [target]
            with self.subTest(target=target), self.assertRaises(Mismatch):
                self.verify()

    def test_tag_only_old_revision_does_not_receive_default_traffic(self):
        tag = {"type": self.target["type"], "revision": "old", "tag": "retained"}
        self.service["traffic"].append(tag)
        self.service["trafficStatuses"].append(dict(tag, uri="https://retained---synthetic.a.run.app"))
        self.assertEqual(self.verify()["percent"], 100)

    def test_changing_failed_or_deleted_service_is_rejected(self):
        for delta in ({"reconciling": True}, {"generation": "16"}, {"generation": None, "observedGeneration": None},
                      {"terminalCondition": {"state": "CONDITION_FAILED"}}, {"deleteTime": "yesterday"},
                      {"name": self.service["name"] + "-other"}):
            with self.subTest(delta=delta):
                changed = dict(self.service, **delta)
                with self.assertRaises(Mismatch):
                    check(self.plan, self.rev, self.raw, service=changed)

    def test_origin_must_be_available_on_the_intended_service(self):
        for delta in ({"urls": []}, {"urls": self.origin}, {"defaultUriDisabled": True},
                      {"iapEnabled": True}, {"ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY"}):
            with self.subTest(delta=delta), self.assertRaises(Mismatch):
                check(self.plan, self.rev, self.raw, service=dict(self.service, **delta))
        self.service["uri"] = self.origin
        del self.service["urls"]
        self.assertEqual(self.verify()["percent"], 100)
        self.plan["serving_origin"] = self.origin.replace("native-sandbox", "candidate")
        self.service["uri"] = self.plan["serving_origin"]
        with self.assertRaises(Mismatch):
            self.verify()

    def test_legacy_plan_accepts_mount_check_but_cannot_certify_routing(self):
        del self.plan["serving_origin"]
        self.assertNotIn("serving", check(self.plan, self.rev, self.raw))
        with self.assertRaises(Mismatch):
            self.verify()

    def test_cli_serving_readback(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [Path(d)/name for name in ("plan","revision","settings","service")]
            for path, content in zip(paths, (json.dumps(self.plan).encode(), json.dumps(self.rev).encode(),
                                             self.raw, json.dumps(self.service).encode())):
                path.write_bytes(content)
            cmd = [sys.executable, "-B", str(Path(__file__).with_name("check_lite_native_revision.py")), *paths[:3],
                 "--service", paths[-1]]
            self.assertEqual(json.loads(subprocess.check_output(cmd))["serving"]["origin"], self.origin)
            paths[-1].write_text('{"error":{"code":404}}')
            denied = subprocess.run(cmd, capture_output=True)
            self.assertEqual(denied.returncode, 1)
            self.assertEqual(denied.stdout, b"")
            paths[0].write_text(json.dumps({k: v for k, v in self.plan.items() if k != "serving_origin"}))
            paths[-1].write_text("null")
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 1)


class OwnerServingTests(ServingFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.rev.update(generation="4", observedGeneration="4",
                        conditions=[{"type": "Ready", "state": "CONDITION_SUCCEEDED"}])
        self.owner = {"mode": "owner-review", "installation_sha256": "c" * 64, "starts": 100, "until": 3700}
        self.plan["owner"] = self.owner
        self.env = self.rev["containers"][0]["env"]
        self.env.extend([{"name": "LITE_INTERNAL_OWNER_MODE", "value": "owner-review"},
                         {"name": "LITE_INTERNAL_INSTALLATION_SHA256", "value": "c" * 64},
                         {"name": "LITE_INTERNAL_REVIEW_UNTIL", "value": "3700"}])

    def verify(self, *, clock=lambda: 101, pin=None):
        raw = json.dumps(self.plan).encode()
        return check_owner(raw, pin or hashlib.sha256(raw).hexdigest(), self.rev, self.raw, self.service, clock=clock)

    def activate(self):
        self.owner.clear()
        self.owner.update(mode="owner-only", installation_sha256="c" * 64, account_sha256="d" * 64)
        self.env[-3]["value"] = "owner-only"
        self.env[-1] = {"name": "LITE_INTERNAL_ACCOUNT_SHA256", "value": "d" * 64}

    def test_two_stages_report_observed_route_without_participant_pins(self):
        result = self.verify()
        self.assertEqual((result["owner_mode"], result["seconds_remaining"]), ("owner-review", 3599))
        self.activate()
        result = self.verify()
        self.assertEqual(result["scope"], "owner-personal-service-serving-metadata")
        self.assertEqual((result["owner_mode"], result["serving"]["percent"]), ("owner-only", 100))
        self.assertNotIn("seconds_remaining", result)
        for pin in (self.owner["installation_sha256"], self.owner["account_sha256"]):
            self.assertNotIn(pin, json.dumps(result))

    def test_window_edges(self):
        for now, remaining in ((100, 3600), (3699, 1)):
            self.assertEqual(self.verify(clock=lambda: now)["seconds_remaining"], remaining)
        for times in ((99, 99), (3700, 3700), (101, 100), (3699, 3700)):
            with self.subTest(times=times), self.assertRaises(Mismatch):
                self.verify(clock=iter(times).__next__)
        self.owner["until"] = 3701
        with self.assertRaises(Mismatch):
            self.verify()

    def test_public_check_and_unpinned_plan_do_not_accept_owner(self):
        with self.assertRaises(Mismatch):
            check(self.plan, self.rev, self.raw, service=self.service)
        with self.assertRaises(Mismatch):
            self.verify(pin="1" * 64)
        self.env.clear()
        with self.assertRaises(Mismatch):
            self.verify()

    def test_final_plan_cannot_match_first_stage_or_a_different_account(self):
        first = copy.deepcopy(self.env)
        self.activate()
        final = copy.deepcopy(self.env)
        self.env[:] = first
        with self.assertRaises(Mismatch):
            self.verify()
        self.env[:] = final
        self.env[-1]["value"] = "e" * 64
        with self.assertRaises(Mismatch):
            self.verify()

    def test_wrong_installation_deadline_and_extra_variables(self):
        for offset, value in ((-1, "3701"), (-2, "e" * 64)):
            before = self.env[offset]["value"]
            self.env[offset]["value"] = value
            with self.subTest(offset=offset), self.assertRaises(Mismatch):
                self.verify()
            self.env[offset]["value"] = before
        for key in ("LITE_OWNER_ENROLLMENT_PLAN", "LITE_INTERNAL_ACCOUNT_SHA256", "GCE_METADATA_HOST"):
            self.env.append({"name": key, "value": "unexpected"})
            with self.subTest(key=key), self.assertRaises(Mismatch):
                self.verify()
            self.env.pop()

    def test_revision_readiness_tags_and_service_required(self):
        for key in ("conditions", "observedGeneration"):
            original = self.rev.pop(key)
            with self.subTest(key=key), self.assertRaises(Mismatch):
                self.verify()
            self.rev[key] = original
        for key in ("traffic", "trafficStatuses"):
            self.service[key].append({"tag": "old", "percent": 0})
            with self.subTest(key=key), self.assertRaises(Mismatch):
                self.verify()
            self.service[key].pop()
        self.service = None
        with self.assertRaises(Mismatch):
            self.verify()

    def test_unexpected_runtime_mount(self):
        self.rev["volumes"].append({"name": "scratch", "emptyDir": {}})
        self.rev["containers"][0]["volumeMounts"].append({"name": "scratch", "mountPath": "/app"})
        with self.assertRaises(Mismatch):
            self.verify()

    def test_cli_requires_separate_owner_plan_pin(self):
        self.activate()
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder)/name for name in ("plan", "revision", "settings", "service")]
            for path, data in zip(paths, (json.dumps(self.plan).encode(), json.dumps(self.rev).encode(),
                                          self.raw, json.dumps(self.service).encode())):
                path.write_bytes(data)
            cmd = [sys.executable, "-B", str(Path(__file__).with_name("check_lite_native_revision.py")),
                   *paths[:3], "--service", paths[3]]
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            proof = json.loads(subprocess.check_output([*cmd, "--owner-plan-sha256",
                                                       hashlib.sha256(paths[0].read_bytes()).hexdigest()]))
            self.assertEqual(proof["owner_mode"], "owner-only")
