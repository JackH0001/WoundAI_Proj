"""Read-only check of a Cloud Run v2 owner enrollment revision, service and private scope."""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from check_lite_native_revision import Mismatch, absolute, index, need, parse, serving_route, version_ref


def checked_mounts(container, revision, settings, deployment):
    volumes = index(revision["volumes"], "name")
    mounts = index(container["volumeMounts"], "name")
    need(len(volumes) == 2 and volumes.keys() == mounts.keys(), "expected two configuration volumes")
    observed = {}
    for name, volume in volumes.items():
        mount = mounts[name]
        parent = absolute(mount["mountPath"])
        need(not mount.get("subPath") and "secret" in volume, "expected a secret directory")
        secret = volume["secret"]
        items = secret["items"]
        need(len(items) == 1 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", items[0]["path"]),
             "expected one mounted configuration file")
        observed[str(parent / items[0]["path"])] = version_ref(secret["secret"], items[0]["version"], deployment)
    need(observed == settings, "mounted configuration path or secret version differs")


def check(release, scope_raw, revision, service, clock=time.time, *, probe=None):
    need(release["schema"] == "lite-owner-enrollment-revision/1" and
         hashlib.sha256(scope_raw).hexdigest() == release["scope_sha256"], "reviewed enrollment scope differs")
    scope = parse(scope_raw)
    need(scope["schema"] == "lite-owner-enrollment-scope/1" and scope["region"] == "asia-east1",
         "expected a prepared owner enrollment scope")
    start, end = scope["starts"], scope["until"]
    need(type(start) is int and type(end) is int and 0 <= start < end <= start + 900 and
         start // 86400 == (end - 1) // 86400, "invalid enrollment interval")
    project, number = scope["project"], scope["project_number"]
    service_id = "woundai-lite-native-sandbox"
    name = revision["name"]
    match = re.fullmatch(r"projects/([^/]+)/locations/asia-east1/services/" + service_id +
                         r"/revisions/([a-z0-9-]+)", name)
    need(match and match[1] in (project, number) and release["revision"] == name and
         not revision.get("deleteTime"), "revision differs")
    need(revision["serviceAccount"] == scope["service_account"], "enrollment identity differs")
    generation = revision.get("generation")
    ready = [c for c in revision["conditions"] if c["type"] == "Ready"]
    need(isinstance(generation, str) and re.fullmatch(r"[1-9][0-9]*", generation) and
         revision.get("observedGeneration") == generation and revision.get("reconciling", False) is False and
         len(ready) == 1 and ready[0].get("state") == "CONDITION_SUCCEEDED", "revision is not ready")
    need(len(revision["containers"]) == 1, "expected one enrollment container")
    container = revision["containers"][0]
    image = f"asia-east1-docker.pkg.dev/{project}/woundai-lite-candidate/native-sandbox@sha256:{scope['image']}"
    need(container["image"] == image and re.fullmatch(r"[0-9a-f]{64}", scope["image"]) and
         not container.get("command") and not container.get("args"), "image or entry point differs")

    env = index(container["env"], "name")
    files = release["files"]
    need(set(files) == {"native", "enrollment"}, "expected native and enrollment settings")
    paths = [absolute(files[key]["path"]) for key in ("native", "enrollment")]
    a, b = (path.parent for path in paths)
    need(a != b and a not in b.parents and b not in a.parents, "configuration directories overlap")
    expected = {
        "LITE_INTERNAL_OWNER_MODE": "owner-enrollment",
        "LITE_NATIVE_CONFIG": str(paths[0]),
        "LITE_NATIVE_CONFIG_SHA256": scope["native_sha256"],
        "LITE_NATIVE_SOURCE": scope["source"],
        "LITE_NATIVE_IMAGE_SHA256": scope["image"],
        "LITE_OWNER_ENROLLMENT_PLAN": str(paths[1]),
        "LITE_OWNER_ENROLLMENT_PLAN_SHA256": scope["plan_sha256"],
    }
    if "LITE_RESEARCH_CAPTURE_ENABLED" in env:
        expected["LITE_RESEARCH_CAPTURE_ENABLED"] = "0"
    need(set(env) == set(expected) and all(row.get("value") == expected[key] and "valueSource" not in row
                                            for key, row in env.items()), "enrollment environment differs")
    versions = {v["path"]: v["version"] for v in files.values()}
    need(len({v.rsplit("/versions/", 1)[0] for v in versions.values()}) == 2,
         "enrollment configuration files require separate secrets")
    checked_mounts(container, revision, versions, scope)
    need(not any(row.get("tag") for field in ("traffic", "trafficStatuses") for row in service.get(field, [])),
         "tagged traffic requires separate review")
    origin = f"https://{service_id}-{number}.asia-east1.run.app"
    route = serving_route(service, {"deployment": scope}, name, origin)
    now = int(clock())
    need(start <= now < end, "outside enrollment interval")
    result = dict(scope="owner-enrollment-serving-metadata", build=scope["build"], revision=name,
                  checked_at=now, until=end, seconds_remaining=end-now, image=image, serving=route)
    if probe is not None:
        # This response is specific to the owner's enrollment app and is returned before storage or budget access.
        need(probe["method"] == "POST" and probe["url"] == origin + "/api/v1/lite/attest/challenge" and
             probe["scope_sha256"] == release["scope_sha256"] and probe["service_generation"] == route["generation"]
             and type(probe["observed_at"]) is int and start <= probe["observed_at"] <= now,
             "HTTP observation is for a different origin, generation or interval")
        need(probe["status"] == 403 and probe["body"] == {"error": "lite_owner_enrollment_closed"} and
             probe["content_type"].split(";", 1)[0].strip().lower() == "application/json" and
             "no-store" in {v.strip().lower() for v in probe["cache_control"].split(",")},
             "owner enrollment HTTP response differs")
        result["observed_enrollment_http"] = {"status": 403, "observed_at": probe["observed_at"]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release", "scope", "revision", "service"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--release-sha256", required=True)
    parser.add_argument("--probe", type=Path, help="saved unauthenticated response for a synthetic non-owner key")
    args = parser.parse_args()
    try:
        raw = args.release.read_bytes()
        need(hashlib.sha256(raw).hexdigest() == args.release_sha256, "release digest differs")
        probe = None
        if args.probe is not None:
            probe = parse(args.probe.read_bytes())
            need(isinstance(probe, dict), "expected HTTP observation")
        result = check(parse(raw), args.scope.read_bytes(), parse(args.revision.read_bytes()),
                       parse(args.service.read_bytes()), probe=probe)
    except Mismatch as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValueError, OSError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
        print("Incomplete enrollment readback.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
