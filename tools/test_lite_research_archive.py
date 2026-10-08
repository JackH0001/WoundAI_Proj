import copy, datetime as dt, json, tempfile, unittest
from pathlib import Path
from lite_research_archive import archive,digest,MEDIA,LEDGERS,RESERVE_FREE
NOW=dt.datetime(2026,10,5,tzinfo=dt.timezone.utc)
class Source:
 def __init__(self):self.data={};self.calls=0;self.change=False;self.fail=False
 def put(self,name,data):self.data[name]=(data,'1')
 def inventory(self):
  self.calls+=1
  if self.fail:raise RuntimeError('partial page')
  rows=[dict(name=n,size=len(d),generation=g,updated='2026-08-20T00:00:00+00:00',crc32c='fake') for n,(d,g) in self.data.items()]
  if self.change and self.calls==2:rows[0]['generation']='2'
  return sorted(rows,key=lambda r:r['name'])
 def read(self,r):
  d,g=self.data[r['name']]
  if r['generation']!=g:raise RuntimeError('generation changed')
  return d
 def record(self,owner='owner',iid='a'*16,consent=True):
  self.put(f'{MEDIA}{owner}/{iid}.json',json.dumps(dict(anon_id=owner,image_id=iid,research_consent=consent,consent_version='test-v1')).encode())
  self.put(f'{MEDIA}{owner}/{iid}.jpg',b'synthetic jpeg')
  self.put(LEDGERS[0]+owner+'.jsonl',json.dumps(dict(anon_id=owner,image_id=iid)).encode())
class Tests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.v=Path(self.temp.name).resolve()/'vault';self.s=Source();self.s.record()
 def run_archive(self,**kw):return archive(self.s,self.v,now=NOW,free_bytes=RESERVE_FREE+10**8,**kw)
 def test_export_and_idempotent_reuse(self):
  r=self.run_archive();self.assertEqual(r['records'],1);self.assertFalse(r['training_release_allowed']);self.assertFalse(r['cloud_deletion_enabled']);self.assertEqual(r['raw_rgbd_records'],0)
  r=self.run_archive();self.assertEqual(r['downloaded_bytes'],0);self.assertEqual(r['reused_objects'],2)
 def test_withdrawal_removes_copy(self):
  self.run_archive();self.s.put(LEDGERS[1]+'withdraw.jsonl',b'{"anon_id":"owner","action":"withdrawal_requested"}')
  r=self.run_archive(reconcile_only=True);self.assertEqual(r['records'],0);self.assertEqual(list((self.v/'objects').iterdir()),[])
 def test_shared_jpeg_preserves_other_owner(self):
  self.s.record('other');self.run_archive();self.s.put(LEDGERS[1]+'withdraw.jsonl',b'{"anon_id":"owner","action":"deleted"}')
  r=self.run_archive();self.assertEqual(r['records'],1);self.assertTrue((self.v/'objects'/digest(b'synthetic jpeg')).exists())
 def test_no_consent_excluded(self):
  self.s.record(consent=False);self.assertEqual(self.run_archive()['records'],0)
 def test_generation_race_publishes_nothing(self):
  self.s.change=True
  with self.assertRaises(ValueError):self.run_archive()
  self.assertFalse((self.v/'catalog.json').exists());self.assertFalse(list(self.v.glob('.staging-*')))
 def test_missing_cloud_record_reconciles_out(self):
  self.run_archive();del self.s.data[f'{MEDIA}owner/{"a"*16}.json'];self.assertEqual(self.run_archive(reconcile_only=True)['records'],0)
 def test_reconcile_does_not_claim_changed_generation_current(self):
  self.run_archive();n=f'{MEDIA}owner/{"a"*16}.jpg';self.s.data[n]=(b'changed','2');self.run_archive(reconcile_only=True)
  self.assertFalse(json.loads((self.v/'catalog.json').read_text())['records'][0]['source_unchanged'])
 def test_malformed_ledger_fails_closed(self):
  self.s.put(LEDGERS[1]+'broken.jsonl',b'not-json')
  with self.assertRaises(ValueError):self.run_archive()
 def test_scope_escape_rejected(self):
  self.s.put(MEDIA+'../../private.jpg',b'bad')
  with self.assertRaises(ValueError):self.run_archive()
 def test_partial_inventory_fails(self):
  self.s.fail=True
  with self.assertRaises(RuntimeError):self.run_archive()
 def test_low_disk_keeps_cloud_untouched(self):
  with self.assertRaises(ValueError):archive(self.s,self.v,now=NOW,free_bytes=RESERVE_FREE)
  self.assertFalse((self.v/'catalog.json').exists())
 def test_corrupt_local_copy_not_silently_reused(self):
  self.run_archive();f=self.v/'objects'/digest(b'synthetic jpeg');f.write_bytes(b'bad')
  with self.assertRaises(ValueError):self.run_archive()
 def test_symlink_output_rejected(self):
  self.v.mkdir();(self.v/'objects').symlink_to(Path(self.temp.name),target_is_directory=True)
  with self.assertRaises(ValueError):self.run_archive()
 def test_unknown_withdrawal_action_refused(self):
  self.s.put(LEDGERS[1]+'unknown.jsonl',b'{"anon_id":"owner","action":"restored"}')
  with self.assertRaises(ValueError):self.run_archive()
if __name__=='__main__':unittest.main()
