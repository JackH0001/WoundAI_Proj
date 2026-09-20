#!/usr/bin/env python3
# The docstring quotes regexes verbatim, so it must be a raw string: a bare
# "\s" in a normal string literal is a SyntaxWarning on Python 3.12+ and
# becomes a SyntaxError in 3.14.
r"""The secret-key rule must catch assignments and ignore name lists.

PR #8 (2026-09-20) was blocked by three findings that were not secrets. The rule
regex was

    (?i)secret[_-]?key['"]?\s*[,=]\s*['"][\w\-]{8,}['"]

and the unqualified `,` made it read a list of ENVIRONMENT VARIABLE NAMES --
("JWT_SECRET_KEY", "FLASK_SECRET_KEY") -- as "secret_key assigned an 8+
character value". The response at the time was to rewrite the application code
into "A B C".split() so it would stop matching. Contorting source to satisfy a
defective lint is backwards: the rule was wrong, so the rule is what changed.

The comma form still has to work, because the shape the rule's description
actually names -- os.environ.get('FLASK_SECRET_KEY', '<literal>') -- is a comma
form. So the regex now spells out the two real assignment syntaxes instead of
accepting any comma.

These tests run both corpora through the live regex read out of .gitleaks.toml,
so the rule cannot be loosened or re-broken without a red test.

Known gaps, deliberately left alone (the previous regex missed these too, so
nothing regressed; closing them means either cleaning ~20 call sites or adding
exemptions, which is its own change):
  * app.config['SECRET_KEY'] = '<literal>'   -- quote-then-bracket before the =
  * secret_key: "<literal>"                  -- dict / YAML colon form
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".gitleaks.toml"
RULE_ID = "woundai-flask-secret-default"

# Lines that must NEVER be reported. Every one of these is a name list or a call
# that passes a name, not a value. The first two are verbatim from PR #8.
MUST_NOT_MATCH = (
    'for key in ("ADMIN_PASSWORD", "JWT_SECRET_KEY", "FLASK_SECRET_KEY", "CARE_RECEIPT_SECRET"):',
    '_SERVER_ONLY = ["JWT_SECRET_KEY", "FLASK_SECRET_KEY"]',
    'STRIP = ("SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD")',
    '_SERVER_ONLY_SECRETS = set("ADMIN_PASSWORD JWT_SECRET_KEY FLASK_SECRET_KEY".split())',
    "SECRET_KEY=resolve_secret('FLASK_SECRET_KEY', 'Flask session and itsdangerous signing')",
    "JWT_SECRET_KEY=resolve_secret('JWT_SECRET_KEY', 'access tokens issued to the apps')",
    'self.assertEqual(env["JWT_SECRET_KEY"], env["FLASK_SECRET_KEY"])',
    # Below the rule's 8-character floor. Pinned so the floor cannot be dropped:
    # without it the rule fires on every empty or placeholder assignment and
    # becomes noise, which is how a guard gets allowlisted into uselessness.
    'SECRET_KEY = ""',
    "SECRET_KEY = 'short'",
)

# Lines that must ALWAYS be reported. The marker keeps this file exempt under the
# rule's own phase2 allowlist (path AND literal), so pinning the rule does not
# make the repository look like it leaks.
MUST_MATCH = (
    'SECRET_KEY = "synthetic-test-only-abcdef123456"',
    "SECRET_KEY=os.environ.get('FLASK_SECRET_KEY', 'synthetic-test-only-default')",
    'jwt = os.getenv("JWT_SECRET_KEY", "synthetic-test-only-9f2a")',
    "secret-key = 'synthetic-test-only-dashed'",
)


def rule_regex() -> str:
    """Read the live regex for RULE_ID out of .gitleaks.toml.

    Deliberately not a TOML library: the backend runtime lock carries no TOML
    parser, and a test that needs an extra dependency is a test that gets
    skipped. Raises rather than returning None, so a config reshuffle fails
    loudly instead of quietly testing nothing.
    """
    text = CONFIG.read_text(encoding="utf-8-sig")
    marker = 'id = "%s"' % RULE_ID
    start = text.find(marker)
    if start < 0:
        raise AssertionError("%s is no longer defined in .gitleaks.toml" % RULE_ID)
    end = text.find("[[rules]]", start)
    block = text[start:] if end < 0 else text[start:end]
    found = re.findall(r"^regex = '''(.+?)'''\s*$", block, re.M)
    if len(found) != 1:
        raise AssertionError(
            "expected exactly one regex in the %s block, found %d" % (RULE_ID, len(found)))
    return found[0]


class SecretKeyRulePrecisionTests(unittest.TestCase):

    def setUp(self):
        self.pattern = re.compile(rule_regex())

    def test_name_lists_are_not_assignments(self):
        for line in MUST_NOT_MATCH:
            hit = self.pattern.search(line)
            self.assertIsNone(
                hit,
                "the rule reports a name list as a hardcoded secret:\n  %s\n  matched: %r"
                % (line, hit.group(0) if hit else None))

    def test_real_hardcoded_values_are_still_caught(self):
        for line in MUST_MATCH:
            self.assertIsNotNone(
                self.pattern.search(line),
                "the rule no longer catches a hardcoded secret:\n  %s" % line)

    def test_the_unqualified_comma_alternation_does_not_come_back(self):
        # The single character that caused PR #8. A bare [,=] (or [=,]) accepts any
        # comma, which is what turned a list of names into a finding.
        self.assertNotRegex(
            rule_regex(), r"\[[,=]{2}\]",
            "an unqualified comma alternation is back; name lists will be reported again")

    def test_the_environ_default_form_is_still_covered(self):
        # The comma form the rule's description actually targets must survive the
        # fix -- removing the comma entirely would have been a silent regression.
        self.assertIn("get", rule_regex(),
                      "the get()/getenv() default form is no longer matched at all")


if __name__ == "__main__":
    unittest.main(verbosity=2)
