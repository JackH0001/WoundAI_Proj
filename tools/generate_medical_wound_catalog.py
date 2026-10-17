"""Generate identical offline catalogs for medical iOS/Android/backend. No clinical inference."""
import argparse,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def outputs():
    data=json.loads((ROOT/'engineering/contracts/medical_wound_catalog.json').read_text())
    raw=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    fixtures=hashlib.sha256((ROOT/'engineering/contracts/medical_wound_profile_cases.json').read_bytes()).hexdigest()
    return {
      'iOS/WoundMeasurementApp/Core/MedicalWoundCatalog.generated.swift':'// Generated; fixtures SHA-256: '+fixtures+'\n// Edit engineering/contracts/medical_wound_catalog.json.\nimport Foundation\nenum MedicalWoundCatalogData { static let json = #"""\n'+raw+'\n"""# }\n',
      'Android/app/src/main/java/com/woundmeasurement/app/data/MedicalWoundCatalogData.kt':'// Generated; fixtures SHA-256: '+fixtures+'\n// Edit engineering/contracts/medical_wound_catalog.json.\npackage com.woundmeasurement.app.data\ninternal object MedicalWoundCatalogData { val json = """'+raw+'""" }\n',
      'Backend/Flask/medical_wound_catalog.py':'# Generated; fixtures SHA-256: '+fixtures+'\n# Edit engineering/contracts/medical_wound_catalog.json.\nimport json\nCATALOG = json.loads(r\'\'\''+raw+'\'\'\')\n',
    }
def main():
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
    for name,body in outputs().items():
        path=ROOT/name
        if args.check:
            if not path.exists() or path.read_text()!=body:raise SystemExit('Catalog drift: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
    print('medical catalog: 3 outputs match' if args.check else 'medical catalog generated')
if __name__=='__main__':main()
