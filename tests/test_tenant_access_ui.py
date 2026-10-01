"""Access elevation is an explicit one-attempt UI action, never an automatic retry."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from sentinel_app import desktop_app as ui
from sentinel_app import desktop_backend as backend
from sentinel_app.auth_recovery import AuthenticationRequired
from test_desktop import TENANT, SUB, P1

ROWS = [dict(id=SUB, tenantId=TENANT, name='Client subscription', state='Enabled')]


class TenantAccessUITests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        for module in (ui, backend):
            patcher = patch.object(module, 'DATA', Path(folder.name))
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(ui, 'create_handoff', return_value=None)
        patcher.start(); self.addCleanup(patcher.stop)
        self.root = tk.Tk(); self.root.withdraw()
        self.app = ui.App(self.root); self.root.update()
        self.addCleanup(self.cleanup)
        self.session = MagicMock(); self.session.env = {}; self.session.signed_in = True
        self.session.login.return_value = []
        self.app.session = self.session
        self.app.auth_hint = TENANT
        self.app.vars['tenant'].set(TENANT)
        self.plan = dict(tenant=TENANT, principal=P1, account='admin@example.test', scope='/', created=time.time())

    def cleanup(self):
        self.app.set_busy(False); self.app.finish_signin()
        for timer in self.root.tk.call('after', 'info'): self.root.after_cancel(timer)
        self.root.destroy()

    def test_option_starts_unchecked_and_is_not_saved_as_a_setting(self):
        self.assertFalse(self.app.offer_access_management.get())
        self.assertNotIn('offer_access_management', self.app.values())
        self.assertNotIn('offer_access_management', ui.SETTINGS_KEYS)

    def test_opted_in_login_uses_arm_and_reviews_without_a_subscription(self):
        self.app.offer_access_management.set(True)
        with patch.object(ui, 'Session', return_value=self.session), patch.object(self.app, 'work') as work:
            self.app.login()
        task, done = work.call_args.args[1:3]
        rows = task()
        self.session.login.assert_called_once_with(TENANT, resource='arm', allow_empty=True)
        self.assertFalse(self.app.offer_access_management.get())
        with patch.object(self.app, 'azure_access_management') as review, \
             patch.object(self.app, 'subscription_changed') as discover:
            done(rows)
        review.assert_called_once(); discover.assert_not_called()

    def test_option_requires_explicit_client_guid_before_login(self):
        self.app.offer_access_management.set(True)
        self.app.vars['tenant'].set('')
        with patch.object(ui, 'Session') as session, patch.object(ui.messagebox, 'showerror') as error:
            self.app.login()
        session.assert_not_called(); error.assert_called_once()

    def test_cancelled_login_cannot_start_access_review(self):
        self.app.offer_access_management.set(True)
        with patch.object(ui, 'Session', return_value=self.session), patch.object(self.app, 'work') as work:
            self.app.login()
        done = work.call_args.args[2]
        self.app.signin_cancel.set()
        with patch.object(self.app, 'azure_access_management') as review:
            done(ROWS)
        review.assert_not_called()

    def test_mfa_recovery_does_not_replay_or_offer_elevation(self):
        self.app.offer_access_management.set(True)
        error = AuthenticationRequired('AADSTS50076', TENANT)
        with patch.object(ui, 'Session', return_value=self.session), patch.object(self.app, 'work') as work:
            self.app.login(recovery=error)
        task, done = work.call_args.args[1:3]
        task()
        self.session.login.assert_called_once_with(TENANT, resource='arm', claims=None)
        with patch.object(self.app, 'azure_access_management') as review, \
             patch.object(self.app, 'subscription_changed'):
            done(ROWS)
        review.assert_not_called(); self.assertFalse(self.app.offer_access_management.get())

    def test_declined_review_does_not_write_and_continues_discovery(self):
        continuation = MagicMock()
        with patch.object(self.app, 'work') as work, patch.object(ui, 'enable_access_management') as apply, \
             patch.object(ui.messagebox, 'askokcancel', return_value=False) as confirm:
            self.app.azure_access_management(on_skip=continuation)
            reviewed = work.call_args.args[2]
            reviewed(self.plan)
        apply.assert_not_called(); continuation.assert_called_once()
        self.assertEqual(work.call_count, 1)
        self.assertIn(TENANT, confirm.call_args.args[1]); self.assertIn('root scope', confirm.call_args.args[1])

    def test_session_or_tenant_change_discards_review(self):
        with patch.object(self.app, 'work') as work:
            self.app.azure_access_management()
        reviewed = work.call_args.args[2]
        for changed_session in (True, False):
            self.app.session = MagicMock() if changed_session else self.session
            self.app.vars['tenant'].set(TENANT if changed_session else SUB)
            with patch.object(ui.messagebox, 'askokcancel') as confirm, patch.object(self.app, 'work') as writes:
                reviewed(self.plan)
            confirm.assert_not_called(); writes.assert_not_called()

    def test_confirmation_applies_once_then_refreshes_only_the_reviewed_tenant(self):
        self.session.refresh_subscriptions.return_value = ROWS + [dict(ROWS[0], tenantId=SUB)]
        with patch.object(self.app, 'work') as work:
            self.app.azure_access_management()
        reviewed = work.call_args.args[2]
        with patch.object(ui.messagebox, 'askokcancel', return_value=True), patch.object(self.app, 'work') as write:
            reviewed(self.plan)
        with patch.object(ui, 'enable_access_management', return_value='Azure accepted') as apply:
            result = write.call_args.args[1]()
        apply.assert_called_once_with(self.session, self.plan)
        with patch.object(ui.messagebox, 'showinfo'), patch.object(self.app, 'work') as refresh:
            write.call_args.args[2](result)
        self.assertEqual(refresh.call_args.args[1](), ROWS)
        with patch.object(self.app, 'subscription_changed') as discover:
            refresh.call_args.args[2](ROWS)
        discover.assert_called_once()

    def test_refresh_is_available_after_successful_signin_without_subscriptions(self):
        self.assertFalse(self.app.subscriptions)
        with patch.object(self.app, 'work') as work, patch.object(ui.messagebox, 'showinfo') as warning:
            self.app.refresh_subscriptions()
        work.assert_called_once(); warning.assert_not_called()

    def test_changing_client_clears_the_one_time_option(self):
        self.app.offer_access_management.set(True)
        self.app.client_changed()
        self.assertFalse(self.app.offer_access_management.get())
