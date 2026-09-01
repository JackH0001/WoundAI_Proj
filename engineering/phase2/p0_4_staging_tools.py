"""List-first staging inventory, sweep and repair. No implicit cloud writes.

The default inventory has no code/actor fields. Image IDs remain linkable PHI;
keep output in restricted artifacts, not Git. Repair and sweep are separate
operations and each requires --apply plus an operator-supplied authorization.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Backend" / "Flask"))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("inventory", "sweep", "repair"))
    parser.add_argument("--root", type=Path, help="explicit local flywheel root")
    parser.add_argument("--store", choices=("local", "gcs"), default="local")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator", default="")
    parser.add_argument("--authorization-ref", default="")
    parser.add_argument("--grace-seconds", type=int, default=86400)
    args = parser.parse_args(argv)
    if args.apply and (args.operation == "inventory" or not args.operator.strip()
                       or not args.authorization_ref.strip()
                       or any(c in args.authorization_ref for c in "\r\n<>")):
        parser.error("writes require sweep/repair, --operator and a single-line --authorization-ref")
    if args.store == "local" and (args.root is None or not args.root.is_dir()):
        parser.error("local operation requires an existing --root")
    os.environ["WOUNDAI_STORE"] = args.store
    if args.root:
        os.environ["WOUNDAI_FLYWHEEL_DIR"] = str(args.root.resolve())
    import api_flywheel as fw
    from consent_staging import ConsentError, promotion_key
    svc = fw.promotion_service(args.operator or "inventory", "operator", None)
    result, failures = [], 0
    keys = sorted(set(svc.store.list_keys("staging") + svc.store.list_keys("images")))
    for key in keys:
        match = re.fullmatch(r"(staging|images)/([0-9a-f]{16})\.jpg", key)
        if not match:
            result.append({"key": key, "error": "unexpected_object_key"})
            failures += 1
            continue
        prefix, iid = match.groups()
        if args.operation != "inventory" and prefix != "staging":
            continue
        row = {"image_id": iid, "key": key}
        try:
            if args.operation == "inventory":
                p = svc._p(iid)
                bind = svc.store.get_json("staging_meta/bind/" + iid + ".json")
                row.update(promoted=p is not None, legacy=p is None and prefix == "images",
                           canonicalization_version=(p or bind or {}).get("canonicalization_version"))
            elif args.operation == "sweep":
                row["eligible"] = svc.sweep(iid, apply=args.apply)
            else:
                row["eligible"] = svc.repair(iid, args.operator or "list-only",
                                              args.grace_seconds, apply=args.apply)
            if args.apply and row.get("eligible"):
                svc.audit("staging_tool_authorization_reference", "-",
                           {"operation": args.operation, "image_id": iid,
                            "reference_supplied_by_operator": args.authorization_ref,
                            "verifiable_by_this_script": False})
        except (ConsentError, OSError, ValueError) as exc:
            row["error"] = str(exc)
            failures += 1
        result.append(row)
    print(json.dumps({"schema": "woundai.staging-tool/1", "operation": args.operation,
                      "apply": args.apply, "failures": failures, "objects": result},
                     ensure_ascii=False, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
