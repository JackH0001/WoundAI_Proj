"""Fail closed on XCTest failures, missing evidence or unexpected simulator skips."""
import argparse
import json

PROTECTION_TEST = 'HealthDataBackupTests/testDeviceFileProtectionClassSurvivesAtomicReplacement()'
PROTECTION_REASON = 'Test skipped - This simulator returns no protectionKey; verify the class on an iOS device.'


def verify(summary, tree, allow_simulator_protection_skip=False):
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
    if skipped:
        devices = summary.get('devicesAndConfigurations', [])
        if not allow_simulator_protection_skip or skipped != 1 or not devices:
            raise ValueError('Unexpected skipped test')
        if any(d.get('device', {}).get('platform') != 'iOS Simulator' for d in devices):
            raise ValueError('File protection must be checked on a physical device')
        case = omitted[0]
        # xcresulttool may render XCTest's skip explanation as a Failure Message.
        # The case must still be Skipped and the exact explanation must match.
        reasons = [c.get('name') for c in case.get('children', [])
                   if c.get('nodeType') in ('Skip Message', 'Failure Message')]
        if case.get('nodeIdentifier') != PROTECTION_TEST or reasons != [PROTECTION_REASON]:
            raise ValueError('Skip identity or reason is not the documented simulator limitation')
    return {'passed': passed, 'failed': failed, 'skipped': skipped,
            'physical_file_protection_validation_pending': bool(skipped)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('summary')
    parser.add_argument('tree')
    parser.add_argument('--allow-simulator-protection-skip', action='store_true')
    args = parser.parse_args()
    with open(args.summary) as source:
        summary = json.load(source)
    with open(args.tree) as source:
        tree = json.load(source)
    print(json.dumps(verify(summary, tree, args.allow_simulator_protection_skip)))
