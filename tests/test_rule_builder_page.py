import tempfile
import tkinter as tk
from pathlib import Path
import unittest
from unittest.mock import patch

import desktop_app as ui
import rule_drafts as drafts
from test_rule_drafts import valid_form


class RuleBuilderPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(ui, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.page = self.app.rule_builder
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def test_validation_clears_after_field_or_query_change(self):
        self.page.load_form(valid_form()); self.assertIsNotNone(self.page.validate())
        self.page.vars["name"].set("Edited")
        self.assertEqual(self.page.preview.get("1.0", "end-1c"), "")
        self.assertTrue(self.page.dirty())
        self.page.validate(); self.page.texts["query"].insert("end", " | take 1"); self.root.update()
        self.assertEqual(self.page.preview.get("1.0", "end-1c"), "")

    def test_invalid_export_does_not_open_save_dialog(self):
        with patch("rule_builder_page.messagebox.showerror"), patch("rule_builder_page.filedialog.asksaveasfilename") as save:
            self.page.export(); save.assert_not_called()

    def test_cancel_new_keeps_unsaved_form(self):
        self.page.vars["name"].set("Keep this")
        identity = self.page.vars["id"].get()
        with patch("rule_builder_page.messagebox.askyesno", return_value=False): self.page.new()
        self.assertEqual(self.page.vars["name"].get(), "Keep this")
        self.assertEqual(self.page.vars["id"].get(), identity)

    def test_save_incomplete_then_reopen_and_export(self):
        path = Path(self.temp.name) / "draft.rule-draft.json"
        self.page.vars["name"].set("Incomplete")
        with patch("rule_builder_page.filedialog.asksaveasfilename", return_value=str(path)): self.page.save()
        self.assertFalse(self.page.dirty()); self.assertEqual(drafts.read_draft(path)["name"], "Incomplete")
        self.page.load_form(valid_form()); self.root.update()
        output = Path(self.temp.name) / "export.yml"
        with patch("rule_builder_page.filedialog.asksaveasfilename", return_value=str(output)): self.page.export()
        self.assertEqual(drafts.read_rule(output)["id"], self.page.vars["id"].get())

    def test_repository_edit_preserves_id_without_changing_source(self):
        rule = drafts.to_rule(valid_form()); rule["customDetails"] = {"Synthetic": "Value"}
        self.assertTrue(self.page.edit_rule(rule))
        self.page.vars["name"].set("Local change")
        self.assertEqual(rule["name"], "Synthetic test detection")
        self.assertEqual(drafts.to_rule(self.page.form())["customDetails"], rule["customDetails"])
        self.assertEqual(self.page.vars["id"].get(), rule["id"])

    def test_close_can_be_cancelled_before_logout(self):
        self.page.vars["name"].set("Unsaved")
        with patch("rule_builder_page.messagebox.askyesno", return_value=False), patch.object(self.root, "destroy") as destroy:
            self.app.close(); destroy.assert_not_called()

    def test_rule_builder_has_no_azure_signin_requirement(self):
        self.app.tabs.select(6); self.root.update()
        self.assertIn("Azure sign-in is not required", self.app.workspace_context.get())

    def test_busy_disables_authoring(self):
        form = self.page.form(); self.app.set_busy(True)
        self.page.new(); self.page.save(); self.page.export()
        self.assertEqual(self.page.form(), form)
        self.assertEqual(str(self.page.texts["query"].cget("state")), "disabled")

    def test_all_builder_steps_fit_at_minimum_width(self):
        from test_desktop_design import descendants
        self.root.deiconify(); self.root.geometry("1120x740"); self.app.tabs.select(6)
        for index in range(5):
            self.page.steps.select(index); self.root.update()
            canvas = self.app.page_canvases[6]
            for widget in descendants(self.app.page_contents[6]):
                if widget.winfo_ismapped() and isinstance(widget, (ui.ttk.Button, ui.ttk.Entry, ui.ttk.Combobox)):
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                         canvas.winfo_rootx() + canvas.winfo_width() + 2, str(widget))

    def test_tactic_picker_adds_without_duplicates(self):
        self.page.tactic_choice.set("CredentialAccess")
        self.page.add_tactic(); self.page.add_tactic()
        self.assertEqual(self.page.vars["tactics"].get(), "CredentialAccess")
