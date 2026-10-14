"""Offline MMH IAP proposal/readback checks. No cloud writes or secret input.

This does not authorize execution or replace inherited-IAM and live HTTP tests.
Existing revision deployment tools continue to reject IAP-enabled services.
"""
import copy
import hashlib
import json
import re

PROJECT = 'woundai-jackh001'
NUMBER = '421209514056'
SERVICE = 'woundai-backend-mmhps20261007'
RUN_NAME = f'projects/{PROJECT}/locations/asia-east1/services/{SERVICE}'
IAP_NAME = f'projects/{NUMBER}/iap_web/cloud_run-asia-east1/services/{SERVICE}'
UID = 'ed1bb40f-1c67-4e1a-844f-39a61146769c'
WEB = NUMBER + '-c62hg9vv5p7g2l96n2efig269kcsads6.apps.googleusercontent.com'
IOS = NUMBER + '-j72bu49ou8kh31s61na1v4t817lecsmq.apps.googleusercontent.com'
AGENT = f'serviceAccount:service-{NUMBER}@gcp-sa-iap.iam.gserviceaccount.com'
USER = 'user:jack.hou@gmail.com'
IMAGE = ('asia-east1-docker.pkg.dev/woundai-jackh001/woundai-medical-build/medical'
         '@sha256:72e86414797c03aac3eecc3011cc6ce9216c32aab6c88f276c406c4ce8ad5467')
TYPE = 'TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION'
TRAFFIC = [{'type': TYPE, 'revision': SERVICE + '-00001-hqd', 'percent': 100},
           {'type': TYPE, 'revision': SERVICE + '-c9a4b9e49f414', 'tag': 'candidate'}]
CANDIDATE_URL = f'https://candidate---{SERVICE}-z4kgfkob4a-de.a.run.app'
TRAFFIC_STATUS = [copy.deepcopy(TRAFFIC[0]), dict(TRAFFIC[1], uri=CANDIDATE_URL)]
RUN_BINDING = [{'role': 'roles/run.invoker', 'members': [AGENT]}]
IAP_BINDING = [{'role': 'roles/iap.httpsResourceAccessor', 'members': [USER]}]
# Preserve the entire revision template, plus every reviewed service control.
PROTECTED = ('template', 'traffic', 'scaling', 'ingress', 'customAudiences',
             'invokerIamDisabled', 'defaultUriDisabled', 'binaryAuthorization',
             'buildConfig', 'multiRegionSettings', 'labels')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def policy(value, bindings):
    require(isinstance(value, dict) and set(value) <= {'etag', 'version', 'bindings'}, 'unknown policy shape')
    require(isinstance(value.get('etag'), str) and value['etag'], 'policy etag required')
    require(type(value.get('version', 1)) is int and value.get('version', 1) in (1, 3), 'policy version invalid')
    # Exact lists reject conditions, duplicates, groups and extra principals/roles.
    actual = copy.deepcopy(value.get('bindings', []))
    # IAP canonicalizes this known Google account to user:Jack.Hou@gmail.com.
    # Normalize only that one email; never rewrite arbitrary principal syntax.
    for binding in actual:
        if isinstance(binding, dict) and isinstance(binding.get('members'), list):
            binding['members'] = [USER if isinstance(member, str) and member.startswith('user:')
                                  and member.casefold() == USER else member
                                  for member in binding['members']]
    require(actual == bindings, 'policy differs from reviewed principals')


def stable(service):
    return {k: copy.deepcopy(service.get(k)) for k in PROTECTED}


def canonical_settings(settings):
    """Console saves two explicit disabled controls; accept only exact booleans.

    Do not discard enabled controls, extra subfields, or future unknown settings.
    """
    result = copy.deepcopy(settings)
    for section, control in [('accessSettings', 'allowedDomainsSettings'),
                             ('applicationSettings', 'attributePropagationSettings')]:
        parent = result.get(section, {})
        value = parent.get(control)
        if isinstance(value, dict) and set(value) == {'enable'} and value['enable'] is False:
            del parent[control]
            if not parent:
                del result[section]
    return result


def ready(service):
    require(service.get('name') == RUN_NAME and service.get('uid') == UID, 'wrong service identity')
    require(isinstance(service.get('etag'), str) and service['etag'], 'service etag required')
    require(service.get('generation') and service['generation'] == service.get('observedGeneration')
            and not service.get('reconciling') and not service.get('deleteTime')
            and service.get('terminalCondition', {}).get('state') == 'CONDITION_SUCCEEDED', 'service not ready')
    require(service.get('invokerIamDisabled', False) is False, 'invoker bypass prohibited')
    require(service.get('trafficStatuses') == TRAFFIC_STATUS, 'observed traffic differs')


def propose(service, run_policy, settings, access_policy):
    ready(service)
    require(service.get('iapEnabled', False) is False, 'IAP already configured; re-review required')
    require(service.get('traffic') == TRAFFIC, 'traffic baseline changed')
    template = service.get('template', {})
    require(template.get('serviceAccount') == f'woundai-mmhps20261007-runtime@{PROJECT}.iam.gserviceaccount.com', 'runtime changed')
    containers = template.get('containers', [])
    require(len(containers) == 1 and containers[0].get('image') == IMAGE, 'image changed')
    require(settings == {'name': IAP_NAME}, 'existing IAP settings need review')
    policy(run_policy, []); policy(access_policy, [])
    spec = {
        'run_resource': RUN_NAME, 'iap_resource': IAP_NAME,
        'baseline_etag': service['etag'], 'baseline_generation': service['generation'],
        'protected': stable(service),
        'run_policy_before': copy.deepcopy(run_policy),
        'iap_policy_before': copy.deepcopy(access_policy),
        'settings_before': copy.deepcopy(settings),
        'run_binding_after': copy.deepcopy(RUN_BINDING),
        'iap_binding_after': copy.deepcopy(IAP_BINDING),
        'oauth_client_id': WEB, 'programmatic_clients': [IOS],
        'run_update_mask': 'iapEnabled', 'iap_enabled_after': True,
        'cloud_writes_authorized': False,
        'runtime_acceptance_required': True,
    }
    return {'schema': 'mmh.iap-entry-review/1', 'spec': spec, 'sha256': digest(spec)}


def check_after(proposal, service, run_policy, settings, access_policy, expected_secret_sha256):
    """Check direct-resource readback only; never claim effective access or E2E."""
    spec = proposal['spec']
    require(proposal.get('schema') == 'mmh.iap-entry-review/1' and proposal.get('sha256') == digest(spec), 'proposal integrity failed')
    require(spec.get('run_resource') == RUN_NAME and spec.get('iap_resource') == IAP_NAME
            and spec.get('run_binding_after') == RUN_BINDING and spec.get('iap_binding_after') == IAP_BINDING
            and spec.get('oauth_client_id') == WEB and spec.get('programmatic_clients') == [IOS]
            and spec.get('run_update_mask') == 'iapEnabled' and spec.get('iap_enabled_after') is True,
            'proposal target or scope changed')
    ready(service)
    require(service.get('iapEnabled') is True, 'IAP not enabled')
    require(stable(service) == spec['protected'], 'service changed beyond IAP flag')
    policy(run_policy, RUN_BINDING); policy(access_policy, IAP_BINDING)
    require(isinstance(expected_secret_sha256, str) and re.fullmatch(r'[0-9a-f]{64}', expected_secret_sha256),
            'expected secret digest required; never pass the secret')
    require(canonical_settings(settings) == {'name': IAP_NAME, 'accessSettings': {'oauthSettings': {
        'clientId': WEB, 'clientSecretSha256': expected_secret_sha256,
        'programmaticClients': [IOS]}}}, 'IAP settings differ or unknown controls present')
    return {'direct_resource_configuration_passed': True,
            'inherited_access_verified': False, 'live_authentication_verified': False}


def propose_native_probe(service, run_policy, settings, access_policy):
    """Separate, bounded custom-web-audience experiment, not a v1 success fallback.

    Google supports custom OAuth programmatic access, but this no-organization
    mobile path still needs real Google/IAP validation. No extra OAuth audience
    is accepted, no broker or service-account impersonation is introduced.
    """
    original = propose(service, run_policy, {'name': IAP_NAME}, access_policy)
    normalized = canonical_settings(settings)
    oauth = normalized.get('accessSettings', {}).get('oauthSettings', {})
    sha = oauth.get('clientSecretSha256')
    require(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{64}', sha), 'saved secret digest required')
    require(normalized == {'name': IAP_NAME, 'accessSettings': {'oauthSettings': {
        'clientId': WEB, 'clientSecretSha256': sha}}}, 'probe requires exact custom OAuth and no additional clients')
    spec = copy.deepcopy(original['spec'])
    spec.update(settings_before=copy.deepcopy(settings), programmatic_clients=[],
                authentication_mode='custom-web-audience-native-probe',
                expected_mobile_client=IOS, required_token_audience=WEB,
                probe_only=True)
    return {'schema': 'mmh.iap-native-probe/1', 'spec': spec, 'sha256': digest(spec)}


def check_native_probe(proposal, service, run_policy, settings, access_policy):
    spec = proposal['spec']
    require(proposal.get('schema') == 'mmh.iap-native-probe/1'
            and proposal.get('sha256') == digest(spec), 'probe integrity failed')
    require(spec.get('authentication_mode') == 'custom-web-audience-native-probe'
            and spec.get('probe_only') is True and spec.get('programmatic_clients') == []
            and spec.get('expected_mobile_client') == IOS and spec.get('required_token_audience') == WEB
            and spec.get('run_resource') == RUN_NAME and spec.get('iap_resource') == IAP_NAME
            and spec.get('run_binding_after') == RUN_BINDING and spec.get('iap_binding_after') == IAP_BINDING
            and spec.get('oauth_client_id') == WEB and spec.get('run_update_mask') == 'iapEnabled'
            and spec.get('iap_enabled_after') is True, 'probe scope changed')
    ready(service)
    require(service.get('iapEnabled') is True, 'IAP not enabled')
    require(stable(service) == spec['protected'], 'service changed beyond IAP flag')
    policy(run_policy, RUN_BINDING); policy(access_policy, IAP_BINDING)
    require(settings == spec['settings_before'], 'probe OAuth settings changed')
    # Revalidate the settings contract even if someone rehashed a modified plan.
    baseline = copy.deepcopy(service)
    baseline['iapEnabled'] = False
    propose_native_probe(baseline, {'etag':'check'}, settings, {'etag':'check'})
    return {'probe_configuration_passed': True, 'mobile_authentication_verified': False,
            'inherited_access_verified': False, 'upload_acceptance_passed': False}
