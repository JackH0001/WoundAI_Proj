"""Versioned medical profile codes; no diagnosis, free text or automatic score claims."""
from medical_wound_catalog import CATALOG

KEYS={'schema_version','site','side','surface','digit','etiology','factors'}
FOOT={'heel','forefoot','midfoot','hindfoot','toe','interdigital_foot','toe_tip'}
def validate_profile(value):
    if type(value) is not dict or set(value)!=KEYS:return False
    if any(type(value[k]) is not str for k in KEYS-{'factors'}):return False
    if value['schema_version']!=CATALOG['version']:return False
    site=next((s for s in CATALOG['sites'] if s['code']==value['site']),None)
    if not site or value['side'] not in site['lateralities'] or value['surface'] not in site['surfaces'] or value['digit'] not in site['digits']:return False
    if value['etiology'] not in {e['code'] for e in CATALOG['etiologies']}:return False
    factors=value['factors']
    if type(factors) is not list or any(type(f) is not str for f in factors):return False
    if len(set(factors))!=len(factors) or not set(factors)<={f['code'] for f in CATALOG['factors']}:return False
    return value['etiology']!='diabetic_foot' or value['site'] in FOOT

def assessment_requirements(profile):
    if not validate_profile(profile):raise ValueError('wound_profile invalid')
    ids=next(e['instruments'] for e in CATALOG['etiologies'] if e['code']==profile['etiology'])
    return [next(i for i in CATALOG['instruments'] if i['code']==key) for key in ids]
