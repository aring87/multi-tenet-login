"""Same-tenant registration tests use fake Azure and GitHub responses only."""
import copy
import io
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from sentinel_app import lighthouse_onboarding as lh
from sentinel_app import same_tenant_onboarding as direct
from sentinel_app import desktop_backend as backend

ROOT = Path(__file__).resolve().parents[1]
APP1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
APP2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
WS = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'


def config():
    c = json.loads((ROOT / 'examples/lighthouse-onboarding.example.json').read_text(encoding='utf-8'))
    c.update(tenant_id=c['managing_tenant_id'], client='413', workspace_label='workspace-gcc',
             initial_rule_path='rules/sentinel/testing/pipeline-connection-test.yml')
    return c


class Fake:
    def __init__(self, c, apply=False):
        self.apply, self.c, self.calls = apply, c, []
        self.scope = '/subscriptions/' + c['subscription_id'] + '/resourceGroups/' + c['resource_group']
        self.workspace = dict(id=self.scope+'/providers/Microsoft.OperationalInsights/workspaces/'+c['workspace_name'], customerId=WS)
        self.roles = [dict(principalId=c[field], roleDefinitionId='/providers/Microsoft.Authorization/roleDefinitions/'+role,
                           scope=self.scope, condition=None) for field, _, role in direct.ROLES]
        self.member = True
        self.cloud = 'AzureCloud'
        self.tenant = c['tenant_id']
        self.app2 = APP2

    def az(self, *args, write=False):
        self.calls.append(('az', args, write))
        if write:
            raise AssertionError('No Azure writes')
        if args[:2] == ('cloud', 'show'): return {'name':self.cloud}
        if args[:2] == ('account', 'show'): return dict(tenantId=self.tenant,id=self.c['subscription_id'])
        if args[:4] == ('monitor','log-analytics','workspace','show'): return self.workspace
        if args[:3] == ('ad','group','show'): return dict(id=args[-1],securityEnabled=True)
        if args[:3] == ('ad','sp','show'): return dict(id=args[-1],appId=args[-1])
        if args[:4] == ('ad','group','member','check'): return {'value':self.member}
        if args[:3] == ('role','assignment','list'): return self.roles
        raise AssertionError(args)

    def gh(self, method, endpoint, body=None, missing=False):
        self.calls.append(('gh',method,endpoint))
        if method != 'GET': raise AssertionError('Unexpected GitHub write')
        if endpoint.endswith('/variables/AZURE_CLIENT_ID'):
            return {'value':APP1 if self.c['preview_environment'] in endpoint else self.app2}
        if '/environments/' in endpoint:
            return {'protection_rules':[{'type':'required_reviewers','reviewers':[{'id':1}]}]}
        if endpoint.endswith('/actions/permissions'): return {'enabled':True}
        if '/contents/' in endpoint and '?ref=main' not in endpoint: return {}
        if '/contents/' in endpoint or '/git/ref/' in endpoint: return None
        return dict(private=True,archived=False,permissions={'admin':True},default_branch='main')


class SameTenantTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.c = config()
        self.fake = Fake(self.c)
        self.snapshot = {'issues':[], 'clients':[]}
        patcher = patch.object(direct.GitHubReader, 'load', side_effect=lambda repo: self.snapshot)
        patcher.start(); self.addCleanup(patcher.stop)

    def run_object(self):
        return direct.SameTenantOnboard(self.c, self.fake, Path(self.folder.name)/'state.json',ROOT/'templates/lighthouse-onboard.json')

    def test_lighthouse_still_refuses_self_delegation(self):
        with self.assertRaises(lh.Stop): lh.Onboard(self.c,self.fake,Path(self.folder.name)/'state.json',ROOT/'templates/lighthouse-onboard.json')
        self.assertEqual(direct.validate_app_config(self.c)['target'],'413-workspace-gcc')

    def test_direct_path_refuses_different_tenant(self):
        self.c['tenant_id']=APP1
        with self.assertRaises(lh.Stop): self.run_object()
        self.assertEqual(self.fake.calls,[])

    def test_preview_has_no_writes_or_state_file(self):
        with contextlib.redirect_stdout(io.StringIO()): self.run_object().run()
        self.assertFalse(list(Path(self.folder.name).iterdir()))
        self.assertTrue(all(c[2] is False if c[0]=='az' else c[1]=='GET' for c in self.fake.calls))
        self.assertNotIn('deployment',str(self.fake.calls))
        self.assertNotIn('ManagedServices',str(self.fake.calls))

    def test_apply_only_reaches_repository_helper_after_checks(self):
        self.fake.apply=True
        run=self.run_object()
        with patch.object(run,'create_target_pr') as create, contextlib.redirect_stdout(io.StringIO()): run.run()
        create.assert_called_once_with()
        self.assertEqual(len(run.access_rows),5)

    def test_missing_roles_stops_before_repository_writes_with_group_and_scope(self):
        self.fake.apply=True; self.fake.roles=[]
        run=self.run_object()
        with patch.object(run,'create_target_pr') as create, self.assertRaisesRegex(lh.Stop,'Microsoft Sentinel Contributor') as err:
            run.run()
        self.assertIn(self.fake.scope,str(err.exception)); self.assertIn(self.c['deploy_group_object_id'],str(err.exception))
        create.assert_not_called()

    def test_parent_subscription_roles_are_accepted_and_reported(self):
        for row in self.fake.roles: row['scope']='/subscriptions/'+self.c['subscription_id']
        run=self.run_object();run.preflight()
        self.assertTrue(all('resourceGroups' not in row for row in run.access_rows))

    def test_sibling_scope_or_conditional_roles_are_not_counted(self):
        for key,value in [('scope',self.fake.scope+'-other'),('condition','conditional access')]:
            with self.subTest(key=key):
                saved=copy.deepcopy(self.fake.roles)
                self.fake.roles[0][key]=value
                with self.assertRaisesRegex(lh.Stop,'Cannot confirm'): self.run_object().preflight()
                self.fake.roles=saved

    def test_missing_pipeline_group_membership_stops_registration(self):
        self.fake.member=False
        with self.assertRaisesRegex(lh.Stop,'not a member'): self.run_object().preflight()

    def test_shared_preview_and_production_app_is_rejected(self):
        self.fake.app2=APP1
        with self.assertRaisesRegex(lh.Stop,'different pipeline applications'): self.run_object().preflight()

    def test_wrong_session_or_government_cloud_stops_before_github(self):
        for attr,value in [('tenant',APP1),('cloud','AzureUSGovernment')]:
            with self.subTest(attr=attr):
                saved=getattr(self.fake,attr); setattr(self.fake,attr,value);self.fake.calls=[]
                with self.assertRaises(lh.Stop): self.run_object().preflight()
                self.assertFalse(any(c[0]=='gh' for c in self.fake.calls))
                setattr(self.fake,attr,saved)

    def test_existing_workspace_under_other_target_stops_duplicate(self):
        self.snapshot['clients']=[dict(path='clients/existing/old.yml',target='existing-old',
                                      raw={'azure':dict(workspace_id=WS)})]
        with self.assertRaisesRegex(lh.Stop,'clients/existing/old.yml'): self.run_object().preflight()

    def test_duplicate_destination_is_detected_even_if_guid_differs(self):
        self.snapshot['clients']=[dict(path='clients/existing/old.yml',target='existing-old',raw={'azure':dict(self.c,workspace_id=APP1)})]
        with self.assertRaisesRegex(lh.Stop,'already registered'): self.run_object().preflight()

    def test_invalid_catalog_stops_registration(self):
        self.snapshot['issues']=['Malformed target']
        with self.assertRaisesRegex(lh.Stop,'Malformed target'): self.run_object().preflight()

    def test_numeric_manifest_and_pr_description(self):
        run=self.run_object();run.preflight()
        self.assertIn('client: "413"',run.manifest())
        self.assertIn('enabled: false',run.manifest())
        self.assertIn('clients/413/413-workspace-gcc.yml',run.target_path)
        self.assertIn('No Lighthouse delegation',run.access_description())

    def test_azure_guard_rejects_accidental_inherited_write(self):
        with self.assertRaisesRegex(lh.Stop,'cannot change Azure'):
            self.run_object().io.az('provider','register',write=True)
        self.assertEqual(self.fake.calls,[])

    def test_ui_review_distinguishes_both_paths(self):
        title,action=direct.review_text(self.c)
        self.assertIn('Same-tenant',title);self.assertIn('No Azure resources',action)
        changed=dict(self.c,tenant_id=APP1)
        title,action=direct.review_text(changed)
        self.assertIn('Lighthouse',title);self.assertIn('delegates',action)

    def test_backend_routes_same_tenant_with_existing_session(self):
        session=MagicMock();session.az_exe='az';session.gh_exe='gh'
        with patch.object(backend,'DATA',Path(self.folder.name)),patch.object(backend,'SameTenantOnboard') as same,patch.object(backend,'Onboard') as delegated:
            backend.full_run(session,self.c,False)
        same.return_value.run.assert_called_once_with();delegated.assert_not_called()
        session.verify.assert_called_once_with(self.c)

    def test_actual_pr_contains_direct_mode_and_both_dropdown_updates(self):
        import test_lighthouse_dropdown as fixtures
        github = fixtures.FullPullRequestTests('test_pr_carries_target_and_dropdowns_together').github()
        self.fake.apply = True
        run = self.run_object(); run.preflight()
        self.fake.gh, self.fake.pages = github.gh, github.pages
        with contextlib.redirect_stdout(io.StringIO()): run.create_target_pr()
        self.assertIn('No Lighthouse delegation', github.pr_body)
        self.assertNotIn('Access is via an Azure Lighthouse', github.pr_body)
        puts = [c[1] for c in github.calls if c[0] == 'PUT']
        self.assertTrue(any(p.endswith('clients/413/413-workspace-gcc.yml') for p in puts))
        self.assertEqual(sum(p.endswith(tuple(lh.DROPDOWN_WORKFLOWS)) for p in puts), 2)

    def test_ui_requires_review_and_cancel_never_applies(self):
        from sentinel_app import desktop_app as ui
        app = SimpleNamespace(payload=lambda:self.c, plan=None, summary=MagicMock(), root=MagicMock(),
                              tabs=MagicMock(), work=MagicMock())
        with patch.object(ui.messagebox, 'showerror') as error:
            ui.App.onboard(app, True)
        self.assertIn('Preview this exact configuration', error.call_args.args[1])
        app.work.assert_not_called()
        import time
        app.plan=(backend.fingerprint(self.c,'same-tenant',[]),time.time())
        with patch.object(ui.messagebox,'askokcancel',return_value=False) as confirm:
            ui.App.onboard(app,True)
        self.assertIn('No Azure resources',confirm.call_args.args[1])
        app.work.assert_not_called()


if __name__ == '__main__': unittest.main()
