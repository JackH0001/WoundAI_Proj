"""Console availability follows deployed routes, independently of JWT roles."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
from flask import Flask
from api_console import console_bp, _page_html
from lite_service_profile import install_lite_perimeter


class ConsoleCapabilitiesTests(unittest.TestCase):
    def app(self, lite=False, perimeter=False):
        app=Flask(__name__)
        if perimeter: install_lite_perimeter(app)
        app.register_blueprint(console_bp)
        if lite:
            from api_lite import lite_bp
            app.register_blueprint(lite_bp)
        return app

    def exercise(self, app, permissions, tab='lite'):
        node=shutil.which('node')
        if not node: self.skipTest('Node is required to exercise rendered navigation')
        html=app.test_client().get('/console').get_data(as_text=True)
        navigation=html.split('const TABS =',1)[1].split('async function login()',1)[0]
        js='''
let tok='test', perms=PERMISSIONS;
const hits=[];
const names=['dash','recs','sys','audit','users','lite'];
const nav=names.map(name=>({dataset:{tab:name},hidden:true,classList:{toggle(k,v){if(k==='hide') nav.find(n=>n.dataset.tab===name).hidden=v;}}}));
const $=id=>({classList:{add(){}}});
const document={querySelectorAll:q=>q==='nav a'?nav:[]};
const location={hash:'#'+TAB};const history={replaceState(){}};
const loadDash=()=>hits.push('dash'),loadRecs=()=>hits.push('recs'),loadSys=()=>hits.push('sys'),auditGo=()=>hits.push('audit'),loadUsers=()=>hits.push('users');
async function loadLite(){hits.push('lite');}
''' .replace('PERMISSIONS',json.dumps(permissions)).replace('TAB',json.dumps(tab))
        js+='const TABS ='+navigation
        js+='applyPerms();go('+json.dumps(tab)+');console.log(JSON.stringify({cur,hits,liteHidden:nav.find(n=>n.dataset.tab===\"lite\").hidden}));'
        result=subprocess.run([node,'-e',js],capture_output=True,text=True,check=True)
        return json.loads(result.stdout)

    def test_disabled_service_admin_and_old_bookmark_never_load_lite(self):
        app=self.app()
        self.assertEqual(app.test_client().get('/api/v1/lite/records').status_code,404)
        r=self.exercise(app,['audit.read','flywheel.stats','user.manage'])
        self.assertTrue(r['liteHidden']);self.assertEqual(r['cur'],'dash')
        self.assertNotIn('lite',r['hits'])

    def test_registered_feature_still_requires_role(self):
        app=self.app(lite=True)
        r=self.exercise(app,['flywheel.stats'])
        self.assertTrue(r['liteHidden']);self.assertNotIn('lite',r['hits'])
        r=self.exercise(app,['audit.read'])
        self.assertFalse(r['liteHidden']);self.assertEqual(r['cur'],'lite')
        self.assertIn('lite',r['hits'])

    def test_no_context_defaults_closed_and_routes_are_per_app(self):
        self.assertIn('const CONSOLE_CAPABILITIES = {lite:false};',_page_html())
        enabled=self.app(lite=True);disabled=self.app()
        self.assertIn('{lite:true}',enabled.test_client().get('/console').get_data(as_text=True))
        self.assertIn('{lite:false}',disabled.test_client().get('/console').get_data(as_text=True))
        r=self.exercise(disabled,['audit.read'],tab='unknown-bookmark')
        self.assertEqual(r['cur'],'sys');self.assertNotIn('lite',r['hits'])

    def test_public_lite_perimeter_still_has_no_admin_console(self):
        app=self.app(lite=True,perimeter=True)
        for path in ('/console','/api/v1/lite/records'):
            with self.subTest(path=path):
                r=app.test_client().get(path)
                self.assertEqual(r.status_code,404)
                self.assertEqual(r.json['error'],'lite_route_not_supported')


if __name__=='__main__':unittest.main()
