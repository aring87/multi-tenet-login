import tempfile
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch
from sentinel_app import desktop_app as ui
from test_desktop import WORKSPACE


class ConnectionPageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(ui,'DATA',Path(self.temp.name));p.start();self.addCleanup(p.stop)
        self.root=tk.Tk();self.root.withdraw();self.app=ui.App(self.root);self.root.update()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for timer in self.root.tk.call('after','info'):self.root.after_cancel(timer)
        self.root.destroy()

    def test_new_session_has_one_signin_path_and_hides_advanced_tools(self):
        for group in (self.app.profile_options,self.app.signin_options,self.app.first_time_tools,
                      self.app.connection_support,self.app.workspace_details,self.app.signin_progress):
            self.assertFalse(group.winfo_manager())
        self.assertEqual(str(self.app.subbox.cget('state')),'disabled')
        self.assertEqual(str(self.app.wsbox.cget('state')),'disabled')
        self.assertTrue(all(str(b.cget('state'))=='disabled' for b in self.app.workspace_actions))

    def test_workspace_actions_enable_after_selection_and_clear_with_client(self):
        self.app.session=SimpleNamespace(account={'user':{'name':'analyst@example.com'}})
        self.app.subscriptions=[{'id':'synthetic'}];self.app.workspaces=[WORKSPACE];self.app.workspace=dict(WORKSPACE)
        self.app.sync_connection()
        self.assertTrue(all(str(b.cget('state'))=='normal' for b in self.app.workspace_actions))
        self.assertIn('analyst@example.com',self.app.connection_note.get())
        self.app.workspace_actions[0].invoke();self.assertEqual(self.app.tabs.index('current'),4)
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
        self.app.signin_progress.pack(fill='x');self.app.signin_active=True
        self.app.finish_signin()
        self.assertFalse(self.app.signin_progress.winfo_manager())
        self.assertFalse(self.app.signin_active)


if __name__=='__main__':unittest.main()
