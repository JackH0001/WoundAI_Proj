"""Offline comparison of the external Lite service's mounted inputs with an operator's release plan."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


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


def check(plan, revision, settings_raw):
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
    env = index(container["env"], "name")
    need(not any("valueSource" in entry for entry in env.values()), "unexpected environment secret")
    need(not any(k.startswith("LITE_INTERNAL_") or k.startswith("LITE_OWNER_ENROLLMENT_") for k in env),
         "owner-only settings present on external service")

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
    # Additional reviewed native inputs (e.g. the optional research-link issuer)
    # must have their own independently approved file path and numeric version.
    for n, (filename, secret_version) in enumerate(sorted(plan.get("additional_secret_files", {}).items()), 1):
        key = f"additional-{n}"
        paths[key] = absolute(filename)
        expected[key] = secret_version
    need(len({p.parent for p in paths.values()}) == len(paths), "duplicate secret mount directory")
    volumes = index(revision["volumes"], "name")
    mounts = index(container["volumeMounts"], "mountPath")
    directories = {absolute(path): row for path, row in mounts.items()}
    secret_volumes = {name for name, volume in volumes.items() if "secret" in volume}
    used_volumes = {row["name"] for row in mounts.values()}
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
    return {"scope": "external-personal-service-mounted-inputs", "build": app["version"],
            "revision": name, "settings_sha256": plan["settings_sha256"], "image": plan["image"],
            "matched_roles": sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "revision", "settings"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        result = check(parse(args.plan.read_bytes()), parse(args.revision.read_bytes()), args.settings.read_bytes())
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
