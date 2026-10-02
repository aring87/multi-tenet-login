import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from sentinel_app import desktop_app as ui
from sentinel_app.auth_recovery import AuthenticationRequired
from sentinel_app.desktop_backend import Stop
from sentinel_app.access_readiness import AccessHistory
from test_desktop import TENANT, SUB, WORKSPACE


class AccessRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(ui,'DATA',Path(self.temp.name));p.start();self.addCleanup(p.stop)
        p=patch.object(ui,'create_handoff',return_value=None);p.start();self.addCleanup(p.stop)
        self.root=tk.Tk();self.root.withdraw();self.app=ui.App(self.root);self.root.update()
        self.addCleanup(self.cleanup)
        self.app.session=MagicMock();self.app.subscriptions=[dict(id=SUB,tenantId=TENANT,name='Test')]
        self.app.subbox.configure(values=['Test']);self.app.subbox.current(0)
        self.target=dict(tenant_id=TENANT,subscription_id=SUB)

    def cleanup(self):
        for timer in self.root.tk.call('after','info'):self.root.after_cancel(timer)
        self.root.destroy()

    def work(self,title,task,done,**kw):done(task())

    def test_first_click_is_saved_even_when_role_review_is_cancelled(self):
        plan=dict(needs_role=True,account='user@example.com',principal='id',scope='scope')
        with patch.object(ui,'access_plan',return_value=plan),patch.object(ui.messagebox,'askokcancel',return_value=False),patch.object(self.app,'work',side_effect=self.work),patch.object(ui,'apply_contributor') as apply:
            self.app.setup_access()
        apply.assert_not_called()
        row=self.app.access_history.get(self.target)
        self.assertIn('first_clicked',row);self.assertNotIn('assignment_accepted',row)
        self.assertIn('First Contributor setup click',self.app.access_summary.get())

    def test_refresh_keeps_workspace_and_does_not_sign_out_or_grant(self):
        self.app.workspace=dict(WORKSPACE);self.app.plan=('old',0)
        result=dict(ready=False,account='user@example.com',missing=[],detail='Azure still denies validation.')
        with patch.object(ui,'check_access',return_value=result),patch.object(self.app,'work',side_effect=self.work),patch.object(ui,'apply_contributor') as apply:
            self.app.refresh_setup_access()
        apply.assert_not_called();self.app.session.logout.assert_not_called()
        self.assertEqual(self.app.workspace,WORKSPACE);self.assertIsNone(self.app.plan)
        self.assertIn('not confirmed',self.app.access_summary.get())
        row=AccessHistory(self.app.access_history.path).get(self.target)
        self.assertFalse(row['ready']);self.assertIn('last_checked',row)

    def test_reconnect_uses_selected_tenant_and_arm_scope(self):
        self.app.vars['tenant'].set('wrong.example.com')
        with patch.object(self.app,'login') as login:self.app.reconnect_azure()
        error=login.call_args.kwargs['recovery']
        self.assertIsInstance(error,AuthenticationRequired)
        self.assertEqual(error.tenant,TENANT);self.assertEqual(error.resource,'arm')
        self.assertEqual(self.app.vars['tenant'].get(),TENANT)

    def test_missing_cached_account_does_not_block_new_login(self):
        self.app.vars['tenant'].set(TENANT)
        old=self.app.session;old.logout.side_effect=Stop('Account does not exist in the cache')
        new=MagicMock();new.env={}
        with patch.object(ui,'Session',return_value=new),patch.object(self.app,'work') as work:
            self.app.login()
        work.call_args.args[1]()
        new.login.assert_called_once_with(TENANT)

    def test_unrelated_old_logout_failure_still_stops(self):
        old=self.app.session;old.logout.side_effect=Stop('Disk permission failure')
        new=MagicMock();new.env={}
        with patch.object(ui,'Session',return_value=new),patch.object(self.app,'work') as work:self.app.login()
        with self.assertRaisesRegex(Stop,'Disk permission'):work.call_args.args[1]()
        new.login.assert_not_called()

    def test_client_change_clears_display_but_not_saved_history(self):
        self.app.access_history.record(self.target,'clicked');self.app.render_access_status()
        self.app.clear_selection()
        self.assertIn('Select a subscription',self.app.access_summary.get())
        self.assertIn('first_clicked',self.app.access_history.get(self.target))

    def test_saved_tenant_history_is_visible_before_signin(self):
        self.app.access_history.record(self.target,'clicked')
        self.app.session=None;self.app.clear_selection()
        self.app.vars['tenant'].set(TENANT)
        self.assertIn('First Contributor setup click',self.app.access_summary.get())
        self.assertIn('not a live access check',self.app.access_summary.get())
        self.app.vars['tenant'].set('99999999-9999-4999-8999-999999999999')
        self.assertNotIn('First Contributor setup click',self.app.access_summary.get())


if __name__=='__main__':unittest.main()
