import tempfile
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch
from sentinel_app import desktop_app as ui
from sentinel_app.desktop_backend import Stop
from test_desktop import TENANT, WORKSPACE


class ConnectionPageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(ui,'DATA',Path(self.temp.name));p.start();self.addCleanup(p.stop)
        self.root=tk.Tk();self.root.withdraw();self.app=ui.App(self.root);self.root.update()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for timer in self.root.tk.call('after','info'):self.root.after_cancel(timer)
        self.root.destroy()

    def test_new_session_hides_signin_progress_and_gates_workspace_controls(self):
        self.assertFalse(self.app.signin_panel.winfo_manager())
        self.assertEqual(str(self.app.subbox.cget('state')),'disabled')
        self.assertEqual(str(self.app.wsbox.cget('state')),'disabled')
        self.assertTrue(all(str(b.cget('state'))=='disabled' for b in self.app.workspace_actions))
        self.assertIn('Sign in',self.app.connection_note.get())

    def test_workspace_actions_enable_after_selection_and_clear_with_client(self):
        self.app.session=SimpleNamespace(account={'user':{'name':'analyst@example.com'}})
        self.app.subscriptions=[{'id':'synthetic'}];self.app.workspaces=[WORKSPACE];self.app.workspace=dict(WORKSPACE)
        self.app.sync_connection()
        self.assertTrue(all(str(b.cget('state'))=='normal' for b in self.app.workspace_actions))
        self.assertIn('analyst@example.com',self.app.connection_note.get())
        self.app.workspace_actions[1].invoke();self.root.update()
        self.assertEqual(self.app.current_page(),'rules')
        self.app.clear_selection()
        self.assertTrue(all(str(b.cget('state'))=='disabled' for b in self.app.workspace_actions))
        self.assertEqual(str(self.app.subbox.cget('state')),'disabled')

    def test_busy_state_never_reenables_workspace_actions_without_destination(self):
        self.app.set_busy(True);self.app.set_busy(False)
        self.assertEqual(str(self.app.subbox.cget('state')),'disabled')
        self.assertTrue(all(str(b.cget('state'))=='disabled' for b in self.app.workspace_actions))

    def test_long_errors_do_not_expand_footer_and_full_status_is_kept(self):
        message='Full original diagnostic\n'+'x'*1000
        self.app.status.set(message)
        self.assertEqual(self.app.status.get(),message)
        self.assertLessEqual(len(self.app.status_preview.get()),180)
        self.assertNotIn('\n',self.app.status_preview.get())

    def test_finishing_signin_hides_temporary_controls(self):
        self.app.signin_panel.pack(fill='x');self.app.signin_active=True
        self.app.finish_signin()
        self.assertFalse(self.app.signin_panel.winfo_manager())
        self.assertFalse(self.app.signin_active)


    def test_onboarding_access_tools_are_gone(self):
        for removed in ('offer_access_management','access_summary','setup_access','refresh_setup_access',
                        'azure_access_management','reconnect_azure','open_azure_portal'):
            self.assertFalse(hasattr(self.app,removed),removed)
        self.assertEqual([self.app.client_menu.entrycget(i,'label') for i in (0,2)],
                         ['Save this tenant to the client','Remove client…'])

    def test_enter_in_tenant_signs_in(self):
        tenant=next(w for w,_ in self.app.controls if isinstance(w,ui.ttk.Entry) and str(w.cget('textvariable'))==str(self.app.vars['tenant']))
        self.app.toggle_tenant(True);self.root.deiconify();tenant.focus_force();self.root.update()
        with patch.object(self.app,'login') as login:
            tenant.event_generate('<Return>');self.root.update()
        login.assert_called_once_with()

    def test_refresh_and_missing_subscription_appear_only_after_signin(self):
        self.assertEqual(str(self.app.sub_refresh.cget('state')),'disabled')
        self.assertFalse(self.app.missing_link.winfo_manager())
        self.assertFalse(self.app.signout_link.winfo_manager())
        self.app.session=SimpleNamespace(signed_in=True,account={'user':{'name':'analyst@example.com'}})
        self.app.sync_connection();self.app.render_session()
        self.assertEqual(str(self.app.sub_refresh.cget('state')),'normal')
        self.assertEqual(str(self.app.ws_refresh.cget('state')),'disabled')
        self.assertTrue(self.app.missing_link.winfo_manager())
        self.assertTrue(self.app.signout_link.winfo_manager())
        self.root.deiconify();self.root.update()
        with patch.object(self.app,'logout') as logout:
            self.app.signout_link.event_generate('<Button-1>');self.root.update()
        logout.assert_called_once_with()

    def test_refresh_is_available_after_successful_signin_without_subscriptions(self):
        self.app.session=MagicMock();self.app.session.signed_in=True
        self.assertFalse(self.app.subscriptions)
        with patch.object(self.app,'work') as work,patch.object(ui.messagebox,'showinfo') as warning:
            self.app.refresh_subscriptions()
        work.assert_called_once();warning.assert_not_called()

    def test_missing_cached_account_does_not_block_new_login(self):
        self.app.vars['tenant'].set(TENANT)
        old=self.app.session=MagicMock();old.logout.side_effect=Stop('Account does not exist in the cache')
        new=MagicMock();new.env={}
        with patch.object(ui,'Session',return_value=new),patch.object(ui,'create_handoff',return_value=None),\
             patch.object(self.app,'work') as work:
            self.app.login()
        work.call_args.args[1]()
        new.login.assert_called_once_with(TENANT)
        self.app.finish_signin()

    def test_unrelated_old_logout_failure_still_stops(self):
        old=self.app.session=MagicMock();old.logout.side_effect=Stop('Disk permission failure')
        new=MagicMock();new.env={}
        with patch.object(ui,'Session',return_value=new),patch.object(ui,'create_handoff',return_value=None),\
             patch.object(self.app,'work') as work:
            self.app.login()
        with self.assertRaisesRegex(Stop,'Disk permission'):work.call_args.args[1]()
        new.login.assert_not_called()
        self.app.finish_signin()


if __name__=='__main__':unittest.main()
