"""Pure policy transforms for the authorized old-runtime Editor migration.

No CLI, cloud calls, stored credentials or role changes for other members.
Callers must read fresh IAM and use its etag for both removal and rollback.
"""
import copy
from verify_mmh_effective_iam import OLD_RUNTIME

MEMBER = 'serviceAccount:' + OLD_RUNTIME
ROLE = 'roles/editor'


def transform(policy, *, remove):
    if not isinstance(policy, dict) or not isinstance(policy.get('etag'), str) or not policy['etag']:
        raise ValueError('fresh policy etag required')
    bindings = policy.get('bindings')
    if not isinstance(bindings, list):
        raise ValueError('complete bindings required')
    matching = []
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict) or not isinstance(binding.get('members'), list):
            raise ValueError('malformed IAM binding')
        if binding.get('role') != ROLE:
            continue
        if MEMBER in binding['members'] and binding.get('condition'):
            raise ValueError('conditional Editor grant requires separate review')
        if not binding.get('condition'):
            if set(binding) != {'role', 'members'} or len(set(binding['members'])) != len(binding['members']):
                raise ValueError('unexpected unconditional Editor binding')
            matching.append(index)
    if len(matching) > 1:
        raise ValueError('ambiguous unconditional Editor bindings')
    result = copy.deepcopy(policy)
    if remove:
        if not matching or MEMBER not in bindings[matching[0]]['members']:
            raise ValueError('expected old Editor member absent; no write')
        index = matching[0]
        result['bindings'][index]['members'].remove(MEMBER)
        if not result['bindings'][index]['members']:
            del result['bindings'][index]
    elif matching:
        members = result['bindings'][matching[0]]['members']
        if MEMBER not in members:
            members.append(MEMBER)
    else:
        result['bindings'].append({'role': ROLE, 'members': [MEMBER]})
    return result


def semantic(policy):
    """Compare IAM readback without relying on list order or returned etag."""
    import json
    value = copy.deepcopy(policy)
    value.pop('etag', None)
    for binding in value.get('bindings', []):
        binding['members'] = sorted(binding['members'])
    value['bindings'] = sorted(value.get('bindings', []), key=lambda row: json.dumps(row, sort_keys=True))
    return value
