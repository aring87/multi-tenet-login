import copy
import tempfile
import tkinter as tk
from pathlib import Path
import unittest
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app import rule_drafts as drafts
from sentinel_app.desktop_theme import px
from sentinel_app.rule_review_dialog import ReviewDialog
from test_rule_drafts import valid_form
from test_repository_reviews import FakeGitHub, REPO, NEW
from sentinel_app.repository_reviews import ReviewService


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
        self.assertFalse(self.remote.pulls)  # A reason is required before anything is sent.
        self.assertIn("why the rule is changing", self.dialog.notice.get())
        self.dialog.reason.insert("1.0", "Tightened the threshold after false positives.")
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.submit()
        self.assertEqual(len(self.remote.pulls), 1)
        self.assertIn("Tightened the threshold", self.remote.pulls[0]["body"])
        self.assertEqual(str(self.dialog.submit_button.cget("state")), "disabled")
        self.assertEqual(str(self.dialog.link_button.cget("state")), "normal")

    def test_a_moved_library_rule_opens_where_it_will_be_saved(self):
        self.assertFalse(self.dialog.where_body.winfo_manager())
        error = "The original repository rule is missing or the destination changed. Start a new draft for a new rule."
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: done((None, error))):
            self.dialog.prepare()
        self.assertTrue(self.dialog.where_body.winfo_manager())
        self.assertIn("Where it will be saved", self.dialog.notice.get())

    def test_destination_change_invalidates_prepared_review(self):
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        self.dialog.path.set("rules/sentinel/other.yml")
        self.assertIsNone(self.dialog.record)
        self.assertEqual(str(self.dialog.submit_button.cget("state")), "disabled")
        self.assertEqual(self.dialog.text.get("1.0", "end-1c"), "")

    def test_failed_submission_can_retry_same_request(self):
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        identity = self.dialog.record["plan"]["request_id"]
        self.dialog.reason.insert("1.0", "Covers the new sign-in table.")
        self.remote.fail_after = "/pulls"
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.submit()
        self.assertIn("Recovery files", self.dialog.notice.get())
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.submit()
        self.assertEqual(self.dialog.record["plan"]["request_id"], identity)
        self.assertEqual(len(self.remote.pulls), 1)

    def test_draft_source_round_trip_preserves_update_context(self):
        source = {"repository": REPO, "revision": "a" * 40, "path": NEW}
        self.builder.load_form(valid_form(), source=source)
        self.builder.save()
        path = self.builder.recent_drafts()[0]
        self.assertEqual(drafts.read_draft_source(path), source)
        self.builder.load_form(drafts.new_form())
        with patch("sentinel_app.rule_builder_page.filedialog.askopenfilename", return_value=str(path)): self.builder.open_draft()
        self.assertEqual(self.builder.source, source)

    def test_review_form_is_a_snapshot_not_live_editable_inputs(self):
        before = copy.deepcopy(self.dialog.form)
        self.builder.vars["name"].set("Changed elsewhere")
        self.assertEqual(self.dialog.form, before)

    def test_blue_button_is_check_then_submit_and_open_in_github_sits_left(self):
        style = lambda button: str(button.cget("style"))
        self.assertEqual((style(self.dialog.prepare_button), style(self.dialog.submit_button)),
                         ("Primary.TButton", "Secondary.TButton"))
        self.assertEqual((style(self.dialog.link_button), self.dialog.link_button.pack_info()["side"]), ("Ghost.TButton", "left"))
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        self.assertEqual((style(self.dialog.prepare_button), style(self.dialog.submit_button)),
                         ("Secondary.TButton", "Primary.TButton"))
        self.assertIs(self.dialog.window._enter_target, self.dialog.submit_button)
        self.dialog.path.set("rules/sentinel/other.yml")  # A changed destination needs a new Check.
        self.assertIs(self.dialog.window._enter_target, self.dialog.prepare_button)
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        self.dialog.reason.insert("1.0", "Covers the new sign-in table.")
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.submit()
        self.assertIsNone(self.dialog.window._enter_target)
        self.assertEqual(style(self.dialog.submit_button), "Secondary.TButton")

    def test_diff_keeps_room_when_the_window_is_clamped(self):
        # 150% on a 1080p screen leaves about 640 of the 780 (96-DPI) pixels asked for.
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.prepare()
        self.root.deiconify(); self.dialog.window.deiconify()
        self.dialog.window.geometry(f"{px(900)}x{px(640)}"); self.root.update()
        line = self.dialog.text.tk.call("font", "metrics", self.dialog.text.cget("font"), "-linespace")
        self.assertGreaterEqual(self.dialog.text.winfo_height(), 4 * line)
        self.assertTrue(self.dialog.submit_button.winfo_ismapped())

    def test_review_buttons_fit_minimum_dialog_width(self):
        self.dialog.window.geometry("720x620"); self.root.update()
        edge = self.dialog.window.winfo_rootx() + self.dialog.window.winfo_width()
        for button in (self.dialog.prepare_button, self.dialog.submit_button,
                       self.dialog.link_button, self.dialog.close_button):
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), edge)
