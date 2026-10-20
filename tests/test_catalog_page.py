import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from sentinel_app import desktop_app as ui
from sentinel_app.repository_catalog import catalog
from test_repository_catalog import fixtures


class CatalogPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(ui, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.page = self.app.catalog_page
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def test_filter_and_assignment_state(self):
        self.page.snapshot = catalog(fixtures(), "synthetic", "one"); self.page.render()
        self.assertEqual(len(self.page.tree.get_children()), 1)
        self.page.tree.selection_set("0"); self.page.selected()
        self.assertIn("Disabled", self.page.details.get("1.0", "end"))
        self.page.search.set("missing"); self.assertFalse(self.page.tree.get_children())
        self.page.search.set(""); self.page.view.set("Rules")
        self.assertEqual(len(self.page.tree.get_children()), 1)

    def test_source_change_clears_previous_client_data(self):
        self.page.snapshot = catalog(fixtures(), "synthetic", "one"); self.page.render()
        self.page.source.set("another folder")
        self.assertIsNone(self.page.snapshot); self.assertFalse(self.page.tree.get_children())

    def test_stale_response_is_discarded(self):
        self.page.source.set("first folder"); callbacks = []
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: callbacks.append(done)):
            self.page.load()
        self.page.source.set("second folder")
        callbacks[0]((catalog(fixtures(), "synthetic", "one"), None))
        self.assertIsNone(self.page.snapshot)

    def test_failed_refresh_does_not_keep_old_success(self):
        self.page.source.set("first folder"); self.page.snapshot = catalog(fixtures(), "synthetic", "one")
        callbacks = []
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: callbacks.append(done)):
            self.page.load()
        callbacks[0]((None, "Access denied"))
        self.assertIsNone(self.page.snapshot)
        self.assertIn("unavailable", self.page.note.get())


    def test_rule_can_be_opened_for_editing_from_the_list(self):
        self.page.snapshot = catalog(fixtures(), "synthetic", "one"); self.page.render(); self.root.update()
        self.assertFalse(self.page.edit_bar.winfo_manager())  # Clients view: nothing to edit.
        self.page.view.set("Rules"); self.root.update()
        self.assertTrue(self.page.edit_bar.winfo_manager())
        self.assertEqual(str(self.page.edit_button.cget("state")), "disabled")
        self.page.tree.selection_set("0"); self.page.selected()
        self.assertEqual(str(self.page.edit_button.cget("state")), "normal")
        with patch.object(self.app.rule_builder, "edit_rule", return_value=True) as edit:
            self.page.open_row()  # Enter or double-click on the row.
        self.assertEqual(edit.call_args.args[0], self.page.visible[0]["raw"])
        self.root.update(); self.assertEqual(self.app.current_page(), "builder")
        self.app.set_busy(True)
        self.assertEqual(str(self.page.edit_button.cget("state")), "disabled")
        self.app.set_busy(False)


if __name__ == "__main__": unittest.main()
