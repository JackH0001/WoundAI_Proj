#!/usr/bin/env python3
"""The default backend address must agree across iOS, Android and the runbook.

On 2026-08-08 the iOS release build pointed at a deployment that no longer
existed (wound-ai-867037876992) while Android and docs/admin_operations.md
pointed at woundai-backend-421209514056. Nothing failed loudly: the only symptom
was "backend not connected" on every screen, which a tester reports as "the app
is broken", not as "the address is wrong". Today the three copies are held
together by a comment asking the next editor to remember all three. A comment is
not a gate.

These tests make the three copies one fact, and pin the shape of the iOS
compile-time switch, because the failure that shape prevents -- a release build
resolving to localhost -- is invisible until someone installs the build on a
real phone.

Every extractor below raises when it finds nothing. A parser that silently
matches zero lines and lets the suite pass would be the same fail-open class
this file exists to close.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "iOS" / "WoundMeasurementApp" / "Core" / "AppSettings.swift"
GRADLE = ROOT / "Android" / "app" / "build.gradle"
RUNBOOK = ROOT / "docs" / "admin_operations.md"
WORKFLOW = ROOT / ".github" / "workflows" / "p0-4-audit.yml"

SELF = "engineering/phase2/test_backend_url_parity.py"
# The files whose drift this test exists to catch. A workflow that does not
# trigger on all of them does not run this guard on the very change it guards.
GUARDED = (
    SELF,
    "iOS/WoundMeasurementApp/Core/AppSettings.swift",
    "Android/app/build.gradle",
    "docs/admin_operations.md",
)

RUN_APP = re.compile(r"https://[A-Za-z0-9.\-]+\.run\.app")
LOOPBACK = ("localhost", "127.0.0.1", "10.0.2.2", "0.0.0.0", "::1")


def text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError("missing file: %s" % path)
    return path.read_text(encoding="utf-8-sig")


def without_line_comments(source: str, marker: str) -> str:
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith(marker))


def is_loopback(url: str) -> bool:
    return any(host in url for host in LOOPBACK)


# ---------------------------------------------------------------- iOS

def ios_default_url_body() -> str:
    src = text(IOS)
    anchor = src.find("static var defaultURL: String {")
    if anchor < 0:
        raise AssertionError(
            "AppSettings.swift no longer declares `static var defaultURL: String {`; "
            "this guard can no longer see the release address")
    open_at = src.index("{", anchor)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at + 1:i]
    raise AssertionError("unbalanced braces in defaultURL")


def ios_branches():
    """Return (condition, debug_branch, release_branch, text_outside_the_switch)."""
    body = without_line_comments(ios_default_url_body(), "//")
    ifs = re.findall(r"^[ \t]*#if\b", body, re.M)
    if len(ifs) != 1:
        raise AssertionError(
            "defaultURL must contain exactly one #if; found %d. Without one, a release "
            "build returns whatever comes first in the function." % len(ifs))
    cond = re.search(r"^[ \t]*#if[ \t]+(?P<cond>.+)$", body, re.M)
    els = re.search(r"^[ \t]*#else[ \t]*$", body, re.M)
    end = re.search(r"^[ \t]*#endif[ \t]*$", body, re.M)
    if not (cond and els and end):
        raise AssertionError("defaultURL must have #if / #else / #endif")
    return (cond.group("cond").strip(),
            body[cond.end():els.start()],
            body[els.end():end.start()],
            body[:cond.start()] + body[end.end():])


def sole_returned_string(part: str, what: str) -> str:
    found = re.findall(r'return\s+"([^"]*)"', part)
    if len(found) != 1:
        raise AssertionError(
            "expected exactly one returned string literal in %s, found %d" % (what, len(found)))
    return found[0]


# ------------------------------------------------------------ Android

def android_backend_urls():
    lines = text(GRADLE).splitlines()
    found = {}
    for idx, line in enumerate(lines):
        if "DEFAULT_BACKEND_URL" not in line:
            continue
        window = [line]
        for nxt in lines[idx + 1:idx + 3]:
            if "buildConfigField" in nxt:
                break
            window.append(nxt)
        values = re.findall(r"'\"([^\"]*)\"'", "\n".join(window))
        if len(values) != 1:
            raise AssertionError(
                "cannot read a single DEFAULT_BACKEND_URL value at build.gradle line %d "
                "(found %d)" % (idx + 1, len(values)))
        block = None
        for back in range(idx, -1, -1):
            hit = re.match(r"[ \t]*(release|debug)[ \t]*\{[ \t]*$", lines[back])
            if hit:
                block = hit.group(1)
                break
        if block is None:
            raise AssertionError(
                "DEFAULT_BACKEND_URL at build.gradle line %d is not inside a release or "
                "debug block; which build it applies to cannot be determined" % (idx + 1))
        if block in found:
            raise AssertionError("two DEFAULT_BACKEND_URL entries in the %s block" % block)
        found[block] = values[0]
    if set(found) != {"release", "debug"}:
        raise AssertionError(
            "expected one DEFAULT_BACKEND_URL in each of release and debug; got %s"
            % sorted(found))
    return found


# ------------------------------------------------------------ runbook

def runbook_origins():
    origins = sorted(set(RUN_APP.findall(text(RUNBOOK))))
    if not origins:
        raise AssertionError(
            "docs/admin_operations.md no longer names a *.run.app backend; the runbook "
            "is one of the three places that must agree")
    return origins


# ----------------------------------------------------------- workflow

def workflow_trigger_paths(event: str):
    src = text(WORKFLOW)
    head = re.search(r"^  %s:[ \t]*$" % re.escape(event), src, re.M)
    if not head:
        raise AssertionError("p0-4-audit.yml has no `%s:` trigger" % event)
    tail = src[head.end():]
    stop = re.search(r"^  \S", tail, re.M)
    section = tail[:stop.start()] if stop else tail
    paths = re.search(r"^    paths:[ \t]*$", section, re.M)
    if not paths:
        raise AssertionError(
            "the `%s:` trigger has no paths: filter; this guard would run on every "
            "change or none, and either way the assertion below is meaningless" % event)
    entries = []
    for line in section[paths.end():].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            break
        entries.append(stripped[2:].strip().strip("'\""))
    if not entries:
        raise AssertionError("the `%s:` paths filter is empty" % event)
    return entries


class BackendUrlParityTests(unittest.TestCase):

    # -- iOS shape -----------------------------------------------------

    def test_ios_switch_is_compile_time_and_keyed_on_debug(self):
        cond, _, _, outside = ios_branches()
        self.assertIn(
            "DEBUG", cond,
            "the defaultURL switch must be keyed on DEBUG; [%s] would send release "
            "builds down the development path" % cond)
        self.assertNotIn(
            "return", outside,
            "defaultURL returns outside its #if/#endif. An early return makes the "
            "release branch unreachable -- the exact shape this file replaced.")

    def test_ios_debug_branch_is_a_loopback_and_release_branch_is_not(self):
        _, debug_part, release_part, _ = ios_branches()
        debug_url = sole_returned_string(debug_part, "the iOS #if (debug) branch")
        release_url = sole_returned_string(release_part, "the iOS #else (release) branch")
        self.assertTrue(is_loopback(debug_url), "iOS debug default should be a loopback")
        self.assertFalse(
            is_loopback(release_url),
            "iOS release resolves to [%s]. A release build pointing at a loopback "
            "shows only 'backend not connected' on a tester's phone." % release_url)
        self.assertTrue(release_url.startswith("https://"),
                        "the release backend must be https, got [%s]" % release_url)

    # -- Android shape -------------------------------------------------

    def test_android_debug_is_a_loopback_and_release_is_not(self):
        urls = android_backend_urls()
        self.assertTrue(is_loopback(urls["debug"]), "Android debug default should be a loopback")
        self.assertFalse(
            is_loopback(urls["release"]),
            "Android release resolves to [%s]" % urls["release"])
        self.assertTrue(urls["release"].startswith("https://"))

    def test_the_two_platforms_do_not_copy_each_others_loopback(self):
        # An iOS simulator's localhost is the Mac; Android's emulator loopback to the
        # host is 10.0.2.2. Copying either value across platforms silently breaks the
        # development loop on the other one.
        _, debug_part, _, _ = ios_branches()
        ios_debug = sole_returned_string(debug_part, "the iOS #if (debug) branch")
        android_debug = android_backend_urls()["debug"]
        self.assertIn("localhost", ios_debug)
        self.assertIn("10.0.2.2", android_debug)
        self.assertNotEqual(ios_debug, android_debug)

    # -- the one fact --------------------------------------------------

    def test_release_address_is_identical_in_all_three_places(self):
        _, _, release_part, _ = ios_branches()
        ios_release = sole_returned_string(release_part, "the iOS #else (release) branch")
        android_release = android_backend_urls()["release"]
        origins = runbook_origins()
        self.assertEqual(
            ios_release, android_release,
            "iOS release is [%s] but Android release is [%s]. One of the two builds is "
            "talking to the wrong deployment." % (ios_release, android_release))
        self.assertEqual(
            origins, [ios_release],
            "docs/admin_operations.md names %s but the apps ship [%s]; the runbook "
            "would send an operator to the wrong service." % (origins, ios_release))

    # -- the guard must actually run -----------------------------------

    def test_workflow_triggers_on_every_file_this_guard_reads(self):
        for event in ("push", "pull_request"):
            entries = workflow_trigger_paths(event)
            for guarded in GUARDED:
                self.assertIn(
                    guarded, entries,
                    "p0-4-audit.yml `%s` does not trigger on %s, so a change to it would "
                    "merge without this guard ever running." % (event, guarded))

    def test_workflow_runs_this_test(self):
        self.assertIn(
            "python -B %s" % SELF, text(WORKFLOW),
            "p0-4-audit.yml does not invoke this test; the trigger would fire and prove "
            "nothing.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
