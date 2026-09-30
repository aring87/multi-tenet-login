import copy
import tempfile
import tkinter as tk
from pathlib import Path
import unittest
from unittest.mock import patch

import desktop_app as ui
import rule_drafts as drafts
from rule_review_dialog import ReviewDialog
from test_rule_drafts import valid_form
from test_repository_reviews import FakeGitHub, REPO, NEW
from repository_reviews import ReviewService


class ReviewDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(ui, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.builder = self.app.rule_builder; self.builder.load_form(valid_form())
        self.dialog = ReviewDialog(self.builder)
        self.remote = FakeGitHub(); self.dialog.service = ReviewService(self.remote)
        self.dialog.repository.set(REPO); self.dialog.path.set(NEW)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def work(self, title, task, done, **kwargs):
        done(task())

    def test_prepare_is_read_only_and_submit_is_separate(self):
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        self.assertFalse(self.remote.writes())
        self.assertIsNotNone(self.dialog.record)
        self.assertIn("before/" + NEW, self.dialog.text.get("1.0", "end"))
        self.assertEqual(str(self.dialog.submit_button.cget("state")), "normal")
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.submit()
        self.assertEqual(len(self.remote.pulls), 1)
        self.assertEqual(str(self.dialog.submit_button.cget("state")), "disabled")
        self.assertEqual(str(self.dialog.link_button.cget("state")), "normal")

    def test_destination_change_invalidates_prepared_review(self):
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        self.dialog.path.set("rules/sentinel/other.yml")
        self.assertIsNone(self.dialog.record)
        self.assertEqual(str(self.dialog.submit_button.cget("state")), "disabled")
        self.assertEqual(self.dialog.text.get("1.0", "end-1c"), "")

    def test_failed_submission_can_retry_same_request(self):
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        identity = self.dialog.record["plan"]["request_id"]
        self.remote.fail_after = "/pulls"
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.submit()
        self.assertIn("Recovery files", self.dialog.notice.get())
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.submit()
        self.assertEqual(self.dialog.record["plan"]["request_id"], identity)
        self.assertEqual(len(self.remote.pulls), 1)

    def test_draft_source_round_trip_preserves_update_context(self):
        source = {"repository": REPO, "revision": "a" * 40, "path": NEW}
        self.builder.load_form(valid_form(), source=source)
        path = Path(self.temp.name) / "local.rule-draft.json"
        with patch("rule_builder_page.filedialog.asksaveasfilename", return_value=str(path)): self.builder.save()
        self.assertEqual(drafts.read_draft_source(path), source)
        self.builder.load_form(drafts.new_form())
        with patch("rule_builder_page.filedialog.askopenfilename", return_value=str(path)): self.builder.open_draft()
        self.assertEqual(self.builder.source, source)

    def test_review_form_is_a_snapshot_not_live_editable_inputs(self):
        before = copy.deepcopy(self.dialog.form)
        self.builder.vars["name"].set("Changed elsewhere")
        self.assertEqual(self.dialog.form, before)

    def test_review_buttons_fit_minimum_dialog_width(self):
        self.dialog.window.geometry("720x580"); self.root.update()
        edge = self.dialog.window.winfo_rootx() + self.dialog.window.winfo_width()
        for button in (self.dialog.prepare_button, self.dialog.submit_button,
                       self.dialog.link_button, self.dialog.close_button):
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), edge)
