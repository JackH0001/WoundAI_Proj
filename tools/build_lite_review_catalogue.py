#!/usr/bin/env python3
"""Validate an operator-supplied finite snapshot; never list/read cloud storage.

Input is lite-review-catalogue/1 with exact owner/epoch paths and generation/hash
pins obtained from a separately approved bounded inventory. Does not certify
completeness or current consent. Real review data must stay out of public Git.
"""
import argparse,hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Backend.LiteReview.contracts import Catalogue,decode,encoded

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True);p.add_argument('--expected-sha256',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();source=Path(a.input)
    if source.stat().st_size>16*1024*1024:raise SystemExit('snapshot too large')
    data=source.read_bytes()
    if hashlib.sha256(data).hexdigest()!=a.expected_sha256:raise SystemExit('snapshot digest mismatch')
    value=decode(data);catalogue=Catalogue(value);output=encoded(value)
    fd=os.open(a.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(output)
    print(json.dumps(dict(records=len(catalogue.rows),sha256=hashlib.sha256(output).hexdigest(),
        completeness='not_verified',cloud_mutations=0)))

if __name__=='__main__':main()
