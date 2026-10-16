#!/usr/bin/env python3
"""Pin each shipping profile to its documented backend, including demo isolation.

Medical TestFlight uses the isolated demo service. Lite uses its dedicated
release binding; Android retains its separately documented profiles. A single universal
URL assertion would reject this intentional split; dropping parity altogether
would allow an unnoticed wrong-environment release. Parse the explicit profile
switch fail-closed and compare each profile with a named runbook entry.
"""
from pathlib import Path
import re
import plistlib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "iOS" / "WoundMeasurementApp" / "Core" / "AppSettings.swift"
LITE_CONFIG = ROOT / "iOS/WoundLite/LiteBackendConfiguration.swift"
LITE_INFO = ROOT / "iOS/WoundLite/Info.plist"
PROJECT = ROOT / "iOS/project.yml"
GRADLE = ROOT / "Android" / "app" / "build.gradle"
RUNBOOK = ROOT / "docs" / "admin_operations.md"
WORKFLOW = ROOT / ".github" / "workflows" / "p0-4-audit.yml"

SELF = "engineering/phase2/test_backend_url_parity.py"
# The files whose drift this test exists to catch. A workflow that does not
# trigger on all of them does not run this guard on the very change it guards.
GUARDED = (
    SELF,
    "iOS/WoundMeasurementApp/Core/AppSettings.swift",
    "iOS/WoundLite/LiteBackendConfiguration.swift",
    "iOS/WoundLite/Info.plist",
    "iOS/project.yml",
    "Android/app/build.gradle",
    "docs/admin_operations.md",
)

MMH_STABLE = "https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app"
MMH_SOURCES = (
    "Android/app/src/main/java/com/woundmeasurement/app/institution/InstitutionSession.kt",
    "Backend/AccessManager/authentication.py", "Backend/AccessManager/accounts.py",
    "Backend/AccessManager/sessions.py", "Backend/AccessManager/web.py",
    "Backend/AccessManager/static/members.js",
)


def assert_mmh_stable_sources(sources):
    for name, source in sources.items():
        origins = re.findall(r"https://[A-Za-z0-9.\-]+\.run\.app", source)
        if not origins or set(origins) != {MMH_STABLE}:
            raise AssertionError("MMH stable origin drift: " + name)


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


def ios_profile_urls():
    # Full-match the small switch: unexpected conditions, nested branches, early
    # returns, extra statements or a different profile order must fail closed.
    body = without_line_comments(ios_default_url_body(), "//")
    match = re.fullmatch(
        r'\s*#if DEBUG && targetEnvironment\(simulator\)\s+'
        r'return "(?P<debug>[^"\n]+)"\s+'
        r'#elseif WOUND_LITE\s+return LiteBackendConfiguration\.releaseOrigin\(\s*'
        r'Bundle\.main\.object\(forInfoDictionaryKey: "LiteBackendURL"\) as\? String\)\s+'
        r'#else\s+return demoURL\s+#endif\s*', body)
    if not match:
        raise AssertionError("unrecognized defaultURL profile switch; inspect all shipping profiles")
    src = without_line_comments(text(IOS), "//")
    urls = {"debug": match.group("debug")}
    for constant, profile in (("demoURL", "ios-medical-testflight"),):
        values = re.findall(
            r'^[ \t]*(?:private )?static let ' + constant + r' = "([^"\n]+)"[ \t]*$',
            src, re.M)
        if len(values) != 1:
            raise AssertionError("expected one literal declaration for " + constant)
        urls[profile] = values[0]
    urls['ios-lite'] = lite_release_url()
    return urls


def lite_release_url():
    config = without_line_comments(text(LITE_CONFIG), '//')
    origins = re.findall(r'^\s*static let dedicatedOrigin = "([^"\n]+)"\s*$', config, re.M)
    if len(origins) != 1 or not RUN_APP.fullmatch(origins[0]):
        raise AssertionError('Lite must have one explicit dedicated HTTPS origin')
    if plistlib.loads(text(LITE_INFO).encode()).get('LiteBackendURL') != '$(WOUNDLITE_BACKEND_URL)':
        raise AssertionError('Lite Info.plist must use its release build setting')
    project = text(PROJECT)
    block = re.search(r'^  WoundLite:\n(.*?)(?=^  \w+:|\Z)', project, re.M | re.S)
    if not block:
        raise AssertionError('Missing dedicated Lite target')
    settings = re.findall(r'^\s*WOUNDLITE_BACKEND_URL: "([^"\n]+)"\s*$', block.group(1), re.M)
    if settings != origins:
        raise AssertionError('Lite bundle setting disagrees with origin allowlist')
    if not re.search(r'static func backendURL\(\) -> String \{\s*#if WOUND_LITE\s*return defaultURL',
                     without_line_comments(text(IOS), '//')):
        raise AssertionError('Saved medical settings must not override Lite origin')
    return origins[0]


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
            hit = re.match(r"[ \t]*(release|debug|mmhValidation|mmhInternal|internalTest)[ \t]*\{[ \t]*$", lines[back])
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
    if set(found) != {"release", "debug", "mmhValidation", "mmhInternal"}:
        raise AssertionError(
            "expected one DEFAULT_BACKEND_URL in each of release, debug, mmhValidation and mmhInternal; got %s"
            % sorted(found))
    return found


# ------------------------------------------------------------ runbook

def runbook_profiles():
    src = text(RUNBOOK)
    pairs = re.findall(
        r'^\| `(ios-medical-testflight|ios-lite|android-release|android-mmh-validation|android-mmh-internal)` \| `(https://[^`]+)` \|$',
        src, re.M)
    profiles = dict(pairs)
    expected = {"ios-medical-testflight", "ios-lite", "android-release", "android-mmh-validation", "android-mmh-internal"}
    if len(pairs) != 5 or set(profiles) != expected:
        raise AssertionError("runbook must name each shipping profile exactly once")
    if set(RUN_APP.findall(src)) != set(profiles.values()):
        raise AssertionError("runbook contains an undocumented or missing backend origin")
    return profiles


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

    def test_ios_profile_switch_is_explicit(self):
        self.assertEqual(set(ios_profile_urls()),
                         {"debug", "ios-lite", "ios-medical-testflight"})

    def test_ios_debug_is_loopback_and_both_shipping_profiles_are_https(self):
        urls = ios_profile_urls()
        self.assertEqual(urls["debug"], "http://localhost:5000")
        for profile in ("ios-lite", "ios-medical-testflight"):
            self.assertFalse(is_loopback(urls[profile]), profile)
            self.assertIsNotNone(RUN_APP.fullmatch(urls[profile]), profile)
        self.assertNotEqual(urls["ios-lite"], urls["ios-medical-testflight"],
                            "demo and existing service must remain isolated")

    # -- Android shape -------------------------------------------------

    def test_android_debug_is_a_loopback_and_release_is_not(self):
        urls = android_backend_urls()
        self.assertTrue(is_loopback(urls["debug"]), "Android debug default should be a loopback")
        self.assertFalse(
            is_loopback(urls["release"]),
            "Android release resolves to [%s]" % urls["release"])
        self.assertTrue(urls["release"].startswith("https://"))

    def test_mmh_validation_is_separate_and_private_stable(self):
        urls = android_backend_urls()
        self.assertEqual(urls["mmhValidation"], "https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app")
        self.assertNotEqual(urls["release"], urls["mmhValidation"])
        src = text(GRADLE)
        self.assertIn('applicationIdSuffix ".mmhps20261007.validation"', src)
        self.assertIn('"MMH_VALIDATION", "true"', src)

    def test_mmh_internal_is_separate_and_private_stable(self):
        urls = android_backend_urls()
        self.assertEqual(urls["mmhInternal"], "https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app")
        self.assertNotEqual(urls["release"], urls["mmhInternal"])

    def test_mmh_mobile_and_manager_use_same_stable_origin(self):
        assert_mmh_stable_sources({name: text(ROOT / name) for name in MMH_SOURCES})
        info = plistlib.loads((ROOT / "iOS/WoundMeasurementApp/InstitutionInfo.plist").read_bytes())
        self.assertEqual(info["WoundAIInstitutionBackendURL"], MMH_STABLE)

    def test_candidate_drift_in_each_transport_is_rejected(self):
        sources = {name: text(ROOT / name) for name in MMH_SOURCES}
        for name in sources:
            changed = dict(sources)
            changed[name] = changed[name].replace(MMH_STABLE, MMH_STABLE.replace("https://", "https://candidate---"))
            with self.subTest(source=name), self.assertRaises(AssertionError):
                assert_mmh_stable_sources(changed)

    def test_candidate_drift_in_each_android_build_is_rejected(self):
        original = text(GRADLE)
        for build, check in (("mmhValidation", self.test_mmh_validation_is_separate_and_private_stable),
                             ("mmhInternal", self.test_mmh_internal_is_separate_and_private_stable)):
            before, marker, after = original.partition("        " + build + " {")
            self.assertTrue(marker)
            changed = before + marker + after.replace(MMH_STABLE, MMH_STABLE.replace("https://", "https://candidate---"), 1)
            with self.subTest(build=build), patch(__name__ + ".text", return_value=changed), self.assertRaises(AssertionError):
                check()

    def test_wrong_mmh_internal_address_is_rejected(self):
        original = text(GRADLE)
        before, marker, after = original.partition("        mmhInternal {")
        self.assertTrue(marker)
        changed = before + marker + after.replace(
            "https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app",
            "https://woundai-backend-421209514056.asia-east1.run.app", 1)
        with patch(__name__ + ".text", return_value=changed):
            with self.assertRaises(AssertionError):
                self.test_mmh_internal_is_separate_and_private_stable()

    def test_missing_or_unknown_mmh_internal_profile_is_rejected(self):
        original = text(GRADLE)
        for replacement in ("unknownProfile", "mmhValidation"):
            with self.subTest(replacement=replacement), patch(
                __name__ + ".text", return_value=original.replace("        mmhInternal {", "        " + replacement + " {")):
                with self.assertRaises(AssertionError):
                    android_backend_urls()

    def test_the_two_platforms_do_not_copy_each_others_loopback(self):
        # An iOS simulator's localhost is the Mac; Android's emulator loopback to the
        # host is 10.0.2.2. Copying either value across platforms silently breaks the
        # development loop on the other one.
        ios_debug = ios_profile_urls()["debug"]
        android_debug = android_backend_urls()["debug"]
        self.assertIn("localhost", ios_debug)
        self.assertIn("10.0.2.2", android_debug)
        self.assertNotEqual(ios_debug, android_debug)

    def test_each_shipping_profile_matches_the_runbook(self):
        ios = ios_profile_urls()
        shipped = {
            "ios-medical-testflight": ios["ios-medical-testflight"],
            "ios-lite": ios["ios-lite"],
            "android-release": android_backend_urls()["release"],
            "android-mmh-validation": android_backend_urls()["mmhValidation"],
            "android-mmh-internal": android_backend_urls()["mmhInternal"],
        }
        self.assertEqual(shipped, runbook_profiles())
        self.assertNotEqual(shipped["ios-lite"], shipped["android-release"],
                            "Lite must not reuse the medical service")

    def test_lite_release_binding_drift_is_rejected(self):
        original = text
        mutations = {
            LITE_INFO: original(LITE_INFO).replace('$(WOUNDLITE_BACKEND_URL)', 'https://wrong.run.app'),
            PROJECT: original(PROJECT).replace('WOUNDLITE_BACKEND_URL: "https://', 'WOUNDLITE_BACKEND_URL: "https://wrong-'),
            LITE_CONFIG: original(LITE_CONFIG).replace('static let dedicatedOrigin = "https://', 'static let dedicatedOrigin = "https://wrong-'),
            IOS: original(IOS).replace('#if WOUND_LITE\n        // A saved', '#if WOUND_OTHER\n        // A saved'),
        }
        for path, changed in mutations.items():
            with self.subTest(path=path), patch(__name__+'.text', side_effect=lambda p: changed if p==path else original(p)):
                with self.assertRaises(AssertionError):
                    lite_release_url()

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
