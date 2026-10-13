"""Loopback-only, in-memory UI fixture. No Google credentials or cloud adapter.

Run: python -B -m Backend.AccessManager.demo
Not a deployment entry point; never bind this fixture to a public interface.
"""
from .test_iap_policy import MemoryAdapter, MemoryAudit
from .web import VerifiedAdmin, create_app
from .accounts import AccountAdapter
from .test_accounts import MemoryAccountsHttp


if __name__ == "__main__":
    account = {"org": "mmhps20261007", "identity": "mmhps20261007:demo-admin", "role": "admin", "role_zh": "管理者", "display_name": "合成管理者", "disabled": False}
    principal = VerifiedAdmin("admin@example.test", "synthetic-subject", account["identity"], "admin", "mmhps20261007", "demo-only")
    accounts = AccountAdapter(MemoryAccountsHttp())
    app = create_app(origin="http://127.0.0.1:8768", demonstration=True,
        authenticate=lambda request: principal, policy_adapter=MemoryAdapter(), audit=MemoryAudit(),
        list_accounts=accounts.list_accounts,
        admin_emails=[principal.google_email], account_adapter_for=lambda request,p: accounts)
    app.run(host="127.0.0.1", port=8768, debug=False)
