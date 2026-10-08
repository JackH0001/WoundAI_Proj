import copy
import unittest
import migrate_mmh_editor as m


def policy():
    return {'version':3,'etag':'fresh','bindings':[
        {'role':'roles/owner','members':['user:owner@example.test']},
        {'role':'roles/editor','members':['user:peer@example.test',m.MEMBER]},
        {'role':'roles/viewer','members':['group:team@example.test'],'condition':{'title':'test','expression':'false'}}],
        'auditConfigs':[{'service':'allServices','auditLogConfigs':[{'logType':'ADMIN_READ'}]}]}


class MigrationTests(unittest.TestCase):
    def test_removes_only_exact_member_preserving_everything_else(self):
        p=policy();original=copy.deepcopy(p);actual=m.transform(p,remove=True)
        expected=copy.deepcopy(p);expected['bindings'][1]['members'].remove(m.MEMBER)
        self.assertEqual(actual,expected);self.assertEqual(p,original)

    def test_single_member_binding_removed(self):
        p=policy();p['bindings'][1]['members']=[m.MEMBER]
        result=m.transform(p,remove=True)
        self.assertEqual(result['bindings'],[p['bindings'][0],p['bindings'][2]])
        self.assertEqual(result['etag'],'fresh')

    def test_missing_etag_or_bindings_rejected(self):
        for field in ['etag','bindings']:
            p=policy();p.pop(field)
            with self.assertRaises(ValueError):m.transform(p,remove=True)

    def test_missing_member_never_invents_success(self):
        p=m.transform(policy(),remove=True)
        with self.assertRaises(ValueError):m.transform(p,remove=True)

    def test_conditional_editor_and_duplicate_binding_refused(self):
        p=policy();p['bindings'][1]['condition']={'expression':'false'}
        with self.assertRaises(ValueError):m.transform(p,remove=True)
        p=policy();p['bindings'].append(copy.deepcopy(p['bindings'][1]))
        with self.assertRaises(ValueError):m.transform(p,remove=True)

    def test_rollback_uses_new_policy_etag_and_keeps_concurrent_edits(self):
        p=m.transform(policy(),remove=True);p['etag']='new-etag'
        p['bindings'].append({'role':'roles/logging.viewer','members':['user:new@example.test']})
        actual=m.transform(p,remove=False)
        self.assertEqual(actual['etag'],'new-etag')
        self.assertEqual(actual['bindings'][-1],p['bindings'][-1])
        self.assertEqual(actual['bindings'][1]['members'],['user:peer@example.test',m.MEMBER])

    def test_rollback_recreates_only_deleted_single_member_binding(self):
        p=policy();p['bindings'][1]['members']=[m.MEMBER]
        stripped=m.transform(p,remove=True);restored=m.transform(stripped,remove=False)
        self.assertEqual(m.semantic(restored),m.semantic(p))

    def test_rollback_already_restored_is_idempotent(self):
        self.assertEqual(m.transform(policy(),remove=False),policy())

    def test_readback_comparison_preserves_conditions_and_audit(self):
        p=policy();other=copy.deepcopy(p);other['etag']='next';other['bindings'].reverse()
        self.assertEqual(m.semantic(p),m.semantic(other))
        other['auditConfigs']=[]
        self.assertNotEqual(m.semantic(p),m.semantic(other))


if __name__=='__main__':unittest.main(verbosity=2)
