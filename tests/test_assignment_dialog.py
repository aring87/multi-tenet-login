import copy
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app.assignment_dialog import AssignmentDialog
from sentinel_app.rule_assignments import AssignmentService, load_request
from sentinel_app.repository_catalog import GitHubReader
from test_rule_assignments import AssignmentGitHub, REPO


class AssignmentDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(ui, 'DATA', Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.addCleanup(self.cleanup)
        self.remote = AssignmentGitHub()
        self.snapshot = GitHubReader(lambda endpoint: self.remote('GET', endpoint)).load(REPO)
        self.dialog = AssignmentDialog(self.app, self.snapshot, self.snapshot['rules'][0])
        self.dialog.service = AssignmentService(self.remote)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call('after', 'info'): self.root.after_cancel(timer)
        self.root.destroy()

    def work(self, title, task, done, **kwargs): done(task())

    def prepare(self):
        self.dialog.select_shown()
        with patch.object(self.app, 'work', side_effect=self.work): self.dialog.prepare()
        self.assertIsNotNone(self.dialog.record, self.dialog.notice.get())

    def test_search_selects_visible_targets_and_keeps_hidden_selection(self):
        self.dialog.search.set('413'); self.dialog.select_shown()
        self.assertEqual(self.dialog.selected, {'413-workspace-commercial'})
        self.dialog.search.set('example-workspace'); self.dialog.select_shown()
        self.assertEqual(len(self.dialog.selected), 2)
        self.assertIn('1 hidden', self.dialog.count.get())
        self.dialog.clear(); self.assertFalse(self.dialog.selected)

    def test_review_is_read_only_and_state_changes_invalidate_it(self):
        self.prepare()
        self.assertFalse(self.remote.writes())
        text = self.dialog.text.get('1.0', 'end')
        self.assertIn('413-workspace-commercial', text)
        self.assertIn('example-workspace-commercial', text)
        self.assertIn('Requested rule state: Disabled', text)
        self.assertIn('before/clients/', text)
        self.dialog.enabled.set('Enabled')
        self.assertIsNone(self.dialog.record)
        self.assertEqual(str(self.dialog.submit_button.cget('state')), 'disabled')

    def test_submit_creates_one_pr_and_locks_the_review(self):
        self.prepare()
        with patch.object(self.app, 'work', side_effect=self.work): self.dialog.submit()
        writes = len(self.remote.writes())
        self.assertTrue(self.dialog.locked)
        self.assertIn('/pull/', self.dialog.notice.get())
        self.assertEqual(str(self.dialog.open_button.cget('state')), 'normal')
        self.dialog.clear(); self.assertEqual(len(self.dialog.selected), 2)
        with patch.object(self.app, 'work', side_effect=self.work): self.dialog.submit()
        self.assertEqual(len(self.remote.writes()), writes)
        with patch('sentinel_app.assignment_dialog.webbrowser.open') as opened: self.dialog.open_link()
        opened.assert_called_once_with(self.dialog.record['pull_request'])

    def test_failed_prepare_restores_controls_and_cannot_submit(self):
        self.dialog.clear()
        with patch.object(self.app, 'work', side_effect=self.work): self.dialog.prepare()
        self.assertIsNone(self.dialog.record)
        self.assertIn('Select between', self.dialog.notice.get())
        self.assertEqual(str(self.dialog.prepare_button.cget('state')), 'normal')
        self.assertEqual(str(self.dialog.submit_button.cget('state')), 'disabled')
        self.assertFalse(self.remote.writes())

    def test_saved_request_opens_locked_and_shows_original_diff(self):
        self.prepare()
        with patch.object(self.app, 'work', side_effect=self.work): self.dialog.submit()
        record = load_request(next(self.dialog.folder.glob('*.assignment.json')))
        self.dialog.close()
        self.dialog = AssignmentDialog(self.app, record=record)
        self.assertTrue(self.dialog.locked)
        self.assertIn('before/clients/', self.dialog.text.get('1.0', 'end'))
        self.assertIn('/pull/', self.dialog.notice.get())
        self.assertEqual(str(self.dialog.prepare_button.cget('state')), 'disabled')
        self.assertEqual(str(self.dialog.submit_button.cget('state')), 'disabled')

    def test_catalog_requires_private_remote_write_access_and_clean_catalog(self):
        page = self.app.catalog_page
        for change in ({'repository': None}, {'private': False}, {'can_push': False}, {'issues': ['broken']}):
            page.snapshot = dict(self.snapshot, **change)
            page.view.set('Rules'); page.render(); page.tree.selection_set('0')
            with patch('sentinel_app.catalog_page.AssignmentDialog') as opened: page.assign_selected()
            opened.assert_not_called()
        page.snapshot = copy.deepcopy(self.snapshot); page.render(); page.tree.selection_set('0')
        with patch('sentinel_app.catalog_page.AssignmentDialog') as opened: page.assign_selected()
        opened.assert_called_once()

    def test_controls_fit_at_minimum_window_size(self):
        self.root.deiconify(); self.dialog.window.deiconify()
        self.dialog.window.geometry('900x720'); self.root.update()
        right = self.dialog.window.winfo_rootx() + self.dialog.window.winfo_width()
        bottom = self.dialog.window.winfo_rooty() + self.dialog.window.winfo_height()
        for button in (self.dialog.prepare_button, self.dialog.submit_button, self.dialog.open_button,
                       self.dialog.close_button, self.dialog.all_button, self.dialog.clear_button,
                       self.dialog.state_box):
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), right)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)


if __name__ == '__main__': unittest.main()
