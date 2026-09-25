"""Offline desktop tests: no Azure/GitHub network calls."""
import copy
import json
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
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

    def test_refresh_keeps_non_enabled_subscriptions(self):
        session=backend.Session(self.data/"refresh")
        rows=[dict(id=SUB,tenantId=TENANT,state="Warned"),dict(id=TENANT,tenantId=TENANT)]
        with patch.object(session,"az",return_value=rows) as az:
            self.assertEqual(session.refresh_subscriptions(),rows[:1])
            az.assert_called_once_with("account","list","--all","--refresh")

    def test_direct_check_reports_workspaces_without_changing_selection(self):
        session=backend.Session(self.data/"direct")
        responses=[dict(tenantId=TENANT,user={"name":"example"}),
                   dict(name="AzureCloud",endpoints={"resourceManager":"https://management.azure.com/"}),
                   dict(subscriptionId=SUB,tenantId=TENANT,state="Enabled"),
                   dict(value=[dict(id=ITEM["id"],properties={"customerId":WS})])]
        with patch.object(session,"az",side_effect=responses) as az:
            report=session.check_subscription(SUB)
            self.assertIn(WS,report)
            self.assertIn("law-example-sentinel",report)
            for call in az.call_args_list:
                self.assertNotIn("set",call.args)
                self.assertNotIn("put",call.args)

    def test_direct_check_preserves_access_error(self):
        session=backend.Session(self.data/"denied")
        with patch.object(session,"az",side_effect=[dict(tenantId=TENANT),
             dict(name="AzureCloud",endpoints={"resourceManager":"https://management.azure.com/"}),Stop("AuthorizationFailed")]):
            self.assertIn("AuthorizationFailed",session.check_subscription(SUB))

    def test_login_keeps_warned_subscription_and_excludes_tenant_placeholder(self):
        session=backend.Session(self.data/"warned-login")
        rows=[dict(id=SUB,tenantId=TENANT,state="Warned",name="Example"),
              dict(id=TENANT,tenantId=TENANT,state="Enabled")]
        with patch.object(session,"az",return_value=rows):
            self.assertEqual(session.login(),rows[:1])

    def test_tenantless_login_does_not_pass_tenant_argument(self):
        session=backend.Session(self.data/"no-tenant")
        rows=[dict(id=SUB,tenantId=TENANT,state="Enabled",name="Example")]
        with patch.object(session,"az",return_value=rows) as az:
            self.assertEqual(session.login(),rows)
            az.assert_called_once_with("login","--allow-no-subscriptions")

    def test_known_tenant_login_is_still_scoped(self):
        session=backend.Session(self.data/"known-tenant")
        rows=[dict(id=SUB,tenantId=TENANT,state="Enabled",name="Example")]
        with patch.object(session,"az",return_value=rows) as az:
            session.login(TENANT)
            az.assert_called_once_with("login","--allow-no-subscriptions","--tenant",TENANT)

    def test_signin_auto_discovers_and_subscription_switch_clears_details(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=ui.App(root)
        session=MagicMock();session.env={};session.account={}
        session.login.return_value=[dict(id=SUB,tenantId=TENANT,name="Example",isDefault=True)]
        session.discover.return_value=[WORKSPACE]
        def work(title,task,done):
            done(task())
        with patch.object(ui,"Session",return_value=session),patch.object(app,"work",side_effect=work):
            app.login()
            session.login.assert_called_once_with("")
            session.discover.assert_called_once_with(SUB,TENANT)
            details=app.details.get("1.0","end")
            for value in (TENANT,SUB,WS,"rg-example-sentinel","law-example-sentinel"):
                self.assertIn(value,details)
            self.assertEqual(app.details.cget("state"),"disabled")
            session.discover.return_value=[]
            app.subscription_changed()
            self.assertIsNone(app.workspace)
            self.assertNotIn(WS,app.details.get("1.0","end"))
            self.assertIn("No accessible workspaces",app.identity.get())

    def test_client_can_be_saved_before_tenant_is_known(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root);app.vars["tenant"].set("")
        app.save_client()
        self.assertEqual(json.loads(app.clientfile.read_text())[0]["tenant"],"")

    def test_public_app_starts_with_empty_inventory(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=ui.App(root)
        self.assertEqual(app.clients,[])
        self.assertEqual(app.vars["github_owner"].get(),"")
        self.assertEqual(app.vars["github_repo"].get(),"")

    def test_tenant_hint_accepts_guid_and_verified_domain_syntax(self):
        self.assertEqual(backend.validate_tenant_hint(TENANT),TENANT)
        self.assertEqual(backend.validate_tenant_hint(" Client.OnMicrosoft.com "),"client.onmicrosoft.com")
        self.assertEqual(backend.validate_tenant_hint("customer.example"),"customer.example")

    def test_tenant_hint_rejects_portals_urls_and_usernames(self):
        for value in ("azure.portal.com","portal.azure.com","https://portal.azure.com",
                      "entra.microsoft.com","user@example.com","common","127.0.0.1","bad..example"):
            with self.subTest(value=value),self.assertRaises(Stop):
                backend.validate_tenant_hint(value)

    def test_invalid_tenant_stops_before_session_or_login(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=ui.App(root);app.vars["tenant"].set("azure.portal.com")
        with patch.object(ui.messagebox,"showerror") as error,patch.object(ui,"Session") as session:
            app.login()
            session.assert_not_called()
            self.assertIn("Directory (tenant) ID",error.call_args.args[1])

    def test_invalid_tenant_not_saved(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root);app.vars["tenant"].set("portal.azure.com")
        with patch.object(ui.messagebox,"showerror"):
            app.save_client()
        self.assertFalse(app.clientfile.exists())

    def test_cyberqp_opens_selected_region_without_authentication(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=ui.App(root)
        with patch.object(ui.webbrowser,"open",return_value=True) as browser,patch.object(ui,"Session") as session:
            for region,url in backend.CYBERQP_PORTALS.items():
                app.vars["cyberqp_region"].set(region)
                app.open_cyberqp()
                browser.assert_called_with(url,new=2)
            session.assert_not_called()

    def test_logout_of_empty_session_is_harmless(self):
        session=backend.Session(self.data/"empty")
        session.az_exe="fake"
        with patch.object(session,"az",side_effect=Stop("ERROR: There are no active accounts.")):
            session.logout()
        self.assertIsNone(session.account)

    def test_logout_does_not_hide_other_failures(self):
        session=backend.Session(self.data/"bad")
        session.az_exe="fake"
        with patch.object(session,"az",side_effect=Stop("Access denied")):
            with self.assertRaisesRegex(Stop,"Access denied"):session.logout()

    def test_retry_continues_after_empty_previous_session(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root)
        old=backend.Session(self.data/"old");old.az_exe="fake"
        app.session=old
        new=MagicMock();new.env={}
        with patch.object(old,"az",side_effect=Stop("There are no active accounts.")),patch.object(ui,"Session",return_value=new),patch.object(app,"work",side_effect=lambda title,task,done:task()):
            app.login()
        new.login.assert_called_once_with(TENANT)
        self.assertEqual(new.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"],"false")

    def test_windows_broker_can_be_selected(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=self.make_app(root);app.vars["login_method"].set("Windows account window")
        new=MagicMock();new.env={}
        with patch.object(ui,"Session",return_value=new),patch.object(app,"work"):
            app.login()
        self.assertEqual(new.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"],"true")

    def test_portal_button_opens_portal_without_app_authentication(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=ui.App(root)
        with patch.object(ui.webbrowser,"open",return_value=True) as browser,patch.object(ui,"Session") as session:
            app.open_azure_portal()
        browser.assert_called_once_with("https://portal.azure.com/",new=2)
        session.assert_not_called()

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

