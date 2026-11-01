"""Private provisioning client. No KMS creation rights or credentials in the web app."""
import re
from audit_chain_contract import loads_json_object_strict
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest, _hex

PATH='/internal/lite/account-key/prepare'


class KeyReadinessClient:
    def __init__(self, *, session, origin, scope, keyring):
        if (not callable(getattr(session,'request',None)) or type(origin) is not str
                or re.fullmatch(r'https://[a-z0-9][a-z0-9-]+\.a\.run\.app',origin) is None):
            raise ValueError('explicit private Cloud Run origin and identity transport required')
        _hex(scope)
        self.session,self.origin,self.scope,self.keyring=session,origin,scope,keyring

    def ensure(self, context, version):
        body=dict(v=1,scope=self.scope,person=context['identity_sha256'],grant=context['grant_id'],version=version)
        for name in ('person','grant'):_hex(body[name])
        try:
            r=self.session.request('POST',self.origin+PATH,json=body,timeout=(5,20),allow_redirects=False)
            if r.status_code!=200 or len(r.content)>4096:raise ValueError()
            value=loads_json_object_strict(r.content.decode())
            if value!=dict(v=1,state='ready',request_sha256=digest(body),version=version):raise ValueError()
        except Exception:
            raise StateUnavailable('account key preparation unconfirmed; Apple code not exchanged') from None
