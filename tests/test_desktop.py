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
from sentinel_app import desktop_backend as backend
from sentinel_app import desktop_app as ui
from sentinel_app.lighthouse_onboarding import Stop

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
        handoff=patch.object(ui,"create_handoff",return_value=None)
        handoff.start();self.addCleanup(handoff.stop)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)
        for module in (backend,ui):
            p=patch.object(module,"DATA",self.data)
            p.start();self.addCleanup(p.stop)
    def close_root(self,root):
        for timer in root.tk.call("after","info"):
            root.after_cancel(timer)
        root.update_idletasks()
        root.destroy()

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
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
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
            shown=[label.cget("text") for label in app.details.values]
            for value in (TENANT,SUB,WS,"rg-example-sentinel","law-example-sentinel"):
                self.assertIn(value,shown)
            session.discover.return_value=[]
            app.subscription_changed()
            self.assertIsNone(app.workspace)
            self.assertNotIn(WS,[label.cget("text") for label in app.details.values])
            self.assertIn("No accessible workspaces",app.identity.get())

    def test_client_can_be_saved_before_tenant_is_known(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.vars["tenant"].set("")
        app.save_client()
        self.assertEqual(json.loads(app.clientfile.read_text())[0]["tenant"],"")

    def test_public_app_starts_with_empty_inventory(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
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
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=ui.App(root);app.vars["tenant"].set("azure.portal.com")
        with patch.object(ui.messagebox,"showerror") as error,patch.object(ui,"Session") as session:
            app.login()
            session.assert_not_called()
            self.assertIn("Directory (tenant) ID",error.call_args.args[1])

    def test_invalid_tenant_not_saved(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.vars["tenant"].set("portal.azure.com")
        with patch.object(ui.messagebox,"showerror"):
            app.save_client()
        self.assertFalse(app.clientfile.exists())

    def test_cyberqp_opens_portal_without_authentication(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=ui.App(root)
        self.assertNotIn("cyberqp_region",app.vars)
        with patch.object(ui.webbrowser,"open",return_value=True) as browser,patch.object(ui,"Session") as session:
            app.open_cyberqp()
        browser.assert_called_once_with(backend.CYBERQP_PORTALS["US"],new=2)
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
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        old=backend.Session(self.data/"old");old.az_exe="fake"
        app.session=old
        new=MagicMock();new.env={}
        with patch.object(old,"az",side_effect=Stop("There are no active accounts.")),patch.object(ui,"Session",return_value=new),patch.object(app,"work",side_effect=lambda title,task,done:task()):
            app.login()
        new.login.assert_called_once_with(TENANT)
        self.assertEqual(new.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"],"false")

    def test_signin_handoff_stops_after_success_and_failure(self):
        for failure in (False,True):
            with self.subTest(failure=failure):
                root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
                app=self.make_app(root)
                session=MagicMock();session.env={}
                if failure:session.login.side_effect=Stop("Cancelled")
                handoff=MagicMock()
                with patch.object(ui,"Session",return_value=session),patch.object(ui,"create_handoff",return_value=handoff),patch.object(app,"work") as work:
                    app.login()
                self.assertTrue(app.signin_active)
                self.assertEqual(str(app.signin_button.cget("state")),"normal")
                handoff.bring_forward.assert_called_once_with()
                app.bring_signin_forward();handoff.bring_forward.assert_called_with(manual=True)
                task=work.call_args.args[1]
                if failure:
                    with self.assertRaises(Stop):task()
                else:task()
                app.poll()
                self.assertFalse(app.signin_active)
                self.assertIsNone(app.signin_timer)
                self.assertIsNone(app.signin_handoff)
                self.assertEqual(str(app.signin_button.cget("state")),"disabled")

    def test_windows_broker_can_be_selected(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.vars["login_method"].set("Windows account window")
        new=MagicMock();new.env={}
        with patch.object(ui,"Session",return_value=new),patch.object(app,"work"):
            app.login()
        self.assertEqual(new.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"],"true")

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
    def lighthouse_config(self):
        config=json.loads((backend.BASE/"examples/lighthouse-onboarding.example.json").read_text(encoding="utf-8"))
        config.update({k:WORKSPACE[k] for k in ("tenant_id","subscription_id","resource_group","workspace_name")})
        return config

    def test_retired_permission_mode_blocks_plan_and_apply_before_cloud(self):
        for apply in (False,True):
            with self.subTest(apply=apply):
                session=FakeSession()
                with self.assertRaisesRegex(Stop,"Permissions-only mode is retired"):
                    backend.permission_run(session,WORKSPACE,"acme-primary",[P1,P2],apply)
                self.assertEqual(session.calls,[])

    def test_lighthouse_preview_uses_read_only_adapter_and_delegation_template(self):
        session=FakeSession();session.az_exe="fake-az";session.gh_exe="fake-gh"
        with patch.object(backend,"Onboard") as onboard:
            result=backend.full_run(session,self.lighthouse_config(),False)
        args=onboard.call_args.args
        self.assertFalse(args[1].apply)
        self.assertEqual(args[3],backend.BASE/"templates/lighthouse-onboard.json")
        onboard.return_value.run.assert_called_once_with()
        self.assertIn("no cloud resources changed",result)
        self.assertFalse(list(self.data.rglob("*.lock")))

    def test_lighthouse_invalid_config_blocks_onboarding(self):
        config=self.lighthouse_config();config["tenant_id"]="invalid"
        session=MagicMock()
        with patch.object(backend,"Onboard") as onboard,self.assertRaises(Stop):
            backend.full_run(session,config,True)
        session.verify.assert_not_called();onboard.assert_not_called()

    def test_lighthouse_apply_releases_lock_after_failure(self):
        session=FakeSession();session.az_exe="fake-az";session.gh_exe="fake-gh"
        config=self.lighthouse_config()
        lock=self.data/"runs"/"example-client-primary"/"onboarding.lock"
        def fail():
            self.assertTrue(lock.exists())
            raise Stop("Deployment failed")
        with patch.object(backend,"Onboard") as onboard:
            onboard.return_value.run.side_effect=fail
            with self.assertRaisesRegex(Stop,"Deployment failed"):
                backend.full_run(session,config,True)
            self.assertTrue(onboard.call_args.args[1].apply)
        self.assertFalse(lock.exists())

    def test_plan_fingerprint_covers_destination_and_delegation_groups(self):
        config=self.lighthouse_config()
        changed_tenant=dict(config,tenant_id=SUB)
        changed_group=dict(config,deploy_group_object_id=P1)
        fingerprints={backend.fingerprint(c,"lighthouse-delegation",[])
                      for c in (config,changed_tenant,changed_group)}
        self.assertEqual(len(fingerprints),3)
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
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        before=copy.deepcopy(app.clients)
        with patch.object(ui.messagebox,"askyesno",return_value=False):
            app.delete_client()
        self.assertEqual(app.clients,before)
        self.assertFalse(app.clientfile.exists())

    def test_delete_last_client_persists_empty_list_and_clears_plan(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
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
        self.assertIsNone(app.workspace)
        self.assertEqual(app.session.calls,[])
        self.assertTrue(state.exists())
        self.assertEqual(app.read_clients(),[])

    def test_delete_client_selects_remaining_client(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        app.clients.append({"name":"Second client","slug":"second-client","tenant":TENANT})
        with patch.object(ui.messagebox,"askyesno",return_value=True):
            app.delete_client()
        self.assertEqual(app.vars["client_name"].get(),"Second client")
        self.assertEqual(app.vars["tenant"].get(),TENANT)

    def test_delete_blocked_during_running_operation(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.busy=True
        with patch.object(ui.messagebox,"askyesno") as prompt:
            app.delete_client();prompt.assert_not_called()
        self.assertEqual(len(app.clients),1)

    def test_navigation_offers_connect_rules_and_builder_only(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        self.assertEqual([item["title"].cget("text") for item in app.nav],["Connect","Rules","Rule builder"])
        for removed in ("audit_start","label","managing_tenant_id"):
            self.assertNotIn(removed,app.vars)
        root.deiconify();root.update()
        for index,item in enumerate(app.nav):
            item["row"].event_generate("<Button-1>")
            root.update()
            self.assertEqual(app.tabs.index("current"),index)
            self.assertEqual(item["title"].cget("fg"),"#FFFFFF")

    def test_saving_repository_keeps_command_line_settings(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        (self.data/"settings.json").write_text(json.dumps({"managing_tenant_id":TENANT,"github_owner":"old"}))
        app=self.make_app(root)
        self.assertEqual(app.vars["github_owner"].get(),"old")
        app.vars["github_owner"].set("example");app.vars["github_repo"].set("detections")
        app.save_settings()
        self.assertEqual(json.loads(app.settingsfile.read_text()),
                         {"managing_tenant_id":TENANT,"github_owner":"example","github_repo":"detections"})

    def test_gui_starts_without_authentication(self):
        root=tk.Tk();root.withdraw()
        self.addCleanup(self.close_root,root)
        with patch.object(backend.Session,"login",side_effect=AssertionError("No automatic login")):
            app=self.make_app(root);root.update()
        self.assertEqual(app.vars["client_name"].get(),"Example client")
        self.assertIsNone(app.workspace)
        self.assertIsNone(app.session)
        self.assertEqual(len(app.tabs.tabs()),3)
    def test_new_client_dialog_validates_tenant(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=ui.App(root)
        def show(dialog,tenant):
            dialog.vars["name"].set("Fabrikam Inc");dialog.vars["tenant"].set(tenant)
            dialog.submit()
            if dialog.result is None:
                self.assertIn("Directory (tenant) ID",dialog.error.get());dialog.window.destroy()
            return dialog.result
        with patch.object(ui.kit.FormDialog,"show",lambda dialog:show(dialog,"portal.azure.com")):
            app.add_client()
        self.assertFalse(app.clientfile.exists())
        with patch.object(ui.kit.FormDialog,"show",lambda dialog:show(dialog," Fabrikam.OnMicrosoft.com ")):
            app.add_client()
        self.assertEqual(json.loads(app.clientfile.read_text()),
                         [{"name":"Fabrikam Inc","tenant":"fabrikam.onmicrosoft.com"}])

if __name__=="__main__":unittest.main(verbosity=2)

