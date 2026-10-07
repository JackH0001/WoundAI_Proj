"""Incremental private archive of legacy Lite research data; no cloud writes.

Archives are QUARANTINED, not training datasets. A fresh cloud read is required
before every research release. Existing legacy bucket lifecycle/versioning and
new Lite generation fences must not be bypassed with age-based bucket deletes.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, shutil, subprocess, tempfile
from pathlib import Path

PROJECT = 'woundai-jackh001'
BUCKET = 'woundai-flywheel-jackh001'
MEDIA = 'flywheel/lite/'
LEDGERS = ('flywheel/lite_index.jsonl/', 'flywheel/lite_labels.jsonl/')
REVOKED = {'deleted', 'withdrawal_requested', 'delete_incomplete'}
PATTERN = re.compile(r'flywheel/lite/([A-Za-z0-9_-]{1,128})/([0-9a-f]{16})\.(jpg|json|depth\.png|conf\.png)\Z')
MAX_OBJECT = 24 * 1024**2
MAX_TOTAL = 5 * 1024**3
RESERVE_FREE = 30 * 1024**3


def digest(data): return hashlib.sha256(data).hexdigest()
def signature(rows): return {(r['name'], r['generation'], r['size'], r['updated']) for r in rows}


def read_rows(source, inventory):
    result = []
    for row in inventory:
        if any(row['name'].startswith(p) for p in LEDGERS):
            for line in source.read(row).decode('utf-8').splitlines():
                if not line.strip(): continue
                item = json.loads(line)
                if not isinstance(item, dict) or not isinstance(item.get('anon_id'), str):
                    raise ValueError('invalid research ledger; no archive release')
                if item.get('action') and item['action'] not in REVOKED:
                    raise ValueError('unknown ledger action; stop')
                result.append(item)
    return result


def atomic_json(path, data):
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def _archive(source, vault, stage, *, now=None, free_bytes=None, reconcile_only=False):
    now = now or dt.datetime.now(dt.timezone.utc)
    vault = Path(vault)
    # This directory is deliberately outside all source repositories and sync folders.
    if vault.is_symlink() or vault.resolve() != vault.absolute(): raise ValueError('symlink archive path')
    vault.mkdir(parents=True, exist_ok=True, mode=0o700); vault.chmod(0o700)
    for name in ('objects',):
        p = vault/name
        if p.is_symlink(): raise ValueError('symlink archive subdirectory')
        p.mkdir(exist_ok=True, mode=0o700)
    catalog_file = vault/'catalog.json'
    if catalog_file.is_symlink(): raise ValueError('symlink catalog')
    old = json.loads(catalog_file.read_text()) if catalog_file.exists() else {'records': []}
    inventory = source.inventory()
    if len(inventory) > 10000 or len(signature(inventory)) != len(inventory): raise ValueError('incomplete or duplicate inventory')
    for row in inventory:
        if not (PATTERN.fullmatch(row['name']) or any(row['name'].startswith(p) and re.fullmatch(r'[0-9A-Za-z_-]+\.jsonl',row['name'][len(p):]) for p in LEDGERS)):
            raise ValueError('unexpected object scope')
        if type(row['size']) is not int or not 0 <= row['size'] <= MAX_OBJECT: raise ValueError('object exceeds archive bound')
    total_size=sum(r['size'] for r in inventory)
    free=shutil.disk_usage(vault).free if free_bytes is None else free_bytes
    if total_size>MAX_TOTAL or free-total_size<RESERVE_FREE: raise ValueError('archive size/free space bound')
    ledger = read_rows(source, inventory)
    revoked = {r['anon_id'] for r in ledger if r.get('action') in REVOKED}
    existing = {(r['owner'], r['image_id']):r for r in old['records']}
    records = []; pending = {}; transferred = 0; reused = 0; skipped = 0
    files = {r['name']: r for r in inventory}
    for row in inventory:
        match = PATTERN.fullmatch(row['name'])
        if not match or match[3] != 'json': continue
        owner, iid = match[1], match[2]
        if owner in revoked: skipped += 1; continue
        meta_bytes = source.read(row); meta = json.loads(meta_bytes)
        if not isinstance(meta,dict) or meta.get('anon_id') != owner or meta.get('image_id') != iid: raise ValueError('metadata owner mismatch')
        if meta.get('research_consent') is not True or not isinstance(meta.get('consent_version'),str) or not meta['consent_version'].strip():
            skipped += 1; continue
        if reconcile_only:
            prior = existing.get((owner, iid))
            if prior is not None:
                # Changed cloud content is quarantined until weekly refresh, never relabelled current.
                current = all(files.get(a['name'],{}).get('generation') == a['generation'] for a in prior['assets'])
                records.append(dict(prior, source_unchanged=current))
            continue
        assets=[]
        for ext in ('jpg','json','depth.png','conf.png'):
            name=f'{MEDIA}{owner}/{iid}.{ext}'; item=files.get(name)
            if item is None: continue
            prior=existing.get((owner,iid),{})
            cached=next((a for a in prior.get('assets',[]) if a['name']==name and a['generation']==item['generation']),None)
            if cached is not None:
                key=cached['sha256']
                if re.fullmatch('[0-9a-f]{64}',key) is None: raise ValueError('invalid cached digest')
                file=vault/'objects'/key
                if file.is_symlink(): raise ValueError('symlink cached object')
                if file.is_file() and digest(file.read_bytes())==key:
                    assets.append(cached);reused+=1;continue
            data=meta_bytes if ext=='json' else source.read(item)
            if len(data)!=item['size']: raise ValueError('object byte count mismatch')
            key=digest(data); staged=stage/key; staged.write_bytes(data); staged.chmod(0o600); pending[key]=staged; transferred+=len(data)
            assets.append(dict(item,sha256=key))
        if not any(a['name'].endswith('.jpg') for a in assets): skipped+=1;continue
        records.append({'owner':owner,'image_id':iid,'consent_version':meta['consent_version'],'assets':assets,
                        'raw_rgbd_present':False,'depth_png_present':any(a['name'].endswith('.depth.png') for a in assets),
                        'source_unchanged':True,'training_admission':'quarantined','reason':'legacy consent/quality and raw RGB-D completeness require review',
                        'labels':[r for r in ledger if r.get('anon_id')==owner and r.get('image_id')==iid and not r.get('action')]})
    # Whole inventory checked again; changed versions, new withdrawals, or partial reads abort.
    fresh=source.inventory()
    if signature(fresh)!=signature(inventory): raise ValueError('cloud changed during archive; retry fresh')
    if read_rows(source,fresh)!=ledger: raise ValueError('consent changed during archive')
    if sum(f.stat().st_size for f in pending.values())>MAX_TOTAL: raise ValueError('increment exceeds 5 GiB')
    free=shutil.disk_usage(vault).free if free_bytes is None else free_bytes
    if free-sum(f.stat().st_size for f in pending.values())<RESERVE_FREE: raise ValueError('keep at least 30 GiB free')
    for key,staged in pending.items():
        data=staged.read_bytes()
        f=vault/'objects'/key
        if f.is_symlink():raise ValueError('symlink output')
        if not f.exists():
            fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        if digest(f.read_bytes())!=key: raise ValueError('local readback checksum failure')
    catalog={'schema':'woundlite.private-quarantine/1','project':PROJECT,'bucket':BUCKET,
             'verified_at':now.isoformat(),'training_release_allowed':False,'records':records,
             'withdrawal_freshness_hours':24,'note':'Daily reconciliation is eventual, not synchronous withdrawal; offline research release forbidden.'}
    atomic_json(catalog_file,catalog)
    refs={a['sha256'] for r in records for a in r['assets']};removed=0
    for f in (vault/'objects').iterdir():
        if f.is_symlink() or not re.fullmatch('[0-9a-f]{64}',f.name):raise ValueError('unexpected archive object')
        if f.name not in refs:f.unlink();removed+=1
    dates=[dt.datetime.fromisoformat(a['updated'].replace('Z','+00:00')) for r in records for a in r['assets']]
    eligible=sum((now-max(dt.datetime.fromisoformat(a['updated'].replace('Z','+00:00')) for a in r['assets'])).days>=30 for r in records)
    result={'records':len(records),'objects':len(refs),'downloaded_bytes':transferred,'reused_objects':reused,'removed_unreferenced_local_objects':removed,
            'excluded_records':skipped,'revoked_owners':len(revoked),'cloud_delete_candidate_records':eligible,
            'cloud_deletion_enabled':False,'reason':'legacy mixed bucket and online revision semantics; no capture retirement protocol',
            'raw_rgbd_records':0,'depth_png_records':sum(r['depth_png_present'] for r in records),'training_release_allowed':False,'verified_at':now.isoformat()}
    atomic_json(vault/'summary.json',result);return result


def archive(source, vault, **kwargs):
    vault=Path(vault)
    if vault.is_symlink() or vault.resolve()!=vault.absolute():raise ValueError('symlink archive path')
    vault.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.staging-',dir=vault) as stage:
        return _archive(source,vault,Path(stage),**kwargs)


class GCS:
    def __init__(self,client):self.bucket=client.bucket(BUCKET)
    def inventory(self):
        rows=[]
        for prefix in (MEDIA,*LEDGERS):
            listed=list(self.bucket.list_blobs(prefix=prefix,max_results=10001,timeout=30,retry=None))
            if len(listed)>10000:raise ValueError('inventory limit')
            for b in listed:rows.append(dict(name=b.name,generation=str(b.generation),size=b.size,crc32c=b.crc32c,updated=b.updated.isoformat()))
        return sorted(rows,key=lambda r:r['name'])
    def read(self,row):
        # No Blob(generation=...) fallback: current live object must match the pin.
        return self.bucket.blob(row['name']).download_as_bytes(if_generation_match=int(row['generation']),checksum='crc32c',timeout=30,retry=None)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--vault',required=True,type=Path);ap.add_argument('--reconcile-only',action='store_true');args=ap.parse_args()
    from google.cloud import storage
    from google.oauth2.credentials import Credentials
    import fcntl
    os.umask(0o077)
    fv=subprocess.run(['fdesetup','status'],capture_output=True,text=True,check=True)
    if 'FileVault is On.' not in fv.stdout:raise ValueError('FileVault not confirmed')
    args.vault.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.vault.is_symlink() or args.vault.resolve()!=args.vault.absolute():raise ValueError('symlink vault')
    lock=os.open(args.vault/'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(lock,'w') as locked:
        fcntl.flock(locked,fcntl.LOCK_EX|fcntl.LOCK_NB)
        token=subprocess.run(['/opt/homebrew/bin/gcloud','auth','print-access-token','--project='+PROJECT,'--quiet'],capture_output=True,text=True,check=True).stdout.strip()
        client=storage.Client(project=PROJECT,credentials=Credentials(token))
        print(json.dumps(archive(GCS(client),args.vault,reconcile_only=args.reconcile_only),ensure_ascii=False))
if __name__=='__main__':main()
