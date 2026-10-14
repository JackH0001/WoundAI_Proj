"""Fail closed on XCTest failures, missing evidence or unexpected simulator skips."""
import argparse
import json

PROTECTION_TEST = 'HealthDataBackupTests/testDeviceFileProtectionClassSurvivesAtomicReplacement()'
PROTECTION_REASON = 'Test skipped - This simulator returns no protectionKey; verify the class on an iOS device.'

FOLDER_TEST = 'InstitutionPrivateExportsTests/testPersistedSystemFolderDeliversAfterProcessRestart()'
FOLDER_REASON = 'Test skipped - Requires a system-picker grant from testSelectLocalFolderForRestartValidation'

def verify(summary, tree, allow_simulator_protection_skip=False, allow_system_folder_grant_skip=False):
    def count(key):
        value = summary.get(key)
        if type(value) is not int or value < 0:
            raise ValueError('Missing or invalid test count: ' + key)
        return value
    passed, failed, skipped, total = [count(k) for k in ('passedTests', 'failedTests', 'skippedTests', 'totalTestCount')]
    if passed < 1 or failed or summary.get('expectedFailures') != 0 or summary.get('result') != 'Passed':
        raise ValueError('Expected actual passing tests with no failures')
    cases = []
    def walk(node):
        if isinstance(node, dict):
            if node.get('nodeType') == 'Test Case':
                cases.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(tree)
    if len(cases) != total or passed + skipped != total:
        raise ValueError('Summary and test tree counts disagree')
    if sum(c.get('result') == 'Passed' for c in cases) != passed:
        raise ValueError('Unexpected case outcome')
    omitted = [c for c in cases if c.get('result') == 'Skipped']
    if len(omitted) != skipped:
        raise ValueError('Skipped case evidence missing')
    pending = set()
    if skipped:
        devices = summary.get('devicesAndConfigurations', [])
        allowed = {}
        if allow_simulator_protection_skip:
            allowed[PROTECTION_TEST] = PROTECTION_REASON
        if allow_system_folder_grant_skip:
            allowed[FOLDER_TEST] = FOLDER_REASON
        if skipped > len(allowed) or not devices:
            raise ValueError('Unexpected skipped test')
        if any(d.get('device', {}).get('platform') != 'iOS Simulator' for d in devices):
            raise ValueError('Device acceptance cannot be waived on a physical device')
        for case in omitted:
            identity = case.get('nodeIdentifier')
            reasons = [c.get('name') for c in case.get('children', [])
                       if c.get('nodeType') in ('Skip Message', 'Failure Message')]
            if identity not in allowed or identity in pending or reasons != [allowed[identity]]:
                raise ValueError('Skip identity or reason is not the documented simulator limitation')
            pending.add(identity)
    return {'passed': passed, 'failed': failed, 'skipped': skipped,
            'physical_file_protection_validation_pending': PROTECTION_TEST in pending,
            'system_folder_acceptance_pending': FOLDER_TEST in pending}



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('summary')
    parser.add_argument('tree')
    parser.add_argument('--allow-simulator-protection-skip', action='store_true')
    parser.add_argument('--allow-system-folder-grant-skip', action='store_true')
    args = parser.parse_args()
    with open(args.summary) as source:
        summary = json.load(source)
    with open(args.tree) as source:
        tree = json.load(source)
    print(json.dumps(verify(summary, tree, args.allow_simulator_protection_skip, args.allow_system_folder_grant_skip)))
