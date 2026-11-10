import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app import ui_kit as kit
from sentinel_app.assignment_dialog import mixed_box
from sentinel_app.preview_dialog import PreviewDialog
from sentinel_app.preview_workflow import PreviewService
from sentinel_app.repository_catalog import GitHubReader
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

    def item(self, target):
        return next(k for k, v in self.dialog.visible.items() if v == target)

    def box(self, item):
        return str(self.root.tk.splitlist(self.dialog.clients.item(item, "image"))[0])

    def heading_box(self):
        return str(self.root.tk.splitlist(self.dialog.clients.heading("#0", "image"))[0])

    def test_all_selects_only_eligible_and_filter_does_not_lose_selection(self):
        self.dialog.select_all()
        self.assertEqual(self.dialog.selected, {"client-1-primary", "example-primary"})
        self.dialog.search.set("example-primary")
        self.assertEqual(len(self.dialog.visible), 3)  # The list filters once typing pauses…
        self.dialog.filter.flush()
        self.assertEqual(len(self.dialog.visible), 1)
        self.assertEqual(len(self.dialog.selected), 2)
        self.dialog.clear(); self.assertFalse(self.dialog.selected)

    def test_disabled_row_cannot_be_selected(self):
        item = self.item("client-0-primary")
        self.assertEqual(self.box(item), str(kit.checkbox("disabled", self.root)))
        self.dialog.toggle(item); self.assertFalse(self.dialog.selected)

    def test_rows_tick_in_place_and_heading_box_selects_shown_eligible_rows(self):
        self.assertNotIn("selected", self.dialog.clients.cget("columns"))
        rows = self.dialog.clients.get_children()
        item = self.item("example-primary")
        self.dialog.toggle(item)
        self.assertEqual(self.dialog.clients.get_children(), rows)  # Not rebuilt.
        self.assertEqual(self.box(item), str(kit.checkbox("on", self.root)))
        self.assertEqual(self.heading_box(), str(mixed_box(self.root)))
        self.assertEqual(self.dialog.count.get(), "1 selected of 2 eligible")
        self.dialog.toggle_shown()
        self.assertEqual(self.dialog.selected, {"client-1-primary", "example-primary"})
        self.assertEqual(self.heading_box(), str(kit.checkbox("on", self.root)))
        self.dialog.toggle_shown()
        self.assertFalse(self.dialog.selected)
        self.assertEqual(self.box(item), str(kit.checkbox("off", self.root)))

    def test_blue_button_follows_the_next_step_and_runs_show_once_started(self):
        style = lambda button: str(button.cget("style"))
        self.assertEqual(style(self.dialog.prepare_button), "Primary.TButton")
        self.assertEqual(style(self.dialog.start_button), "Secondary.TButton")
        self.assertIs(self.dialog.window._enter_target, self.dialog.prepare_button)
        self.assertEqual(str(self.dialog.status_button.cget("style")), "Ghost.TButton")
        self.assertFalse(self.dialog.runs_frame.winfo_manager())
        self.prepare()
        self.assertEqual((style(self.dialog.start_button), style(self.dialog.prepare_button)),
                         ("Primary.TButton", "Secondary.TButton"))
        self.assertIs(self.dialog.window._enter_target, self.dialog.start_button)
        self.assertFalse(self.dialog.runs_frame.winfo_manager())
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.start()
        self.assertTrue(self.dialog.runs_frame.winfo_manager())
        self.assertIsNone(self.dialog.window._enter_target)  # Refresh status comes next; nothing is blue.
        self.assertEqual(style(self.dialog.start_button), "Secondary.TButton")
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.refresh_status()
        self.assertEqual(style(self.dialog.deploy_button), "Primary.TButton")

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
        with patch("sentinel_app.preview_dialog.webbrowser.open") as open_url: self.dialog.open_run()
        open_url.assert_called_once_with("https://github.com/example/detections/actions/runs/101")

    def test_uncertain_dispatch_locks_submission_but_retains_actions_link(self):
        self.prepare(); self.remote.lose_response = True
        with patch.object(self.app, "work", side_effect=self.work): self.dialog.start()
        self.assertTrue(self.dialog.locked)
        self.assertEqual(str(self.dialog.start_button.cget("state")), "disabled")
        self.assertIn("unconfirmed", self.dialog.record["results"][0]["state"])
        with patch("sentinel_app.preview_dialog.webbrowser.open") as open_url: self.dialog.open_run()
        self.assertIn("/actions/workflows/", open_url.call_args.args[0])

    def test_catalog_local_snapshot_cannot_open_dispatch(self):
        page = self.app.catalog_page
        page.snapshot = dict(self.snapshot); page.snapshot.pop("repository")
        page.view.set("Rules"); page.render(); page.tree.selection_set("0")
        with patch("sentinel_app.catalog_page.PreviewDialog") as dialog: page.preview_selected()
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


    def test_buttons_read_as_steps_towards_close_and_enter_in_search_starts_nothing(self):
        dialog = self.dialog
        self.root.deiconify(); dialog.window.deiconify(); self.root.update()
        order = [b.winfo_x() for b in (dialog.deploy_button, dialog.start_button, dialog.prepare_button, dialog.close_button)]
        self.assertEqual(order, sorted(order))  # [Deploy…] [Start previews] [Check] [Close]
        self.assertEqual(dialog.open_button.cget("text"), "Open in GitHub")
        self.prepare()
        self.assertIs(dialog.window._enter_target, dialog.start_button)  # The blue button moved on; Enter follows it,
        dialog.search_box.focus_force(); self.root.update()
        with patch.object(self.app, "work", side_effect=self.work):
            dialog.search.set("zzz-no-such-client")
            dialog.search_box.event_generate("<Return>"); self.root.update()
            dialog.search.set("client-1"); dialog.search_box.event_generate("<Return>"); self.root.update()
            self.assertIs(self.root.focus_get(), dialog.clients)
            dialog.clients.event_generate("<Return>"); self.root.update()
        self.assertEqual(self.remote.dispatched, [])  # but never from a search box or the client list.

if __name__ == "__main__": unittest.main()
