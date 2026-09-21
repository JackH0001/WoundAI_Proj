#!/usr/bin/env python3
"""Read-only inventory of the MAIN flywheel bucket, for review before deletion.

On 2026-09-06 a test run inherited cloud routing and wrote into production
storage. 125 records went into the audit epoch bucket, which was already locked
and can never be cleaned. 96 objects also went into the main bucket -- and that
one is NOT locked, so those can be removed.

"Can be removed" is not "should be removed blind". An object that some other
chain still references is not litter, it is a dependency, and deleting it turns
a recoverable mess into a broken dataset. So this tool only looks, and produces
a manifest for a human to read.

Read-only by construction:
  * it never calls a mutating Cloud Storage method -- test_inventory_is_read_only
    pins that by scanning this file;
  * it refuses any bucket whose name looks like an audit epoch, because those are
    immutable by policy and there is nothing to inventory for deletion;
  * its only output is a local JSON file.

The manifest carries a sha256 of the exact candidate list. Any future deletion
step must be bound to that digest, so a candidate that appeared after the review
cannot be swept along with the ones that were actually reviewed -- the same
binding discipline Assert-CIGreen.ps1 uses for a commit.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

# Keys under these prefixes belong to the audit bucket (store.py PROTECTED_KEYS).
# They must never appear in a deletion candidate list even if one is found here.
PROTECTED_PREFIXES = ("audit.jsonl", "receipts")

# Sidecars that reference objects. Anything named in one of these is a
# dependency, not litter.
REFERENCE_FILES = (
    "retrain_queue.jsonl",
    "withdrawn.jsonl",
    "retracted.jsonl",
    "depth_index.jsonl",
)

AUDIT_BUCKET_SHAPE = re.compile(r"-audit(-|$)")
# An object key mentioned inside a sidecar record, however the schema nests it.
KEYISH = re.compile(r"[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|json|jsonl)")
IMAGE_ID = re.compile(r"^[0-9a-f]{8,64}$")


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bucket", required=True, help="MAIN bucket name, never an audit bucket")
    p.add_argument("--prefix", default="flywheel", help="WOUNDAI_GCS_PREFIX (default: flywheel)")
    p.add_argument("--out", required=True, help="where to write the manifest JSON")
    p.add_argument("--since", required=True,
                   help="ISO-8601 UTC start of the window under review, e.g. 2026-09-06T00:00:00Z")
    p.add_argument("--until", required=True, help="ISO-8601 UTC end of the window")
    return p.parse_args(argv)


def utc(value: str, what: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise SystemExit("%s is not an ISO-8601 timestamp: [%s]" % (what, value))
    if parsed.tzinfo is None:
        raise SystemExit("%s must carry a timezone (use ...Z): [%s]" % (what, value))
    return parsed.astimezone(timezone.utc)


def refuse_audit_bucket(name: str) -> None:
    if AUDIT_BUCKET_SHAPE.search(name or ""):
        raise SystemExit(
            "[%s] looks like an audit bucket. Audit epochs are immutable by policy; "
            "there is nothing here to inventory for deletion, and this tool will not "
            "enumerate one as if there were." % name)


def strip_prefix(name: str, prefix: str) -> str:
    head = (prefix or "").strip("/")
    if head and name.startswith(head + "/"):
        return name[len(head) + 1:]
    return name


def is_protected(relative_key: str) -> bool:
    k = (relative_key or "").strip("/")
    return any(k == p or k.startswith(p + "/") for p in PROTECTED_PREFIXES)


def referenced_tokens(blob_text: str):
    """Every token in a sidecar that could name an object.

    Deliberately greedy. A token this misses becomes a deletion candidate that
    something still needs; a token it over-collects only keeps an object alive.
    The asymmetry is the whole point, so when the schema is unclear this errs
    towards calling things referenced.
    """
    tokens = set(KEYISH.findall(blob_text))
    for line in blob_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            # An unparseable line is not a reason to forget what it mentions.
            continue
        stack = [record]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, str):
                if IMAGE_ID.match(node) or KEYISH.match(node):
                    tokens.add(node)
    return tokens


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    refuse_audit_bucket(args.bucket)
    since = utc(args.since, "--since")
    until = utc(args.until, "--until")
    if since >= until:
        raise SystemExit("--since must be earlier than --until")

    from google.cloud import storage  # imported late so --help needs no credentials

    client = storage.Client()
    bucket = client.bucket(args.bucket)

    head = (args.prefix or "").strip("/")
    listing = list(client.list_blobs(args.bucket, prefix=(head + "/") if head else None))

    # Collect what the sidecars point at, before classifying anything.
    references = {}
    for blob in listing:
        rel = strip_prefix(blob.name, args.prefix)
        if rel not in REFERENCE_FILES:
            continue
        try:
            body = blob.download_as_bytes().decode("utf-8", "replace")
        except Exception as exc:  # a sidecar we cannot read makes every token unknown
            raise SystemExit(
                "cannot read reference file %s (%s). Refusing to produce a candidate "
                "list that would treat its contents as absent." % (blob.name, type(exc).__name__))
        for token in referenced_tokens(body):
            references.setdefault(token, set()).add(rel)

    objects, candidates, in_window_referenced, protected_found = [], [], [], []
    for blob in listing:
        rel = strip_prefix(blob.name, args.prefix)
        created = blob.time_created.astimezone(timezone.utc) if blob.time_created else None
        stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        cited = sorted(references.get(rel, set()) | references.get(stem, set()))
        row = {
            "key": blob.name,
            "relative_key": rel,
            "bytes": blob.size,
            "created": created.isoformat() if created else None,
            "generation": blob.generation,
            "referenced_by": cited,
        }
        objects.append(row)

        if is_protected(rel):
            protected_found.append(row)
            continue
        if rel in REFERENCE_FILES:
            continue
        if created is None or not (since <= created < until):
            continue
        (in_window_referenced if cited else candidates).append(row)

    candidates.sort(key=lambda r: r["key"])
    digest_source = "\n".join("%s@%s" % (r["key"], r["generation"]) for r in candidates)
    manifest = {
        "schema": "woundai.main_bucket_inventory.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "bucket": args.bucket,
        "prefix": args.prefix,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "totals": {
            "objects_listed": len(objects),
            "in_window_unreferenced": len(candidates),
            "in_window_referenced": len(in_window_referenced),
            "protected_keys_seen": len(protected_found),
        },
        "candidates_sha256": hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
        "candidates": candidates,
        "in_window_but_referenced": in_window_referenced,
        "protected_keys_seen": protected_found,
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")

    print("bucket              %s (prefix %s)" % (args.bucket, args.prefix))
    print("window              %s .. %s" % (since.isoformat(), until.isoformat()))
    print("objects listed      %d" % len(objects))
    print("in window, unreferenced (DELETION CANDIDATES)  %d" % len(candidates))
    print("in window, referenced by a sidecar (KEEP)      %d" % len(in_window_referenced))
    print("protected keys seen in this bucket             %d" % len(protected_found))
    print("candidates sha256   %s" % manifest["candidates_sha256"])
    print("manifest            %s" % args.out)
    if in_window_referenced:
        print("")
        print("NOTE: the referenced ones are a decision, not litter. Read them in the")
        print("      manifest before anyone writes a deletion step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
