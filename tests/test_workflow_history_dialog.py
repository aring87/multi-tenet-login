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

    def search(self,text):self.dialog.search.set(text);self.dialog.filter.flush()

    def test_opening_history_is_local_and_filters_by_workspace_and_mode(self):
        self.assertEqual(len(self.dialog.visible),1);self.assertFalse(self.remote.calls)
        self.search('example-primary');self.assertEqual(len(self.dialog.visible),1)
        self.dialog.mode.set('Deploy');self.assertFalse(self.dialog.visible)
        self.dialog.mode.set('Preview');self.assertEqual(len(self.dialog.visible),1)
        self.dialog.search.set('nonexistent');self.assertEqual(len(self.dialog.visible),1)  # Filters after a pause.
        self.dialog.filter.flush();self.assertFalse(self.dialog.visible)
        self.assertEqual(str(self.dialog.status_button.cget('state')),'disabled')

    def test_refresh_status_is_blue_only_while_a_request_is_selected(self):
        style=lambda button:str(button.cget('style'))
        self.assertEqual(style(self.dialog.status_button),'Secondary.TButton');self.assertIsNone(self.dialog.window._enter_target)
        self.select()
        self.assertEqual(style(self.dialog.status_button),'Primary.TButton')
        self.assertIs(self.dialog.window._enter_target,self.dialog.status_button)
        for button in (self.dialog.open_button,self.dialog.reload_button,self.dialog.file_button):
            self.assertEqual((style(button),button.pack_info()['side']),('Ghost.TButton','left'))

    def test_headings_sort_and_the_order_survives_a_search(self):
        record=dict(self.dialog.rows[0]['record'],created_at='2000-01-01T00:00:00+00:00',request_id='older')
        self.dialog.rows.append(dict(file='older.preview.json',record=record,mode='preview'));self.dialog.render()
        created=lambda:[self.dialog.table.set(item,'created') for item in self.dialog.table.get_children()]
        self.dialog.table.toggle_sort('created')
        self.assertEqual(created(),sorted(created()));self.assertEqual(self.dialog.table.heading('created','text'),'Requested (UTC) ▲')
        self.dialog.table.toggle_sort('created');self.assertEqual(created(),sorted(created(),reverse=True))
        self.search('example');self.assertEqual(created(),sorted(created(),reverse=True))

    def test_selected_request_shows_targets_inputs_and_original_revision(self):
        self.assertFalse(self.dialog.runs_frame.winfo_manager())  # The details have the room until there are runs.
        self.select();text=self.dialog.detail.get('1.0','end')
        self.assertTrue(self.dialog.runs_frame.winfo_manager());self.assertEqual(int(self.dialog.runs.cget('height')),2)
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
