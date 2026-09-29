"""Offline desktop tests: no Azure/GitHub network calls."""
import copy
import json
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import desktop_backend as backend
import desktop_app as ui
from lighthouse_onboarding import Stop

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

    def test_cyberqp_opens_selected_region_without_authentication(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
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
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        old=backend.Session(self.data/"old");old.az_exe="fake"
        app.session=old
        new=MagicMock();new.env={}
        with patch.object(old,"az",side_effect=Stop("There are no active accounts.")),patch.object(ui,"Session",return_value=new),patch.object(app,"work",side_effect=lambda title,task,done:task()):
            app.login()
        new.login.assert_called_once_with(TENANT)
        self.assertEqual(new.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"],"false")

    def test_windows_broker_can_be_selected(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.vars["login_method"].set("Windows account window")
        new=MagicMock();new.env={}
        with patch.object(ui,"Session",return_value=new),patch.object(app,"work"):
            app.login()
        self.assertEqual(new.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"],"true")

    def test_portal_button_opens_portal_without_app_authentication(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
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
    def lighthouse_config(self):
        config=json.loads((backend.BASE/"lighthouse-onboarding.example.json").read_text(encoding="utf-8"))
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
        self.assertEqual(args[3],backend.BASE/"lighthouse-onboard.json")
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

    def test_lighthouse_settings_and_navigation_are_available(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        self.assertNotIn("mode",app.vars)
        for key in ("managing_tenant_id","deploy_group_object_id","read_group_object_id",
                    "engineer_group_object_id","preview_environment","production_environment"):
            self.assertIn(key,app.vars)
        rows=sorted(app.nav,key=lambda row:int(row[1].cget("text")))
        self.assertEqual([row[2].cget("text") for row in rows],
                         ["Workspaces","Analytics rules","Onboarding","Review & apply","Sentinel audit"])
        root.deiconify();root.update()
        for row in rows:
            row[0].event_generate("<Button-1>")
            root.update()
            self.assertEqual(row[2].cget("fg"),"#ffffff")

    def test_gui_starts_without_authentication(self):
        root=tk.Tk();root.withdraw()
        self.addCleanup(self.close_root,root)
        with patch.object(backend.Session,"login",side_effect=AssertionError("No automatic login")):
            app=self.make_app(root);root.update()
        self.assertEqual(app.vars["client_name"].get(),"Example client")
        self.assertIsNone(app.workspace)
        self.assertIsNone(app.session)
        self.assertEqual(len(app.tabs.tabs()),5)
    def test_audit_requires_discovered_workspace(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        with patch.object(ui.messagebox,"showerror") as message,patch.object(ui.filedialog,"askdirectory") as choose:
            app.collect_audit()
        message.assert_called_once()
        choose.assert_not_called()

    def test_audit_selection_clears_with_workspace(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.workspace=dict(WORKSPACE)
        app.audit_source_tables=["SecurityEvent"]
        app.audit_flags["audit"].set(True)
        app.evidence_folder=self.data
        app.clear_selection()
        self.assertEqual(app.audit_source_tables,[])
        self.assertFalse(app.audit_flags["audit"].get())
        self.assertIsNone(app.evidence_folder)

    def test_audit_invalid_selection_does_not_open_destination_dialog(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.workspace=dict(WORKSPACE);app.session=FakeSession()
        app.audit_source_tables=["Heartbeat | take 1"]
        with patch.object(ui.messagebox,"showerror") as message,patch.object(ui.filedialog,"askdirectory") as choose:
            app.collect_audit()
        message.assert_called_once();choose.assert_not_called()

    def test_audit_defaults_to_configuration_without_logs(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        options=app.audit_options()
        self.assertEqual(options["configurations"],list(ui.CONFIGURATIONS))
        self.assertEqual(options["source_tables"],[])
        self.assertFalse(options["audit"] or options["health"])
        self.assertEqual(options["source_mode"],"sample")

    def test_audit_action_uses_captured_workspace_and_reports_results(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.workspace=dict(WORKSPACE);app.session=FakeSession()
        app.vars["audit_start"].set("2024-01-01");app.vars["audit_end"].set("2024-01-02")
        result=dict(folder=str(self.data/"evidence-example"),manifest=dict(results=[dict(title="Rules",status="access_denied",record_count=0)]))
        with patch.object(ui.filedialog,"askdirectory",return_value=str(self.data)),patch.object(ui,"collect_evidence",return_value=result) as collect,patch.object(app,"work") as launch:
            app.collect_audit()
            self.assertEqual(launch.call_args.kwargs["page"],3)
            app.workspace=None
            returned=launch.call_args.args[1]()
            launch.call_args.args[2](returned)
        self.assertEqual(collect.call_args.args[1],WORKSPACE)
        self.assertIn("access denied",app.audit_summary.get())
        self.assertEqual(app.tabs.index("current"),3)
        self.assertIsNone(app.plan)

    def test_audit_picker_previews_selected_tables_and_does_not_treat_denial_as_zero(self):
        import time
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.workspace=dict(WORKSPACE)
        app.vars["audit_start"].set("2024-01-01");app.vars["audit_end"].set("2024-01-02")
        result=dict(status="collected",records=[dict(name="SigninLogs",properties=dict(plan="Analytics")),dict(name="SentinelAudit")])
        app.show_source_picker(result,FakeSession(),dict(WORKSPACE))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(root))
        tree=next(w for w in widgets if isinstance(w,ui.ttk.Treeview) and w.winfo_toplevel() is not root)
        self.assertEqual(tree.get_children(),("SigninLogs",))
        self.assertFalse(tree.selection())
        tree.selection_set("SigninLogs")
        preview=next(w for w in widgets if isinstance(w,ui.ttk.Button) and w.cget("text")=="Preview selected counts")
        use=next(w for w in widgets if isinstance(w,ui.ttk.Button) and w.cget("text")=="Use selected tables")
        with patch.object(ui,"preview_log_tables",return_value=[dict(table="SigninLogs",records=[],status="access_denied")]) as query:
            preview.invoke()
            deadline=time.monotonic()+3
            while tree.set("SigninLogs","status")=="Not previewed" and time.monotonic()<deadline:
                root.update();time.sleep(.01)
        self.assertEqual(tree.set("SigninLogs","count"),"Unknown")
        self.assertEqual(tree.set("SigninLogs","status"),"access denied")
        self.assertEqual(query.call_args.args[2:],("2024-01-01","2024-01-02",["SigninLogs"]))
        use.invoke()
        self.assertEqual(app.audit_options()["source_tables"],["SigninLogs"])

    def test_audit_configuration_export_ignores_log_dates(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root);app.workspace=dict(WORKSPACE);app.session=FakeSession()
        app.vars["audit_start"].set("invalid")
        with patch.object(ui.filedialog,"askdirectory",return_value=str(self.data)),patch.object(app,"work") as launch:
            app.collect_audit()
        launch.assert_called_once()

    def test_gui_blocks_apply_without_plan(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        app.payload=self.lighthouse_config
        with patch.object(ui.messagebox,"showerror") as message, patch.object(app,"work") as launch:
            app.onboard(True)
            launch.assert_not_called()
            self.assertIn("Preview",message.call_args.args[1])
    def test_gui_blocks_expired_or_changed_plan(self):
        root=tk.Tk();root.withdraw();self.addCleanup(self.close_root,root)
        app=self.make_app(root)
        payload=self.lighthouse_config()
        app.payload=lambda:payload
        token=backend.fingerprint(payload,"lighthouse-delegation",[])
        for plan in ((token,0),("different configuration",time.time())):
            with self.subTest(plan=plan),patch.object(ui.messagebox,"showerror") as message,patch.object(app,"work") as launch:
                app.plan=plan
                app.onboard(True);launch.assert_not_called()
                self.assertIn("Preview",message.call_args.args[1])

if __name__=="__main__":unittest.main(verbosity=2)

