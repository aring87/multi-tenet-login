import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import desktop_app as ui
import rule_drafts as drafts
from repository_reviews import ReviewService, save_request
from review_history import ReviewHistoryDialog, list_requests
from test_repository_reviews import FakeGitHub, REPO, NEW
from test_rule_drafts import valid_form


def request(name="Synthetic saved review"):
    form = valid_form(); form["name"] = name
    return ReviewService(FakeGitHub()).prepare(REPO, NEW, drafts.validated_yaml(form)[0])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "review-requests"

    def test_missing_folder_is_empty_without_creating_it(self):
        self.assertEqual(list_requests(self.folder), ([], []))
        self.assertFalse(self.folder.exists())

    def test_listing_is_local_newest_first_and_does_not_infer_pr_state(self):
        first = request("Earlier rule"); second = request("Later rule")
        second["pull_request"] = "https://github.com/example/detections/pull/12"
        old = save_request(self.folder, first); new = save_request(self.folder, second)
        os.utime(old, (100, 100)); os.utime(new, (200, 200))
        with patch("repository_reviews.api", side_effect=AssertionError("No network")):
            rows, issues = list_requests(self.folder)
        self.assertFalse(issues)
        self.assertEqual([row["name"] for row in rows], ["Later rule", "Earlier rule"])
        self.assertEqual([row["status"] for row in rows], ["PR link saved", "Submission unconfirmed"])
        self.assertNotIn("content", rows[0])

    def test_invalid_file_does_not_hide_valid_records_or_change_files(self):
        good = save_request(self.folder, request())
        bad = self.folder / "broken.review.json"; bad.write_text("{bad json", encoding="utf-8")
        before = good.read_bytes()
        rows, issues = list_requests(self.folder)
        self.assertEqual(len(rows), 1); self.assertEqual(len(issues), 1)
        self.assertIn("broken.review.json", issues[0]); self.assertEqual(good.read_bytes(), before)
        self.assertEqual(bad.read_text(encoding="utf-8"), "{bad json")

    def test_tampered_request_is_reported(self):
        record = request(); record["plan"]["name"] = "Unexpected modification"
        save_request(self.folder, record)
        rows, issues = list_requests(self.folder)
        self.assertFalse(rows); self.assertEqual(len(issues), 1)

    def test_limit_reports_incomplete_list_and_keeps_newest(self):
        older = save_request(self.folder, request("Older")); os.utime(older, (100, 100))
        save_request(self.folder, request("Newer"))
        with patch("review_history.MAX_REQUESTS", 1): rows, issues = list_requests(self.folder)
        self.assertEqual([row["name"] for row in rows], ["Newer"])
        self.assertIn("newest 1", issues[0])

    def test_linked_file_is_not_read(self):
        save_request(self.folder, request())
        with patch("review_history.linked", side_effect=lambda path: path.suffix == ".json"), \
             patch("review_history.read_entry", side_effect=AssertionError("Must not read link")):
            rows, issues = list_requests(self.folder)
        self.assertFalse(rows); self.assertEqual(len(issues), 1)


class HistoryDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        p = patch.object(ui, "DATA", self.data); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.builder = self.app.rule_builder
        self.addCleanup(self.cleanup)
        self.record = request(); self.path = save_request(self.data / "review-requests", self.record)
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kwargs: done(task())):
            self.dialog = ReviewHistoryDialog(self.builder)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def select(self):
        item = self.dialog.table.get_children()[0]
        self.dialog.table.selection_set(item); self.dialog.selection_changed()

    def test_search_matches_repository_and_request_id(self):
        self.dialog.search.set(REPO.upper()); self.assertEqual(len(self.dialog.visible), 1)
        self.dialog.search.set(self.record["plan"]["request_id"]); self.assertEqual(len(self.dialog.visible), 1)
        self.dialog.search.set("absent"); self.assertFalse(self.dialog.visible)
        self.assertEqual(str(self.dialog.open_button.cget("state")), "disabled")

    def test_open_reuses_saved_request_and_preserves_unsaved_draft(self):
        self.builder.vars["name"].set("Unsaved analyst work")
        before = self.builder.form(); self.select()
        with patch("review_history.ReviewDialog") as open_dialog:
            self.dialog.open_selected()
        self.assertEqual(open_dialog.call_args.args[1]["plan"]["request_id"], self.record["plan"]["request_id"])
        self.assertEqual(self.builder.form(), before)

    def test_changed_file_is_revalidated_before_opening(self):
        self.select(); self.path.write_text("{}", encoding="utf-8")
        with patch("review_history.ReviewDialog") as open_dialog: self.dialog.open_selected()
        open_dialog.assert_not_called()
        self.assertIn("Could not open", self.dialog.notice.get())

    def test_busy_operation_blocks_reopen_and_close(self):
        self.select(); self.app.busy = True
        with patch("review_history.ReviewDialog") as open_dialog:
            self.dialog.open_selected(); self.dialog.close()
        open_dialog.assert_not_called(); self.assertTrue(self.dialog.window.winfo_exists())

    def test_actions_fit_minimum_width(self):
        self.root.deiconify(); self.dialog.window.deiconify()
        self.dialog.window.geometry("760x420"); self.root.update()
        edge = self.dialog.window.winfo_rootx() + self.dialog.window.winfo_width()
        bottom = self.dialog.window.winfo_rooty() + self.dialog.window.winfo_height()
        for button in (self.dialog.refresh_button, self.dialog.open_button, self.dialog.file_button, self.dialog.close_button):
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), edge)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)
            self.assertTrue(button.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
