#!/usr/bin/env python3
"""Static safety gates for the PowerShell cloud deployment tools."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "Backend" / "Flask" / "deploy_cloudrun.ps1"
HARDEN = ROOT / "Backend" / "Flask" / "harden_bucket.ps1"
PROVISION = ROOT / "Backend" / "Flask" / "provision_runtime_identity.ps1"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class DeploymentScriptSafetyTests(unittest.TestCase):
    def test_deploy_is_clean_explicit_and_non_mutating_iam(self):
        src = text(DEPLOY)
        self.assertRegex(src, r"\[Parameter\(Mandatory = \$true\)\]\[string\]\$AuditBucket")
        self.assertRegex(src, r"\[Parameter\(Mandatory = \$true\)\]\[string\]\$RuntimeServiceAccount")
        self.assertIn("refuse to deploy a dirty worktree", src)
        self.assertIn("--raw --format=json", src)
        self.assertIn("--service-account $RuntimeServiceAccount", src)
        self.assertNotIn("add-iam-policy-binding", src)
        self.assertNotIn("roles/storage.objectAdmin", src)
        self.assertRegex(src, r"\(smoke\|test\|tmp\|temp\|dev\|sandbox\)")
        self.assertIn("-Setup is retired for P0-4", src)
        self.assertIn("deployment source must be the checked-out main branch", src)
        self.assertIn("local main is not the current reviewed origin/main commit", src)
        self.assertIn("https://github.com/JackH0001/WoundAI_Proj.git", src)

    def test_deploy_preflight_precedes_publish_and_verify_only_repeats_it(self):
        src = text(DEPLOY)
        publish = src.index("Invoke-GCloud run deploy")
        preflight = src.rfind("Invoke-DeploymentPreflight", 0, publish)
        self.assertGreater(preflight, 0)
        verify = src.index('Say "只跑驗證')
        self.assertGreater(src.find("Invoke-DeploymentPreflight", verify), verify)
        self.assertIn("Assert-CloudRunRevisionConfiguration", src)
        self.assertIn("latest created revision", src)
        self.assertIn("traffic is not exclusively pinned", src)
        self.assertIn("known-default password probe was unreachable", src)
        self.assertIn("unauthenticated probe returned unexpected HTTP", src)

    def test_deploy_verifies_a_no_traffic_candidate_before_cutover(self):
        src = text(DEPLOY)
        publish = src.index("Invoke-GCloud run deploy")
        no_traffic = src.index("--no-traffic", publish)
        candidate_config = src.index(
            "Assert-CloudRunRevisionConfiguration -CandidateTag", no_traffic)
        candidate_health = src.index('Say "部署後驗證"', candidate_config)
        cutover = src.index("Invoke-GCloud run services update-traffic", candidate_health)
        live_verify = src.index(
            "Assert-CloudRunRevisionConfiguration -RequireExclusiveTraffic", cutover)
        rollback = src.index("rolling traffic back", live_verify)
        self.assertLess(publish, no_traffic)
        self.assertLess(no_traffic, candidate_config)
        self.assertLess(candidate_config, candidate_health)
        self.assertLess(candidate_health, cutover)
        self.assertLess(cutover, live_verify)
        self.assertLess(live_verify, rollback)
        self.assertIn("--to-revisions=$PreviousRevision=100", src)

    def test_harden_lock_is_named_and_evidenced(self):
        src = text(HARDEN)
        self.assertIn("-LockRetention refuses a disposable-environment bucket name", src)
        self.assertIn("-LockRecordPath", src)
        for field in (
            "authorisation_reference", "operator", "before_raw_sha256",
            "after_raw_sha256", "hash_basis", "lock_performed_this_run", "locked",
        ):
            self.assertIn(field, src)
        self.assertIn("--lock-retention-period", src)
        self.assertLess(src.index("Assert-AuditObjectsExpected $AuditBucket -RequireEmpty:$LockRetention"),
                        src.index("--lock-retention-period"))

    def test_provisioner_is_dry_by_default_and_uses_exact_roles(self):
        src = text(PROVISION)
        self.assertRegex(src, r"\[switch\]\$Apply")
        self.assertIn("DRY RUN ONLY", src)
        self.assertNotIn("roles/storage.objectAdmin", src)
        self.assertNotIn("roles/editor", src)
        expected = {
            "storage.objects.create", "storage.objects.delete", "storage.objects.get",
            "storage.objects.list", "storage.buckets.get",
        }
        for permission in expected:
            self.assertIn(permission, src)
        self.assertIn("roles/secretmanager.secretAccessor", src)
        self.assertIn("runtime identity has forbidden project-level roles", src)
        self.assertRegex(src, r"if \(\$Apply\) \{")


if __name__ == "__main__":
    unittest.main(verbosity=2)
