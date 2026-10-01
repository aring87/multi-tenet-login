import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import desktop_app as ui
from preview_dialog import PreviewDialog
from preview_workflow import PreviewService
from repository_catalog import GitHubReader
from test_preview_workflow import PreviewGitHub, REPO, EXISTING


class PreviewDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(ui, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.addCleanup(self.cleanup)
        self.remote = PreviewGitHub(); self.remote.clients(3)
        disabled = json.loads(self.remote.files["clients/client-0/workspace.yml"])
        disabled["enabled"] = False; self.remote.files["clients/client-0/workspace.yml"] = json.dumps(disabled).encode()
        self.snapshot = GitHubReader(lambda endpoint: self.remote("GET", endpoint)).load(REPO)
        self.dialog = PreviewDialog(self.app, self.snapshot, self.snapshot["rules"][0])
        self.dialog.service = PreviewService(self.remote)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def work(self, title, task, done, **kwargs): done(task())

    def prepare(self):
        self.dialog.select_all()
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()

    def test_all_selects_only_eligible_and_filter_does_not_lose_selection(self):
        self.dialog.select_all()
        self.assertEqual(self.dialog.selected, {"client-1-primary", "example-primary"})
        self.dialog.search.set("example-primary")
        self.assertEqual(len(self.dialog.visible), 1)
        self.assertEqual(len(self.dialog.selected), 2)
        self.dialog.clear(); self.assertFalse(self.dialog.selected)

    def test_disabled_row_cannot_be_selected(self):
        item = next(k for k, v in self.dialog.visible.items() if v == "client-0-primary")
        self.dialog.toggle(item); self.assertFalse(self.dialog.selected)

    def test_prepare_reviews_exact_targets_without_running_and_selection_invalidates(self):
        self.prepare(); self.assertFalse(self.remote.dispatched)
        text = self.dialog.review.get("1.0", "end")
        self.assertIn("example-primary", text); self.assertIn("client-1-primary", text)
        self.assertNotIn("client-0-primary", text)
        self.assertEqual(str(self.dialog.start_button.cget("state")), "normal")
        self.dialog.clear(); self.assertIsNone(self.dialog.record)
        self.assertEqual(str(self.dialog.start_button.cget("state")), "disabled")

    def test_submission_then_refresh_is_explicit_and_uses_returned_run(self):
        self.prepare()
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.start()
        self.assertEqual(len(self.remote.dispatched), 1); self.assertTrue(self.dialog.locked)
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.start()
        self.assertEqual(len(self.remote.dispatched), 1)
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.refresh_status()
        self.assertEqual(self.dialog.record["results"][0]["state"], "success")
        self.dialog.runs.selection_set("0")
        with patch("preview_dialog.webbrowser.open") as open_url: self.dialog.open_run()
        open_url.assert_called_once_with("https://github.com/example/detections/actions/runs/101")

    def test_uncertain_dispatch_locks_submission_but_retains_actions_link(self):
        self.prepare(); self.remote.lose_response = True
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.start()
        self.assertTrue(self.dialog.locked)
        self.assertEqual(str(self.dialog.start_button.cget("state")), "disabled")
        self.assertIn("unconfirmed", self.dialog.record["results"][0]["state"])
        with patch("preview_dialog.webbrowser.open") as open_url: self.dialog.open_run()
        self.assertIn("/actions/workflows/", open_url.call_args.args[0])

    def test_catalog_local_snapshot_cannot_open_dispatch(self):
        page = self.app.catalog_page
        page.snapshot = dict(self.snapshot); page.snapshot.pop("repository")
        page.view.set("Rules"); page.render(); page.tree.selection_set("0")
        with patch("catalog_page.PreviewDialog") as dialog: page.preview_selected()
        dialog.assert_not_called(); self.assertIn("private GitHub", page.note.get())

    def test_controls_are_visible_at_minimum_size(self):
        self.root.deiconify(); self.dialog.window.deiconify()
        self.dialog.window.geometry("850x680"); self.root.update()
        bottom = self.dialog.window.winfo_rooty() + self.dialog.window.winfo_height()
        right = self.dialog.window.winfo_rootx() + self.dialog.window.winfo_width()
        for button in (self.dialog.prepare_button, self.dialog.start_button, self.dialog.deploy_button, self.dialog.status_button, self.dialog.open_button, self.dialog.close_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), right)


if __name__ == "__main__": unittest.main()
