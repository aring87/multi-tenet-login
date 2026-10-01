"""Cancellation tests use synthetic output and local child processes, never Azure."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from sentinel_app import desktop_app as ui
from sentinel_app import desktop_backend as backend
from sentinel_app.auth_recovery import SignInCancelled, signin_was_cancelled, AuthenticationRequired
from sentinel_app.signin_process import run_signin
from sentinel_app.lighthouse_onboarding import Stop
from test_desktop import TENANT, SUB

ROWS = [dict(id=SUB, tenantId=TENANT, name="Synthetic subscription", isDefault=True)]


class SignInProcessTests(unittest.TestCase):
    def test_cancel_before_launch_starts_no_process(self):
        cancel = threading.Event(); cancel.set()
        with patch("sentinel_app.signin_process.subprocess.Popen") as process:
            with self.assertRaises(SignInCancelled):run_signin(["unused"], {}, cancel)
        process.assert_not_called()

    def test_cancel_waiting_child_exits_promptly_and_reaps_only_that_child(self):
        cancel = threading.Event(); children = []
        popen = subprocess.Popen
        def spawn(*args, **kwargs):
            process = popen(*args, **kwargs); children.append(process)
            cancel.set()
            return process
        started = time.monotonic()
        with patch("sentinel_app.signin_process.subprocess.Popen", side_effect=spawn):
            with self.assertRaises(SignInCancelled):
                run_signin([sys.executable, "-c", "import time; time.sleep(30)"], os.environ.copy(), cancel)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_success_drains_large_stdout_and_stderr_without_pipe_deadlock(self):
        result = run_signin([sys.executable, "-c", "import sys; sys.stdout.write('x'*200000); sys.stderr.write('y'*200000)"],
                            os.environ.copy(), threading.Event())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "x" * 200000); self.assertEqual(result.stderr, "y" * 200000)

    def test_timeout_stops_and_reaps_child(self):
        children = []; popen = subprocess.Popen
        def spawn(*args, **kwargs):
            child = popen(*args, **kwargs); children.append(child); return child
        with patch("sentinel_app.signin_process.subprocess.Popen", side_effect=spawn):
            with self.assertRaisesRegex(Stop, "sign-in timed out"):
                run_signin([sys.executable, "-c", "import time; time.sleep(30)"], os.environ.copy(), threading.Event(), timeout=0.1)
        self.assertIsNotNone(children[0].poll())

    def test_cancel_wins_when_success_arrives_simultaneously(self):
        cancel = threading.Event(); process = MagicMock(returncode=0)
        process.wait.side_effect = lambda **kwargs:cancel.set()
        process.poll.return_value = 0
        with patch("sentinel_app.signin_process.subprocess.Popen", return_value=process):
            with self.assertRaises(SignInCancelled):run_signin(["fake"], {}, cancel)
        process.kill.assert_not_called()

    def test_explicit_cancel_codes_are_not_confused_with_mfa_or_access_denials(self):
        for text in ("User cancelled the flow", "user_canceled", "Status_UserCanceled", "AADSTS65004: User declined consent"):
            self.assertTrue(signin_was_cancelled(text), text)
        for text in ("AADSTS50076: MFA required", "AuthorizationFailed: Access denied", "access_denied", "consent_required"):
            self.assertFalse(signin_was_cancelled(text), text)

    def test_only_authentication_context_uses_cancellable_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            session = backend.Session(Path(folder)); session.az_exe = "fake-az"
            result = subprocess.CompletedProcess([], 0, "[]", "")
            with patch("sentinel_app.desktop_backend.run_signin", return_value=result) as signin, \
                 patch("sentinel_app.desktop_backend.subprocess.run", return_value=result) as ordinary:
                session.execute("fake-az", ["deployment", "sub", "validate"])
                ordinary.assert_called_once(); signin.assert_not_called()
                session._signin_cancel = threading.Event()
                session.execute("fake-az", ["login"])
                signin.assert_called_once(); self.assertEqual(ordinary.call_count, 1)

    def test_authentication_cancellation_context_cannot_run_cloud_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            session = backend.Session(Path(folder)); session.az_exe = "fake-az"
            session._signin_cancel = threading.Event()
            with patch("sentinel_app.desktop_backend.run_signin") as signin:
                with self.assertRaisesRegex(Stop, "Only authentication commands"):
                    session.execute("fake-az", ["deployment", "sub", "create"])
            signin.assert_not_called()

    def test_returned_cancellation_does_not_become_mfa_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            session = backend.Session(Path(folder))
            for method in ("false", "true"):
                session.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"] = method
                response = subprocess.CompletedProcess([], 1, "", "Status_UserCanceled: user canceled")
                with patch.object(session, "execute", return_value=response):
                    with self.assertRaises(SignInCancelled):session.login(TENANT)
                self.assertFalse(session.signed_in)


class SignInCancelUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(ui, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        p = patch.object(ui, "create_handoff", return_value=None); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root)
        self.addCleanup(self.cleanup)
        self.session = MagicMock(); self.session.env = {}; self.session.login.return_value = ROWS

    def cleanup(self):
        self.app.set_busy(False)
        self.app.finish_signin()
        for timer in self.root.tk.call("after", "info"):self.root.after_cancel(timer)
        self.root.destroy()

    def begin(self):
        with patch.object(ui, "Session", return_value=self.session), patch.object(self.app, "work") as work:
            self.app.login()
        self.app.set_busy(True)
        return work.call_args.args[1], work.call_args.args[2]

    def test_cancel_remains_enabled_while_busy_and_returns_ready_without_discovery(self):
        task, done = self.begin()
        self.assertEqual(str(self.app.cancel_signin_button.cget("state")), "normal")
        self.app.cancel_signin_button.invoke()
        with self.assertRaises(SignInCancelled) as caught:task()
        self.app.events.put(("done", (done, None, caught.exception)))
        with patch.object(ui.messagebox, "showerror") as error, patch.object(self.app, "recover_authentication") as recover:
            self.app.poll()
        self.assertFalse(self.app.busy); self.assertFalse(self.app.signin_active)
        self.assertIs(self.app.session, self.session); self.assertFalse(self.app.session.signed_in)
        self.assertFalse(self.app.subscriptions)
        self.assertIn("cancelled", self.app.status.get())
        self.session.login.assert_not_called(); self.session.discover.assert_not_called()
        error.assert_not_called(); recover.assert_not_called()
        self.assertEqual(str(self.app.cancel_signin_button.cget("state")), "disabled")

    def test_late_success_queued_before_cancel_cannot_start_discovery(self):
        task, done = self.begin(); rows = task()
        self.app.cancel_signin()
        self.app.events.put(("done", (done, rows, None)))
        self.app.poll()
        self.session.discover.assert_not_called()
        self.assertIs(self.app.session, self.session); self.assertFalse(self.app.session.signed_in)
        self.assertFalse(self.app.busy)
        self.assertFalse(self.app.workspaces)

    def test_cancellation_after_login_returns_still_discards_result(self):
        task, done = self.begin()
        cancel = self.app.signin_cancel
        def login(*args, **kwargs):cancel.set(); return ROWS
        self.session.login.side_effect = login
        with self.assertRaises(SignInCancelled):task()
        self.session.discover.assert_not_called()

    def test_cancel_during_previous_session_logout_does_not_launch_new_login(self):
        old = MagicMock(); self.app.session = old
        task, done = self.begin(); cancel = self.app.signin_cancel
        old.logout.side_effect = cancel.set
        with self.assertRaises(SignInCancelled):task()
        self.session.login.assert_not_called(); self.assertIsNone(old._signin_cancel)

    def test_queued_mfa_error_after_cancel_does_not_reopen_authentication(self):
        task, done = self.begin()
        problem = AuthenticationRequired("AADSTS50076: MFA required", TENANT)
        self.session.login.side_effect = problem
        with self.assertRaises(AuthenticationRequired):task()
        self.app.cancel_signin()
        self.app.events.put(("done", (done, None, problem)))
        with patch.object(self.app, "recover_authentication") as recover, patch.object(ui.messagebox, "showerror") as error:
            self.app.poll()
        recover.assert_not_called(); error.assert_not_called()
        self.assertFalse(self.app.busy); self.assertIn("cancelled", self.app.status.get())

    def test_cancel_wins_over_cleanup_failure(self):
        old = MagicMock(); self.app.session = old
        task, done = self.begin(); cancel = self.app.signin_cancel
        def logout():cancel.set(); raise Stop("Cleanup failed")
        old.logout.side_effect = logout
        with self.assertRaises(SignInCancelled):task()
        self.session.login.assert_not_called()

    def test_success_still_discovers_and_disables_cancellation(self):
        task, done = self.begin(); rows = task()
        self.app.events.put(("done", (done, rows, None)))
        with patch.object(self.app, "subscription_changed") as discover:self.app.poll()
        discover.assert_called_once()
        self.assertEqual(self.app.subscriptions, ROWS)
        self.assertFalse(self.app.signin_active)
        self.assertEqual(str(self.app.cancel_signin_button.cget("state")), "disabled")

    def test_retry_uses_new_session_and_fresh_cancellation_event(self):
        task, done = self.begin(); first = self.app.signin_cancel
        self.app.cancel_signin()
        self.app.events.put(("done", (done, None, SignInCancelled())))
        self.app.poll()
        next_session = MagicMock(); next_session.env = {}
        self.session = next_session
        task, done = self.begin()
        self.assertIsNot(first, self.app.signin_cancel)
        self.assertFalse(self.app.signin_cancel.is_set())
        self.assertIs(self.app.session, next_session)
