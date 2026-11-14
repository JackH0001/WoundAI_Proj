"""Two synthetic issuer keys. Private keys never enter the application package."""
from base64 import b64encode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_research_association import ResearchAssociations

_KEYS={role:Ed25519PrivateKey.generate() for role in ('native','research')}

def pair_config(f, research, pairs, *, operation='d'*32):
    # Only the test process holds these throwaway private keys.
    keys=_KEYS
    signed=[]
    for native,owner in pairs:
        pair=dict(action='withdraw-linked-research/1',scope=f.d.scope,bucket=research.privacy.objects.bucket.name,
            person=f.person,native=native,research=owner,operation=operation,
            registration=f.d.bridge.registry.registration(native)['proof'])
        signed.append(dict(pair=pair,**{r:b64encode(k.sign(ResearchAssociations.message(r,pair))).decode()
                                     for r,k in keys.items()}))
    return dict(native_key=b64encode(keys['native'].public_key().public_bytes_raw()).decode(),
        research_key=b64encode(keys['research'].public_key().public_bytes_raw()).decode(),proofs=signed)

def pairs(f,research,subjects):
    from base64 import b64decode
    config=pair_config(f,research,subjects)
    return ResearchAssociations(b64decode(config['native_key']),b64decode(config['research_key']),config['proofs'])
