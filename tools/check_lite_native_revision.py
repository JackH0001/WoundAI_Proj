"""Compare a Lite Sandbox serving revision with its external or private owner release plan."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time


class Mismatch(ValueError):
    pass


def need(ok, message):
    if not ok:
        raise Mismatch(message)


def index(rows, field):
    result = {}
    for row in rows:
        key = row[field]
        need(isinstance(key, str) and key and key not in result, "duplicate or missing identifier")
        result[key] = row
    return result


def parse(raw):
    def unique(pairs):
        result = dict(pairs)
        need(len(result) == len(pairs), "duplicate JSON field")
        return result
    return json.loads(raw, object_pairs_hook=unique)


def absolute(value):
    p = PurePosixPath(value)
    need(p.is_absolute() and value == str(p) and not value.startswith("//")
         and p != PurePosixPath("/") and ".." not in p.parts, "invalid container path")
    return p


def version_ref(secret, version, deployment):
    if "/" in secret:
        match = re.fullmatch(r"projects/([^/]+)/secrets/([A-Za-z0-9_-]+)", secret)
        need(match and match[1] in (deployment["project"], deployment["project_number"]),
             "secret project differs")
        secret = match[2]
    need(re.fullmatch(r"[A-Za-z0-9_-]+", secret) and re.fullmatch(r"[1-9][0-9]*", version),
         "explicit numeric secret version required")
    return f"projects/{deployment['project']}/secrets/{secret}/versions/{version}"


def serving_route(service, settings, revision, origin):
    """Check the untagged URL used by the app, including the provider's observed traffic."""
    deployment = settings["deployment"]
    service_id = "woundai-lite-native-sandbox"
    location = "locations/asia-east1/services/" + service_id
    names = {f"projects/{project}/{location}" for project in (deployment["project"], deployment["project_number"])}
    need(service.get("name") in names and not service.get("deleteTime"), "serving service differs")
    generation = service.get("generation")
    need(isinstance(generation, str) and re.fullmatch(r"[1-9][0-9]*", generation)
         and service.get("observedGeneration") == generation and service.get("reconciling", False) is False
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED",
         "service reconciliation incomplete")
    need(service.get("ingress") == "INGRESS_TRAFFIC_ALL" and service.get("defaultUriDisabled", False) is False
         and service.get("iapEnabled", False) is False, "external app origin unavailable")
    urls = service.get("urls", [])
    expected_origin = f"https://{service_id}-{deployment['project_number']}.asia-east1.run.app"
    need(origin == expected_origin and isinstance(urls, list) and (origin == service.get("uri") or origin in urls),
         "app origin differs from serving URLs")

    short = revision.rsplit("/", 1)[1]
    revision_names = {short} | {name + "/revisions/" + short for name in names}
    latest = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    explicit = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
    for field in ("traffic", "trafficStatuses"):
        rows = service.get(field, [])
        need(isinstance(rows, list), "invalid traffic readback")
        if not rows and field == "traffic":
            rows = [{"type": latest, "percent": 100}]
        total = 0
        for row in rows:
            percent = row.get("percent", 0)
            need(type(percent) is int and 0 <= percent <= 100, "invalid traffic percentage")
            if percent == 0:
                continue
            allocation = row.get("type")
            need(allocation in (explicit, latest), "unknown traffic allocation")
            if allocation == latest:
                need(service.get("latestReadyRevision") in revision_names, "latest ready revision differs")
            if field == "trafficStatuses" or allocation == explicit or row.get("revision"):
                need(row.get("revision") in revision_names, "traffic reaches a different revision")
            total += percent
        need(total == 100, "reviewed revision must receive all app traffic")
    return {"origin": origin, "generation": generation, "percent": 100}


def check_owner(plan_raw, expected_sha256, revision, settings_raw, service, *, clock=time.time, historical=False):
    need(hashlib.sha256(plan_raw).hexdigest() == expected_sha256, "reviewed owner plan bytes differ")
    plan = parse(plan_raw)
    owner = plan["owner"]
    need(isinstance(owner, dict) and isinstance(service, dict) and "serving_origin" in plan,
         "owner serving plan and service readback required")
    mode = owner["mode"]
    installation = owner["installation_sha256"]
    need(isinstance(installation, str) and re.fullmatch(r"[0-9a-f]{64}", installation), "invalid installation pin")
    internal = {"LITE_INTERNAL_OWNER_MODE": mode, "LITE_INTERNAL_INSTALLATION_SHA256": installation}
    if mode == "owner-review":
        need(set(owner) == {"mode", "installation_sha256", "starts", "until"}, "unexpected owner-review fields")
        start, until = owner["starts"], owner["until"]
        observed_at = clock()
        need(type(start) is int and type(until) is int and 0 <= start <= observed_at < until <= start + 3600,
             "outside approved owner-review window")
        internal["LITE_INTERNAL_REVIEW_UNTIL"] = str(until)
    else:
        need(mode == "owner-only" and set(owner) == {"mode", "installation_sha256", "account_sha256"},
             "expected owner-only installation and account")
        account = owner["account_sha256"]
        need(isinstance(account, str) and re.fullmatch(r"[0-9a-f]{64}", account), "invalid account pin")
        internal["LITE_INTERNAL_ACCOUNT_SHA256"] = account
    generation = revision.get("generation")
    ready = [row for row in revision.get("conditions", []) if row.get("type") == "Ready"]
    need(isinstance(generation, str) and re.fullmatch(r"[1-9][0-9]*", generation)
         and generation == revision.get("observedGeneration") and revision.get("reconciling", False) is False
         and len(ready) == 1 and ready[0].get("state") == "CONDITION_SUCCEEDED", "owner revision is not ready")
    need(not any(row.get("tag") for field in ("traffic", "trafficStatuses") for row in service.get(field, [])),
         "unexpected tagged route on owner service")
    result = _check(plan, revision, settings_raw, service=service, internal=internal, historical=historical)
    if mode == "owner-review":
        finished_at = clock()
        need(observed_at <= finished_at < until, "owner-review window changed during check")
        result["seconds_remaining"] = until - int(finished_at)
    result["owner_mode"] = mode
    return result


def check(plan, revision, settings_raw, *, service=None, historical=False):
    need("owner" not in plan, "private owner plan requires separate verification")
    return _check(plan, revision, settings_raw, service=service, internal=None, historical=historical)


def worker_probe(contract, container, historical):
    if contract is None and historical:
        return "unverified-historical"
    need(contract == "http-worker/1", "reviewed worker_startup contract required for a candidate revision")
    ports = container.get("ports") or [{"containerPort": 8080}]
    need(isinstance(ports, list) and len(ports) == 1 and isinstance(ports[0], dict)
         and ports[0].get("containerPort") == 8080 and ports[0].get("name", "") in ("", "http1"),
         "personal worker requires HTTP/1 on port 8080")
    probe = container.get("startupProbe")
    http = probe.get("httpGet") if isinstance(probe, dict) else None
    need(isinstance(http, dict) and "tcpSocket" not in probe and "grpc" not in probe
         and http.get("path") == "/startupz" and http.get("port", 8080) == 8080 and http.get("httpHeaders", []) == [],
         "personal worker requires an HTTP startup probe on /startupz")
    return "http-worker/1-configured"


def _check(plan, revision, settings_raw, *, service, internal, historical):
    settings = parse(settings_raw)
    deployment = settings["deployment"]
    need(hashlib.sha256(settings_raw).hexdigest() == plan["settings_sha256"], "settings bytes differ")
    need(settings["schema"] in ("lite-native-startup/2", "lite-native-startup/3")
         and settings["enabled"] is True, "separate mounted inputs required")
    app = settings["app"]
    need(app["bundle"] == "com.woundai.lite" and app["app_id"] == 6825208065
         and app["attest_audience"] == "woundlite-native-sandbox-v1"
         and app["team"] == plan["team"] and re.fullmatch(r"[A-Z0-9]{10}", plan["team"])
         and app["version"] == plan["build"] and re.fullmatch(r"[1-9][0-9]*", plan["build"]),
         "external Lite application differs")
    need(re.fullmatch(r"[0-9a-f]{40}", deployment["source"]), "fixed application source required")
    need(settings["apple"]["environment"] == "Sandbox"
         and settings["apple"]["product"] == "com.woundai.lite.plus.monthly", "Apple Sandbox product differs")
    name = revision["name"]
    resource = re.fullmatch(r"projects/([^/]+)/locations/asia-east1/services/woundai-lite-native-sandbox/revisions/([^/]+)", name)
    need(resource and resource[1] in (deployment["project"], deployment["project_number"])
         and name == plan["revision"] and not revision.get("deleteTime"), "revision differs")
    need(revision["serviceAccount"] == deployment["service_account"], "service identity differs")
    need(len(revision["containers"]) == 1, "single personal-service container required")
    container = revision["containers"][0]
    need(container["image"] == plan["image"] and re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", plan["image"])
         and plan["image"].endswith("@sha256:" + deployment["image"]), "immutable image differs")
    need(not container.get("command") and not container.get("args"), "candidate startup command differs")
    startup = worker_probe(plan.get("worker_startup"), container, historical)
    env = index(container["env"], "name")
    need(not any("valueSource" in entry for entry in env.values()), "unexpected environment secret")
    if internal is None:
        need(not any(k.startswith("LITE_INTERNAL_") or k.startswith("LITE_OWNER_ENROLLMENT_") for k in env),
             "owner-only settings present on external service")
    else:
        native_fields = {"LITE_NATIVE_CONFIG", "LITE_NATIVE_SECRET_DIR", "LITE_NATIVE_CONFIG_SHA256",
                         "LITE_NATIVE_SOURCE", "LITE_NATIVE_IMAGE_SHA256", "LITE_RESEARCH_CAPTURE_ENABLED",
                         "LITE_RESEARCH_LINK_FILE", "LITE_RESEARCH_LINK_SHA256"}
        need(env.keys() <= native_fields | internal.keys() and
             all(env.get(key, {}).get("value") == value for key, value in internal.items()),
             "owner mode, installation, account, deadline or environment differs")

    def literal(k):
        need(isinstance(env[k].get("value"), str), "literal settings pin required")
        return env[k]["value"]

    for key, expected in (("LITE_NATIVE_CONFIG_SHA256", plan["settings_sha256"]),
                          ("LITE_NATIVE_IMAGE_SHA256", deployment["image"]), ("LITE_NATIVE_SOURCE", deployment["source"])):
        need(literal(key) == expected, "startup pins differ")
    roots = settings["apple"]["roots"]
    need(1 <= len(roots) <= 4 and set(settings["secrets"]) == {"api", "oauth", "identity"}, "native inputs differ")
    refs = dict(settings["secrets"], **{f"root-{n}": ref for n, ref in enumerate(roots, 1)})
    base = absolute(literal("LITE_NATIVE_SECRET_DIR"))
    paths = {"config": absolute(literal("LITE_NATIVE_CONFIG"))}
    for role, ref in refs.items():
        need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", ref["file"]), "invalid mounted file name")
        paths[role] = base / role / ref["file"]
    expected = dict(plan["secret_versions"])
    need(set(paths) == set(expected), "approved role set differs")
    key_stores = {expected[k].rsplit("/versions/", 1)[0] for k in ("identity", "oauth", "api")}
    need(len(key_stores) == 3, "identity and Apple credentials require separate secrets")
    additional = plan.get("additional_secret_files", {})
    need(isinstance(additional, dict), "additional secret files must map paths to versions")
    research = plan.get("research_link")
    capture = env.get("LITE_RESEARCH_CAPTURE_ENABLED", {"value": "0"}).get("value")
    if "research_link" not in plan:
        need(capture == "0" and "LITE_RESEARCH_LINK_FILE" not in env and "LITE_RESEARCH_LINK_SHA256" not in env,
             "research link settings require a reviewed plan")
    else:
        need(isinstance(research, dict) and set(research) == {"file", "sha256", "capture_enabled"},
             "research link plan fields differ")
        need(research["file"] in additional and env.get("LITE_RESEARCH_LINK_FILE", {}).get("value") == research["file"],
             "research link path differs from reviewed mount")
        need(isinstance(research["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", research["sha256"])
             and env.get("LITE_RESEARCH_LINK_SHA256", {}).get("value") == research["sha256"],
             "research link issuer digest differs")
        need(type(research["capture_enabled"]) is bool and capture == ("1" if research["capture_enabled"] else "0"),
             "research capture setting differs")
    for n, (filename, secret_version) in enumerate(sorted(additional.items()), 1):
        key = f"additional-{n}"
        paths[key] = absolute(filename)
        expected[key] = secret_version
    need(len({p.parent for p in paths.values()}) == len(paths), "duplicate secret mount directory")
    volumes = index(revision["volumes"], "name")
    mounts = index(container["volumeMounts"], "mountPath")
    directories = {absolute(path): row for path, row in mounts.items()}
    secret_volumes = {name for name, volume in volumes.items() if "secret" in volume}
    used_volumes = {row["name"] for row in mounts.values()}
    if internal is not None:
        need(volumes.keys() == secret_volumes == used_volumes, "unexpected runtime volume on owner revision")
    need(secret_volumes <= used_volumes, "unreviewed unused secret volume")
    secret_dirs = {p for p, row in directories.items() if "secret" in volumes[row["name"]]}
    need(secret_dirs == {p.parent for p in paths.values()}, "secret mount set differs")
    for role, path in paths.items():
        parent = path.parent
        need(parent in directories, f"{role} mount missing")
        need(not any(other != parent and (other in parent.parents or parent in other.parents)
                     for other in directories), f"{role} mount overlaps another mount")
        mount = directories[parent]
        need(not mount.get("subPath"), f"{role} mount uses a subpath")
        secret = volumes[mount["name"]]["secret"]
        items = secret["items"]
        need(len(items) == 1 and items[0]["path"] == path.name, f"{role} filename differs")
        actual = version_ref(secret["secret"], items[0]["version"], deployment)
        need(actual == expected[role], f"{role} secret version differs")
    scope = "owner-personal-service-serving-metadata" if internal is not None else "external-personal-service-mounted-inputs"
    result = {"scope": scope, "build": app["version"], "worker_startup": startup,
              "revision": name, "settings_sha256": plan["settings_sha256"], "image": plan["image"],
              "matched_roles": sorted(paths)}
    if research is not None:
        result["research_link"] = {"capture_enabled": research["capture_enabled"],
                                   "secret_version": additional[research["file"]], "sha256": research["sha256"]}
    if service is not None or "serving_origin" in plan:
        need(isinstance(service, dict) and "serving_origin" in plan, "serving origin and service readback required")
        result["serving"] = serving_route(service, settings, name, plan["serving_origin"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "revision", "settings"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--service", type=Path, help="Cloud Run v2 Service JSON for the app's untagged origin")
    parser.add_argument("--owner-plan-sha256", help="SHA-256 of the separately reviewed private owner plan file")
    parser.add_argument("--historical", action="store_true", help="audit archived plans that predate worker probes")
    args = parser.parse_args()
    try:
        service = None
        if args.service is not None:
            service = parse(args.service.read_bytes())
            need(isinstance(service, dict), "service readback required")
        plan_raw = args.plan.read_bytes()
        revision = parse(args.revision.read_bytes())
        settings_raw = args.settings.read_bytes()
        if args.owner_plan_sha256 is None:
            result = check(parse(plan_raw), revision, settings_raw, service=service, historical=args.historical)
        else:
            result = check_owner(plan_raw, args.owner_plan_sha256, revision, settings_raw, service, historical=args.historical)
    except Mismatch as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
        print("Invalid or incomplete input metadata.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
