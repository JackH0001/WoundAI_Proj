"""Render the real system-status functions with synthetic health receipts in Node."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]

class AuditStatusUITest(unittest.TestCase):
    def test_rendered_retention_states(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for actual JavaScript rendering checks')
        tree = ast.parse((ROOT / 'Backend/Flask/api_console.py').read_text())
        page = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, 'id', '') == '_PAGE' for t in n.targets))
        start = page.index('function auditProtectionRow(')
        end = page.index('// 部署身分。', start)
        code = page[start:end]
        cases = [
            ('mmh-not-worm', {'verified': True, 'locked': False, 'retention_seconds': 0}, 'mmh-unlocked-validation', 'MMH 驗證模式・未鎖定', False),
            ('unlocked-with-period', {'verified': True, 'locked': False, 'retention_seconds': 220903200}, 'locked-seven-year', '未鎖定', False),
            ('locked', {'verified': True, 'locked': True, 'retention_seconds': 220903200}, 'locked-seven-year', '已鎖定保留政策', True),
            ('actual-lock-despite-mode', {'verified': True, 'locked': True, 'retention_seconds': 100}, 'mmh-unlocked-validation', '已鎖定保留政策', True),
            ('unverified', {'verified': False, 'locked': True, 'retention_seconds': 100}, 'locked-seven-year', '尚未確認', False),
            ('missing', {}, 'locked-seven-year', '尚未確認', False),
            ('string-verified', {'verified': 'true', 'locked': True, 'retention_seconds': 100}, 'locked-seven-year', '尚未確認', False),
            ('string-locked', {'verified': True, 'locked': 'false', 'retention_seconds': 100}, 'locked-seven-year', '尚未確認', False),
            ('string-period', {'verified': True, 'locked': True, 'retention_seconds': '100'}, 'locked-seven-year', '尚未確認', False),
            ('negative', {'verified': True, 'locked': True, 'retention_seconds': -1}, 'locked-seven-year', '尚未確認', False),
            ('zero-locked', {'verified': True, 'locked': True, 'retention_seconds': 0}, 'locked-seven-year', '尚未確認', False),
            ('fractional', {'verified': True, 'locked': True, 'retention_seconds': 1.1}, 'locked-seven-year', '尚未確認', False),
            ('unsafe-number', {'verified': True, 'locked': True, 'retention_seconds': 10**20}, 'locked-seven-year', '尚未確認', False),
        ]
        harness = '''const assert = require('node:assert/strict');
const target={innerHTML:''}; const $=()=>target;
const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const buildBox=()=>''; let health;
const fetch=async()=>({json:async()=>health});
'''+code+'''
(async()=>{
for(const [name,receipt,mode,label,locked] of CASES){
 health={status:'healthy',store:'gcs://synthetic [mutable audit, not WORM]',audit_retention:receipt,audit_write_mode:mode};
 await loadSys();
 const row=target.innerHTML.match(/<tr><td>稽核保留保護<\\/td>[\\s\\S]*?<\\/tr>/)[0];
 assert.ok(row.includes(label),name);assert.equal(row.includes('class="ok"'),locked,name);
 assert.ok(!row.includes('✓'),name);assert.ok(!target.innerHTML.includes('稽核 WORM 桶'),name);
 if(name==='mmh-not-worm')assert.ok(row.includes('不屬於 WORM'),name);
 if(name==='unlocked-with-period')assert.ok(row.includes('政策可變更'),name);
}
console.log('13 rendered health-state cases passed');
})().catch(e=>{console.error(e);process.exit(1)});
'''.replace('CASES', json.dumps(cases, ensure_ascii=False))
        result = subprocess.run([node, '-e', harness], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        print(result.stdout.strip())

if __name__ == '__main__':
    unittest.main()
