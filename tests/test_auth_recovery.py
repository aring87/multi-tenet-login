import base64
import json
import subprocess
import tempfile
import time
import tkinter as tk
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import desktop_backend as backend
import desktop_app as ui
from auth_recovery import AuthenticationRequired, azure_error, recovery_scope
from lighthouse_onboarding import Stop, CLI
from test_desktop import TENANT, SUB, WORKSPACE

MFA = "AADSTS50076: You must use multi-factor authentication to access '797f4846-ba00-4fd7-ba43-dac1f8f63013'. Correlation ID: synthetic"
ROWS = [{"id": SUB, "tenantId": TENANT, "name": "Synthetic subscription"}]
CLOUD = {"endpoints": {"activeDirectoryResourceId": "https://management.core.windows.net/",
                       "microsoftGraphResourceId": "https://graph.microsoft.com"}}


class AuthenticationBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = backend.Session(Path(self.temp.name))
        self.session.az_exe, self.session.gh_exe = "fake-az", "fake-gh"

    def result(self, code=0, data=None, error=""):
        return subprocess.CompletedProcess([], code, json.dumps(data), error)

    def test_failed_azure_operation_preserves_tenant_and_mfa_error(self):
        self.session.account = {"tenantId": TENANT}
        with patch.object(self.session, "execute", return_value=self.result(1, error=MFA)):
            with self.assertRaises(AuthenticationRequired) as caught:
                self.session.az("provider", "register")
        self.assertEqual(caught.exception.tenant, TENANT)
        self.assertEqual(caught.exception.resource, "arm")
        self.assertIn("AADSTS50076", str(caught.exception))

    def test_success_exit_with_empty_subscriptions_and_mfa_warning_is_not_access_denial(self):
        placeholder = [{"id": TENANT, "tenantId": TENANT}]
        with patch.object(self.session, "execute", return_value=self.result(data=placeholder, error=MFA)) as execute:
            with self.assertRaises(AuthenticationRequired): self.session.login(TENANT)
        self.assertNotIn("--only-show-errors", execute.call_args.args[1])
        self.assertIn("AADSTS50076", self.session.login_diagnostics)

    def test_partial_tenant_login_keeps_accessible_subscriptions_and_logs_warning(self):
        logs = []; self.session.log = logs.append
        with patch.object(self.session, "execute", return_value=self.result(data=ROWS, error=MFA)):
            self.assertEqual(self.session.login(), ROWS)
        self.assertTrue(any("AADSTS50076" in line for line in logs))

    def test_empty_subscriptions_preserves_non_mfa_diagnostics_and_allows_direct_check(self):
        with patch.object(self.session, "execute", return_value=self.result(data=[], error="Tenant discovery was incomplete")):
            with self.assertRaisesRegex(Stop, "Tenant discovery was incomplete") as caught: self.session.login(TENANT)
        self.assertNotIsInstance(caught.exception, AuthenticationRequired)
        self.assertTrue(self.session.signed_in)

    def test_recovery_login_uses_explicit_client_and_management_scope(self):
        with patch.object(self.session, "az", side_effect=[CLOUD, ROWS]) as az:
            self.session.login(TENANT, resource="arm")
        self.assertEqual(az.call_args.args, ("login", "--allow-no-subscriptions", "--tenant", TENANT,
                                          "--scope", "https://management.core.windows.net//.default"))

    def test_graph_recovery_uses_graph_scope(self):
        error = azure_error("InteractionRequired for 00000003-0000-0000-c000-000000000000", TENANT)
        with patch.object(self.session, "az", side_effect=[CLOUD, ROWS]) as az:
            self.session.login(TENANT, resource=error.resource)
        self.assertIn("https://graph.microsoft.com/.default", az.call_args.args)

    def test_claims_challenge_forwarded_as_one_argument_and_omitted_from_log(self):
        encoded = base64.b64encode(json.dumps({"access_token": {"acrs": {"essential": True, "value": "c1"}}}).encode()).decode()
        error = azure_error(MFA + "\naz login --claims-challenge '" + encoded + "'", TENANT)
        self.assertEqual(error.claims, encoded); self.assertNotIn(encoded, str(error))
        with patch.object(self.session, "az", side_effect=[CLOUD, ROWS]) as az:
            self.session.login(TENANT, resource=error.resource, claims=error.claims)
        self.assertEqual(az.call_args.args[-2:], ("--claims-challenge", encoded))

    def test_revoked_graph_token_is_scoped_to_graph(self):
        error = azure_error("role/_msgraph/_graph_client.py TokenIssuedBeforeRevocationTimestamp", TENANT)
        self.assertIsInstance(error, AuthenticationRequired)
        self.assertEqual(error.resource, "graph")

    def test_invalid_claims_are_not_forwarded(self):
        for value in ("bad", base64.b64encode(b'{"unexpected":true}').decode()):
            self.assertIsNone(azure_error(MFA + " --claims-challenge " + value).claims)

    def test_regular_rbac_denial_does_not_offer_mfa_recovery(self):
        self.assertNotIsInstance(azure_error("AuthorizationFailed: no role assignments/write"), AuthenticationRequired)

    def test_recovery_requires_client_tenant_before_login(self):
        with patch.object(self.session, "az") as az:
            with self.assertRaises(Stop): self.session.login(resource="arm")
        az.assert_not_called()

    def test_onboarding_adapter_preserves_typed_authentication_failure_without_retry(self):
        self.session.account = {"tenantId": TENANT}
        cli = backend.DesktopCLI(self.session, True)
        with patch.object(self.session, "execute", return_value=self.result(1, error=MFA)) as execute:
            with self.assertRaises(AuthenticationRequired): cli.az("provider", "register", write=True)
        execute.assert_called_once()

    def test_github_failure_is_not_treated_as_azure_authentication(self):
        cli = backend.DesktopCLI(self.session, True)
        with patch.object(self.session, "execute", return_value=self.result(1, error=MFA)):
            with self.assertRaises(Stop) as caught: cli.gh("GET", "repos/example/test")
        self.assertNotIsInstance(caught.exception, AuthenticationRequired)

    def test_resource_challenge_warning_reaches_onboarding_recovery(self):
        encoded = base64.b64encode(b'{"access_token":{"amr":{"values":["mfa"]}}}').decode()
        warning = 'WARNING: az login --claims-challenge "' + encoded + '"\n'
        self.session.account = {"tenantId": TENANT}
        def execute(executable, args, data=None):
            # Reproduce CLI warning suppression, not just a static exception fixture.
            visible = "" if "--only-show-errors" in args else warning
            return self.result(1, error=visible + MFA)
        with patch.object(self.session, "execute", side_effect=execute) as call:
            with self.assertRaises(AuthenticationRequired) as caught:
                backend.DesktopCLI(self.session, True).az("deployment", "sub", "validate")
        self.assertEqual(caught.exception.claims, encoded)
        self.assertNotIn(encoded, str(caught.exception)); call.assert_called_once()

    def test_session_operations_keep_challenge_warnings_despite_inherited_setting(self):
        with patch.dict(backend.os.environ, {"AZURE_CORE_ONLY_SHOW_ERRORS": "true"}):
            session = backend.Session(Path(self.temp.name))
        self.assertEqual(session.env["AZURE_CORE_ONLY_SHOW_ERRORS"], "false")
        with patch.object(session, "execute", return_value=self.result(data={})) as execute:
            session.az("deployment", "sub", "validate")
        self.assertNotIn("--only-show-errors", execute.call_args.args[1])

    def test_standalone_cli_preserves_warning_setting(self):
        cli = object.__new__(CLI); cli.azure = "fake-az"; cli.apply = False
        with patch("lighthouse_onboarding.subprocess.run", return_value=self.result(data={})) as run:
            cli.az("deployment", "sub", "validate")
        self.assertNotIn("--only-show-errors", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["env"]["AZURE_CORE_ONLY_SHOW_ERRORS"], "false")

    def test_unsupported_claims_flag_is_upgrade_error_not_signin_loop(self):
        error = azure_error("unrecognized arguments: --claims-challenge abc", TENANT)
        self.assertNotIsInstance(error, AuthenticationRequired)
        self.assertIn("Update Azure CLI", str(error))
        self.assertNotIn(" abc", str(error))

    def test_scope_follows_cloud_metadata_and_refuses_unknown_resource(self):
        self.assertEqual(recovery_scope({"endpoints": {"activeDirectoryResourceId": "https://management.core.usgovcloudapi.net/"}}, "arm"),
                         "https://management.core.usgovcloudapi.net//.default")
        with self.assertRaises(Stop): recovery_scope(CLOUD, "unexpected")


class AuthenticationUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        for module in (backend, ui):
            p = patch.object(module, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        p = patch.object(ui, "create_handoff", return_value=None); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.addCleanup(self.cleanup)
        self.error = AuthenticationRequired(MFA, TENANT)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def test_accepting_reauthentication_invalidates_preview_and_only_starts_signin(self):
        self.app.plan = ("old", time.time()); self.app.workspace = WORKSPACE
        with patch.object(ui.messagebox, "askyesno", return_value=True), patch.object(self.app, "login") as login, patch.object(self.app, "onboard") as onboard:
            self.app.recover_authentication(self.error)
        login.assert_called_once_with(recovery=self.error); onboard.assert_not_called()
        self.assertIsNone(self.app.plan); self.assertEqual(self.app.vars["tenant"].get(), TENANT)

    def test_declining_does_not_open_signin_and_invalidates_preview(self):
        self.app.plan = ("old", time.time())
        with patch.object(ui.messagebox, "askyesno", return_value=False), patch.object(self.app, "login") as login:
            self.app.recover_authentication(self.error)
        login.assert_not_called(); self.assertIsNone(self.app.plan)

    def test_repeated_challenge_stops_instead_of_prompt_loop(self):
        self.app.auth_recovery_attempts.add((TENANT, "arm", "Browser", False))
        with patch.object(ui.messagebox, "showerror") as show, patch.object(ui.messagebox, "askyesno") as ask, patch.object(self.app, "login") as login:
            self.app.recover_authentication(self.error)
        show.assert_called_once(); ask.assert_not_called(); login.assert_not_called()

    def test_new_resource_claim_can_follow_scoped_signin_once(self):
        self.app.auth_recovery_attempts.add((TENANT, "arm", "Browser", False))
        encoded = base64.b64encode(b'{"access_token":{"amr":{"values":["mfa"]}}}').decode()
        error = AuthenticationRequired(MFA + " --claims-challenge " + encoded, TENANT)
        with patch.object(ui.messagebox, "askyesno", return_value=True), patch.object(self.app, "login") as login:
            self.app.recover_authentication(error)
        login.assert_called_once_with(recovery=error)
        # A different payload must not create an endless series of prompts.
        changed = base64.b64encode(b'{"access_token":{"amr":{"essential":true,"values":["mfa"]}}}').decode()
        with patch.object(ui.messagebox, "showerror") as show, patch.object(self.app, "login") as login:
            self.app.recover_authentication(AuthenticationRequired(MFA + " --claims-challenge " + changed, TENANT))
        show.assert_called_once(); login.assert_not_called()

    def test_broker_failure_offers_browser_and_changes_method_only_when_accepted(self):
        error = AuthenticationRequired("SubError: basic_action V2Error: " + MFA + " Status_InteractionRequired", TENANT)
        self.app.vars["login_method"].set("Windows account window")
        with patch.object(ui.messagebox, "askyesno", return_value=False), patch.object(self.app, "login") as login:
            self.app.recover_authentication(error)
        self.assertEqual(self.app.vars["login_method"].get(), "Windows account window")
        login.assert_not_called()
        with patch.object(ui.messagebox, "askyesno", return_value=True) as ask, patch.object(self.app, "login") as login:
            self.app.recover_authentication(error)
        self.assertIn("browser", ask.call_args.args[1])
        self.assertEqual(self.app.vars["login_method"].get(), "Browser")
        login.assert_called_once_with(recovery=error)

    def test_recovery_with_claims_reaches_new_isolated_login(self):
        old, new = MagicMock(), MagicMock(); new.env = {}; new.login.return_value = ROWS
        encoded = base64.b64encode(b'{"access_token":{"amr":{"values":["mfa"]}}}').decode()
        error = AuthenticationRequired(MFA + " --claims-challenge " + encoded, TENANT)
        self.app.session = old; self.app.vars["tenant"].set(TENANT)
        with patch.object(ui, "Session", return_value=new), patch.object(self.app, "work") as work:
            self.app.login(recovery=error); work.call_args.args[1]()
        new.login.assert_called_once_with(TENANT, resource="arm", claims=encoded)
        old.logout.assert_called_once()

    def test_unknown_tenant_must_be_supplied_not_guessed(self):
        with patch.object(ui.messagebox, "askyesno", return_value=True), patch.object(ui.simpledialog, "askstring", return_value=None), patch.object(self.app, "login") as login:
            self.app.recover_authentication(AuthenticationRequired(MFA))
        login.assert_not_called()

    def test_worker_keeps_exception_type_and_never_replays_task(self):
        task = MagicMock(side_effect=self.error)
        def start(thread): thread.run()
        with patch.object(ui.threading.Thread, "start", start): self.app.work("synthetic write", task)
        with patch.object(self.app, "recover_authentication") as recover, patch.object(ui.messagebox, "showerror") as show:
            self.app.poll()
        task.assert_called_once(); recover.assert_called_once_with(self.error); show.assert_not_called()

    def test_recovery_starts_fresh_app_session_with_scoped_login(self):
        old, new = MagicMock(), MagicMock(); new.env = {}; new.login.return_value = ROWS
        self.app.session = old; self.app.vars["tenant"].set(TENANT)
        with patch.object(ui, "Session", return_value=new), patch.object(self.app, "work") as work:
            self.app.login(recovery=self.error)
            work.call_args.args[1]()
        old.logout.assert_called_once()
        new.login.assert_called_once_with(TENANT, resource="arm", claims=None)
        self.assertIs(self.app.session, new)

    def test_missing_subscription_diagnostic_available_after_empty_discovery(self):
        self.app.session = MagicMock(); self.app.session.signed_in = True
        self.app.subscriptions = []
        with patch.object(ui.simpledialog, "askstring", return_value=SUB), patch.object(self.app, "work") as work:
            self.app.check_subscription()
        self.assertTrue(work.called)
