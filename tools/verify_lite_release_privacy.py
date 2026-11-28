"""Verify that the live Lite privacy notice matches a selected distribution IPA and its source."""
import argparse
import hashlib
import json
import plistlib
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile

NOTICE_URL = "https://jackh0001.github.io/WoundAI_Proj/woundlite.html"
LIMIT = 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_public_notice():
    request = Request(NOTICE_URL, headers={"Cache-Control": "no-cache", "Accept": "text/html"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        if (response.status != 200 or response.geturl() != NOTICE_URL
                or response.headers.get_content_type() != "text/html"):
            raise ValueError("The published policy URL must directly return HTML with HTTP 200")
        return response.read(LIMIT + 1)


def notice_hash(content):
    if not 0 < len(content) <= LIMIT:
        raise ValueError("Missing or oversized policy")
    return hashlib.sha256(content).hexdigest()


def compare(ipa, expected_sha256, build, source, published):
    with ipa.open("rb") as file:
        actual = hashlib.sha256()
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            actual.update(chunk)
        if actual.hexdigest() != expected_sha256:
            raise ValueError("IPA differs from the selected distribution receipt")
        with ZipFile(file) as archive:
            apps = [name for name in archive.namelist()
                    if name.startswith("Payload/") and name.endswith(".app/Info.plist")
                    and name.count("/") == 2]
            if len(apps) != 1:
                raise ValueError("Expected exactly one top-level app")
            info = plistlib.loads(archive.read(apps[0]))
            if info.get("CFBundleIdentifier") != "com.woundai.lite" or info.get("CFBundleVersion") != build:
                raise ValueError("Unexpected app identifier or build number")
            name = apps[0].removesuffix("Info.plist") + "woundlite.html"
            if archive.namelist().count(name) != 1 or archive.getinfo(name).file_size > LIMIT:
                raise ValueError("Expected one bundled Lite privacy notice within the size limit")
            bundled = notice_hash(archive.read(name))
    release = notice_hash(source)
    live = notice_hash(published)
    return {"build": build, "ipa_sha256": actual.hexdigest(), "public_url": NOTICE_URL,
            "source_sha256": release, "bundled_sha256": bundled, "public_sha256": live,
            "matches": release == bundled == live}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipa", type=Path, required=True)
    parser.add_argument("--ipa-sha256", required=True, help="SHA-256 from the distribution receipt")
    parser.add_argument("--build", required=True, help="CFBundleVersion of the release")
    parser.add_argument("--source", type=Path, required=True, help="woundlite.html from the release commit")
    args = parser.parse_args()
    try:
        result = compare(args.ipa, args.ipa_sha256, args.build, args.source.read_bytes(), read_public_notice())
    except (OSError, URLError, BadZipFile, ValueError, plistlib.InvalidFileException) as exc:
        print(f"Cannot verify Lite release privacy: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["matches"] else 1


if __name__ == "__main__":
    sys.exit(main())
