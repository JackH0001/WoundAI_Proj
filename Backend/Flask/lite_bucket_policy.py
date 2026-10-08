"""Pure validation of a fresh Cloud Storage JSON API bucket readback.

A dedicated mutable bucket is necessary for assertion counters and withdrawal
fences. Lifecycle deletion of these rows could reopen a withdrawn identity.
Media cleanup also must not silently create retained historical/soft-deleted
copies. These checks are point-in-time evidence, not an IAM/policy-change lock.
"""
import re


def validate_bucket_policy(metadata, *, name, project_number, role, location=None):
    if role not in ('security', 'media'):
        raise ValueError('unknown Lite bucket role')
    if type(project_number) is not str or re.fullmatch(r'[1-9][0-9]{5,19}', project_number) is None:
        raise ValueError('numeric project number required')
    if type(name) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', name) is None:
        raise ValueError('invalid expected bucket name')
    if type(metadata) is not dict or metadata.get('kind') != 'storage#bucket':
        raise ValueError('raw Cloud Storage bucket metadata required')
    if metadata.get('name') != name or metadata.get('projectNumber') != project_number:
        raise ValueError('bucket identity/project mismatch')
    if location is not None and metadata.get('location') != location:
        raise ValueError('bucket location mismatch')
    iam = metadata.get('iamConfiguration')
    if (type(iam) is not dict or iam.get('publicAccessPrevention') != 'enforced' or
            type(iam.get('uniformBucketLevelAccess')) is not dict or
            iam['uniformBucketLevelAccess'].get('enabled') is not True):
        raise ValueError('explicit public-access prevention and uniform IAM required')
    if 'retentionPolicy' in metadata or metadata.get('defaultEventBasedHold', False) is not False:
        raise ValueError('Lite bucket must permit replacement/deletion without retention or default holds')
    versioning = metadata.get('versioning', {})
    if (type(versioning) is not dict or set(versioning) - {'enabled'} or
            versioning.get('enabled', False) is not False):
        raise ValueError('Lite bucket object versioning must be disabled')
    soft = metadata.get('softDeletePolicy')
    if type(soft) is not dict or soft.get('retentionDurationSeconds') != '0':
        raise ValueError('explicit zero soft-delete duration readback required')
    lifecycle = metadata.get('lifecycle', {})
    if (type(lifecycle) is not dict or set(lifecycle) - {'rule'} or lifecycle.get('rule', []) != []):
        raise ValueError('candidate bucket must not expire security state or research data automatically')
    generation = metadata.get('metageneration')
    if type(generation) is not str or re.fullmatch(r'[1-9][0-9]*', generation) is None:
        raise ValueError('bucket policy metageneration required')
    return dict(bucket=name, project_number=project_number, role=role,
                metageneration=generation, live_object_deletion_policy='unretained',
                scope='current bucket policy; not IAM, old versions, backups or external exports')
