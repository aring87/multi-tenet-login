"""Offline desktop tests: no Azure/GitHub network calls."""
import copy
import json
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import desktop_backend as backend
import desktop_app as ui
from full_onboarding import Stop

TENANT="11111111-1111-4111-8111-111111111111"
SUB="22222222-2222-4222-8222-222222222222"
WS="33333333-3333-4333-8333-333333333333"
P1="44444444-4444-4444-8444-444444444444"
P2="55555555-5555-4555-8555-555555555555"
ITEM={"id":f"/subscriptions/{SUB}/resourceGroups/rg-example-sentinel/providers/Microsoft.OperationalInsights/workspaces/law-example-sentinel","customerId":WS}
WORKSPACE=backend.workspace_record(ITEM,SUB,TENANT)

class FakeSession:
    def __init__(self):
        self.calls=[]
        self.fail_validate=False
    def verify(self,c):
        if c["tenant_id"]!=TENANT:raise Stop("Wrong tenant")
        return {}
    def log(self,text): pass
    def az(self,*args):
        self.calls.append(args)
        if args[:3]==("ad","sp","show"):return {"id":args[-1]}
        if args[:4]==("monitor","log-analytics","workspace","show"):return ITEM
        if args[:3]==("deployment","group","validate") and self.fail_validate:raise Stop("Validation failed")
        return {"properties":{"provisioningState":"Succeeded"}}

class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)
        for module in (backend,ui):
            p=patch.object(module,"DATA",self.data)
            p.start();self.addCleanup(p.stop)
    def make_app(self,root):
        app=ui.App(root)
        app.clients=[{"name":"Example client","slug":"example-client","tenant":TENANT}]
        app.refresh_clients()
        return app

    def test_public_app_starts_with_empty_inventory(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=ui.App(root)
        self.assertEqual(app.clients,[])
        self.assertEqual(app.vars["github_owner"].get(),"")
        self.assertEqual(app.vars["github_repo"].get(),"")

    def test_workspace_discovery_extracts_all_ids(self):
        self.assertEqual(WORKSPACE["resource_group"],"rg-example-sentinel")
        self.assertEqual(WORKSPACE["workspace_id"],WS)
        self.assertEqual(WORKSPACE["tenant_id"],TENANT)
    def test_cross_subscription_workspace_rejected(self):
        with self.assertRaises(Stop):backend.workspace_record(ITEM,TENANT,TENANT)
    def test_malformed_workspace_rejected(self):
        with self.assertRaises(Stop):backend.workspace_record({"id":"/other","customerId":WS},SUB,TENANT)
    def test_session_uses_own_cli_cache(self):
        with patch("shutil.which",return_value="fake"):
            a=backend.Session(self.data/"one"); b=backend.Session(self.data/"two")
        self.assertNotEqual(a.env["AZURE_CONFIG_DIR"],b.env["AZURE_CONFIG_DIR"])
    def test_permission_plan_never_creates(self):
        session=FakeSession()
        backend.permission_run(session,WORKSPACE,"acme-primary",[P1,P2],False)
        self.assertFalse(any(c[:3]==("deployment","group","create") for c in session.calls))
    def test_permission_apply_validates_before_create(self):
        session=FakeSession()
        backend.permission_run(session,WORKSPACE,"acme-primary",[P1,P2],True)
        operations=[c[2] for c in session.calls if c[:2]==("deployment","group")]
        self.assertEqual(operations,["validate","what-if","create"])
    def test_validation_failure_stops_apply(self):
        session=FakeSession();session.fail_validate=True
        with self.assertRaises(Stop):backend.permission_run(session,WORKSPACE,"acme-primary",[P1,P2],True)
        self.assertFalse(any(c[:3]==("deployment","group","create") for c in session.calls))
    def test_identical_principals_stop_before_cloud(self):
        session=FakeSession()
        with self.assertRaises(Stop):backend.permission_run(session,WORKSPACE,"acme-primary",[P1,P1],True)
        self.assertEqual(session.calls,[])
    def test_plan_fingerprint_covers_destination_and_mode(self):
        a=backend.fingerprint(WORKSPACE,"Permissions only",[P1,P2])
        b=backend.fingerprint(dict(WORKSPACE,tenant_id=SUB),"Permissions only",[P1,P2])
        c=backend.fingerprint(WORKSPACE,"Full Azure + GitHub onboarding",[P1,P2])
        self.assertEqual(len({a,b,c}),3)
    def test_full_cli_write_guard(self):
        class Fake:
            az_exe="az";gh_exe="gh"
            def log(self,m):pass
            def execute(self,*a):raise AssertionError("Must not launch")
        cli=backend.DesktopCLI(Fake(),False)
        with self.assertRaises(Stop):cli.run("gh",["api"],write=True)
    def test_timeout_message_does_not_claim_rollback(self):
        with patch("shutil.which",return_value="fake"):
            session=backend.Session(self.data/"session")
        with patch("subprocess.run",side_effect=subprocess.TimeoutExpired("fake",1)):
            with self.assertRaisesRegex(Stop,"may still be running"):session.execute("fake",[])
    def test_delete_client_cancel_preserves_inventory(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root)
        before=copy.deepcopy(app.clients)
        with patch.object(ui.messagebox,"askyesno",return_value=False):
            app.delete_client()
        self.assertEqual(app.clients,before)
        self.assertFalse(app.clientfile.exists())

    def test_delete_last_client_persists_empty_list_and_clears_plan(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root)
        app.plan=("old",123)
        app.workspace=dict(WORKSPACE)
        retained=self.data/"runs"/"example-client-primary"
        retained.mkdir(parents=True)
        state=retained/"onboarding.state.json"
        state.write_text('{"keep":true}')
        app.session=FakeSession()
        with patch.object(ui.messagebox,"askyesno",return_value=True):
            app.delete_client()
        self.assertEqual(json.loads(app.clientfile.read_text()),[])
        self.assertEqual(app.clients,[])
        self.assertEqual(app.vars["client_name"].get(),"")
        self.assertIsNone(app.plan)
        self.assertIsNone(app.workspace)
        self.assertEqual(app.session.calls,[])
        self.assertTrue(state.exists())
        self.assertEqual(app.read_clients(),[])

    def test_delete_client_selects_remaining_client(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root)
        app.clients.append({"name":"Second client","slug":"second-client","tenant":TENANT})
        with patch.object(ui.messagebox,"askyesno",return_value=True):
            app.delete_client()
        self.assertEqual(app.vars["client_name"].get(),"Second client")
        self.assertEqual(app.vars["tenant"].get(),TENANT)

    def test_delete_blocked_during_running_operation(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root);app.busy=True
        with patch.object(ui.messagebox,"askyesno") as prompt:
            app.delete_client();prompt.assert_not_called()
        self.assertEqual(len(app.clients),1)

    def test_setup_mode_shows_relevant_panel(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root)
        self.assertTrue(app.permissioncard.winfo_manager())
        self.assertFalse(app.fullcard.winfo_manager())
        app.vars["mode"].set("Full Azure + GitHub onboarding")
        app.update_mode()
        self.assertTrue(app.fullcard.winfo_manager())
        self.assertFalse(app.permissioncard.winfo_manager())


    def test_gui_starts_without_authentication(self):
        root=tk.Tk();root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(backend.Session,"login",side_effect=AssertionError("No automatic login")):
            app=self.make_app(root);root.update()
        self.assertEqual(app.vars["client_name"].get(),"Example client")
        self.assertEqual(app.vars["mode"].get(),"Permissions only")
        self.assertIsNone(app.session)
        self.assertEqual(len(app.tabs.tabs()),3)
    def test_gui_blocks_apply_without_plan(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root)
        app.payload=lambda:(dict(WORKSPACE,target="acme-primary"),"Permissions only",[P1,P2])
        with patch.object(ui.messagebox,"showerror") as message, patch.object(app,"work") as launch:
            app.onboard(True)
            launch.assert_not_called()
            self.assertIn("Preview",message.call_args.args[1])
    def test_gui_blocks_expired_or_changed_plan(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root)
        payload=(dict(WORKSPACE,target="acme-primary"),"Permissions only",[P1,P2])
        app.payload=lambda:payload
        app.plan=(backend.fingerprint(*payload),0)
        with patch.object(ui.messagebox,"showerror"),patch.object(app,"work") as launch:
            app.onboard(True);launch.assert_not_called()

if __name__=="__main__":unittest.main(verbosity=2)

