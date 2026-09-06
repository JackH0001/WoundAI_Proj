#!/usr/bin/env python3
"""Static safety gates for the CI merge gate (Assert-CIGreen.ps1).

On 2026-09-06 a pull request was marked ready and merged with no CI result at
all: `gh pr checks <n> --watch` printed "no checks reported" because the check
runs had not been created yet, and the calling PowerShell line did not test
$LASTEXITCODE. A later attempt reported "already completed with 'success'" for a
run belonging to an earlier push, while the push under test had failed. Both are
fail-open. These tests pin the properties of Assert-CIGreen.ps1 that close them,
so the holes cannot be reopened by a later edit without a red test.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "engineering" / "phase2" / "Assert-CIGreen.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "p0-4-audit.yml"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def code_only(source: str) -> str:
    """Drop whole-line comments so prose about a command is not read as a call."""
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


class CIGateStaticTests(unittest.TestCase):
    def test_gate_is_read_only_and_never_promotes(self):
        src = code_only(text(GATE))
        for forbidden in ("gh pr merge", "gh pr ready", "gh pr edit", "gh workflow run",
                          "git push", "--method", "-X POST", "--quiet"):
            self.assertNotIn(
                forbidden, src,
                "the merge gate must only observe; [%s] would let it act" % forbidden)
        # PowerShell 5.1 has no String.Contains(string, StringComparison).
        self.assertNotIn(".Contains(", src)

    def test_every_input_is_mandatory(self):
        src = text(GATE)
        for declaration in ("[Parameter(Mandatory = $true)][string]$Repo",
                            "[Parameter(Mandatory = $true)][string]$Ref",
                            "[Parameter(Mandatory = $true)][string]$Sha",
                            "[Parameter(Mandatory = $true)][string[]]$Workflow",
                            "[Parameter(Mandatory = $true)][datetime]$NotBefore"):
            self.assertIn(declaration, src,
                          "an optional input becomes a silent default: %s" % declaration)
        self.assertIn('$ErrorActionPreference = "Stop"', src)
        self.assertIn("Set-StrictMode -Version 2.0", src)

    def test_placeholder_shaped_arguments_are_refused(self):
        src = text(GATE)
        self.assertIn(r"$Value -match '[\r\n<>（）]'", src)
        self.assertIn("$Value -match '輸入|填入|placeholder|範本|TODO'", src)
        for label in ("'-Repo'", "'-Ref'", "'-Sha'", "'-Workflow'"):
            self.assertIn("Test-OperatorSupplied %s" % label, src)

    def test_commit_id_must_be_full_and_exact(self):
        src = text(GATE)
        # A prefix comparison against headSha would silently accept the wrong run.
        self.assertIn("$Sha -cnotmatch '^[0-9a-f]{40}$'", src)
        self.assertIn("[string](Get-Prop $run 'headSha') -cne $Sha", src)

    def test_remote_ref_must_already_carry_the_gated_commit(self):
        src = text(GATE)
        # This is what catches a push that failed while an earlier run of the
        # same commit is still sitting there, green.
        self.assertIn("repos/$Repo/git/ref/$Ref", src)
        self.assertIn("$remoteSha -cne $Sha", src)
        self.assertIn("the push did not land", src)

    def test_runs_created_before_the_push_cannot_satisfy_the_gate(self):
        src = text(GATE)
        self.assertIn("$created -lt $notBeforeUtc", src)
        self.assertIn("-NotBefore is in the future", src)

    def test_absent_or_unfinished_runs_are_not_green(self):
        src = code_only(text(GATE))
        self.assertIn('$pending += "$workflowName (no run yet)"', src)
        self.assertIn("if ($pending.Count -eq 0) { break }", src)
        self.assertRegex(src, r'Die "timed out after \$TimeoutSeconds s')

    def test_every_matching_run_is_judged_not_just_the_newest(self):
        src = code_only(text(GATE))
        # One commit can carry a push run and a pull_request run of the same
        # workflow under separate ids (observed 2026-09-06 on gitleaks and
        # integrity-gate). Selecting only the newest would let a red run hide
        # behind a green one.
        self.assertIn("$matched += $run", src)
        self.assertIn("if ($matched.Count -eq 0)", src)
        self.assertIn("$completed[$workflowName] = $matched", src)
        self.assertIn("foreach ($run in @($completed[$workflowName])) {", src)
        # No "newest wins" selection may come back.
        self.assertNotIn("$matchCreated", src)
        self.assertNotRegex(src, r"\$created\s+-gt\s")
        # Every matching run must also be finished before any verdict is given.
        self.assertIn("if ($unfinished.Count -gt 0)", src)

    def test_only_success_passes(self):
        src = code_only(text(GATE))
        self.assertIn("$verdict = 'FAIL'", src)
        # Pinned exactly: adding "-or $conclusion -ceq 'skipped'" turns this red.
        self.assertIn("if ($conclusion -ceq 'success') { $verdict = 'PASS' }", src)
        self.assertIn("required workflows not successful", src)
        for not_success in ("'skipped'", "'neutral'", "'cancelled'", "'timed_out'"):
            self.assertNotIn(
                not_success, src,
                "%s must never be compared as a passing conclusion" % not_success)

    def test_exit_code_is_the_answer(self):
        src = code_only(text(GATE))
        # A caller can only gate on $LASTEXITCODE if the script actually sets it.
        self.assertIn("$exitCode = 1", src)
        self.assertIn("$exitCode = 0", src)
        self.assertRegex(src, r"(?m)^exit \$exitCode\s*$")
        self.assertLess(src.index("$exitCode = 0"), src.rindex("exit $exitCode"))

    def test_ci_runs_this_gate_test(self):
        workflow = text(WORKFLOW)
        # A test the pipeline does not run is documentation, not a gate.
        self.assertIn("python -B engineering/phase2/test_ci_gate_static.py", workflow)
        for watched in ("engineering/phase2/Assert-CIGreen.ps1",
                        "engineering/phase2/test_ci_gate_static.py"):
            self.assertEqual(
                len(re.findall(r"- '%s'" % re.escape(watched), workflow)), 2,
                "%s must be in both the push and pull_request path filters" % watched)


if __name__ == "__main__":
    unittest.main(verbosity=2)
