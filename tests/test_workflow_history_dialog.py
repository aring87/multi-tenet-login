from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app.preview_workflow import PreviewService
from sentinel_app.workflow_history import WorkflowHistoryService
from sentinel_app.workflow_history_dialog import WorkflowHistoryDialog
from test_preview_workflow import PreviewGitHub, REPO, EXISTING


class WorkflowHistoryDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(ui,'DATA',Path(self.temp.name));p.start();self.addCleanup(p.stop)
        self.root=tk.Tk();self.root.withdraw();self.app=ui.App(self.root);self.root.update()
        self.addCleanup(self.cleanup)
        self.remote=PreviewGitHub();service=PreviewService(self.remote)
        record=service.prepare(REPO,self.remote.head,EXISTING,['example-primary'])
        service.dispatch(record,self.app.rule_builder.data_dir/'preview-requests')
        self.remote.calls.clear()
        with patch.object(self.app,'work',side_effect=self.work):self.dialog=WorkflowHistoryDialog(self.app)
        self.dialog.service=WorkflowHistoryService(self.remote)

    def cleanup(self):
        for timer in self.root.tk.call('after','info'):self.root.after_cancel(timer)
        self.root.destroy()

    def work(self,title,task,done,**kw):done(task())

    def select(self):self.dialog.table.selection_set('0');self.dialog.selected()

    def test_opening_history_is_local_and_filters_by_workspace_and_mode(self):
        self.assertEqual(len(self.dialog.visible),1);self.assertFalse(self.remote.calls)
        self.dialog.search.set('example-primary');self.assertEqual(len(self.dialog.visible),1)
        self.dialog.mode.set('Deploy');self.assertFalse(self.dialog.visible)
        self.dialog.mode.set('Preview');self.assertEqual(len(self.dialog.visible),1)
        self.dialog.search.set('nonexistent');self.assertFalse(self.dialog.visible)
        self.assertEqual(str(self.dialog.status_button.cget('state')),'disabled')

    def test_selected_request_shows_targets_inputs_and_original_revision(self):
        self.select();text=self.dialog.detail.get('1.0','end')
        self.assertIn('example-primary',text);self.assertIn('"mode": "preview"',text)
        self.assertIn('a'*40,text);self.assertEqual(len(self.dialog.runs.get_children()),1)

    def test_refresh_updates_selected_request_without_dispatching(self):
        self.select()
        with patch.object(self.app,'work',side_effect=self.work):self.dialog.refresh_status()
        self.assertIn('Status refreshed',self.dialog.notice.get())
        self.assertIn('success',str(self.dialog.runs.item('0','values')))
        self.assertTrue(all(c[0]=='GET' for c in self.remote.calls))
        self.dialog.runs.selection_set('0')
        with patch('sentinel_app.workflow_history_dialog.webbrowser.open') as opened:self.dialog.open_run()
        opened.assert_called_once_with('https://github.com/'+REPO+'/actions/runs/101')

    def test_import_invalid_file_preserves_current_history(self):
        path=Path(self.temp.name)/'bad.preview.json';path.write_text('{bad')
        with patch('sentinel_app.workflow_history_dialog.filedialog.askopenfilename',return_value=str(path)):
            self.dialog.open_file()
        self.assertIn('Receipt unavailable',self.dialog.notice.get())
        self.assertEqual(len(self.dialog.rows),1);self.assertFalse(self.remote.calls)

    def test_failed_refresh_keeps_receipt_visible_and_restores_controls(self):
        self.select()
        with patch.object(self.dialog.service,'refresh',side_effect=RuntimeError('Access unavailable')),patch.object(self.app,'work',side_effect=self.work):
            self.dialog.refresh_status()
        self.assertEqual(self.dialog.notice.get(),'Access unavailable')
        self.assertEqual(str(self.dialog.status_button.cget('state')),'normal')
        self.assertEqual(len(self.dialog.runs.get_children()),1)

    def test_controls_fit_at_minimum_size(self):
        self.root.deiconify();self.dialog.window.deiconify();self.dialog.window.geometry('920x720');self.root.update()
        right=self.dialog.window.winfo_rootx()+self.dialog.window.winfo_width()
        bottom=self.dialog.window.winfo_rooty()+self.dialog.window.winfo_height()
        for button in self.dialog.buttons:
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),right)
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),bottom)


if __name__=='__main__':unittest.main()
