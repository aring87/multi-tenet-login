import copy
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from sentinel_app import desktop_app as ui
from sentinel_app import ui_kit as kit
from sentinel_app.repository_catalog import catalog
from test_repository_catalog import fixtures


def library():
    """Two rules (one High, one Low) and two clients, one of which overrides a rule."""
    files = fixtures()
    files["rules/sentinel/zeta.yml"] = json.dumps(dict(id="22222222-2222-4222-8222-222222222222", name="Zeta rule",
                                                       severity="High", enabled=False)).encode()
    files["rules/sentinel/alpha.yml"] = json.dumps(dict(id="33333333-3333-4333-8333-333333333333", name="Alpha rule",
                                                        severity="Low", enabled=True)).encode()
    files["clients/other/workspace.yml"] = json.dumps(dict(version=1, target="other-primary", client="other", enabled=False,
                                                           azure=dict(workspace_name="other-law"),
                                                           rules=[dict(path="rules/sentinel/zeta.yml")])).encode()
    return catalog(files, "synthetic", "one")


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

    def restart(self, **settings):
        """Start the app again over the same data folder, with these saved settings."""
        self.cleanup()
        (Path(self.temp.name) / "settings.json").write_text(json.dumps(settings))
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.page = self.app.catalog_page

    def assigned(self):
        return [self.page.assigned.item(item, "values") for item in self.page.assigned.get_children()]

    def test_filter_and_assignment_state(self):
        self.page.view.set("Clients")
        self.page.snapshot = catalog(fixtures(), "synthetic", "one"); self.page.render()
        self.assertEqual(len(self.page.tree.get_children()), 1)
        self.page.tree.selection_set("0"); self.page.selected()
        # The client's override turns the rule off; the details list it per assignment.
        self.assertEqual(self.assigned(), [("Example sign in", "Disabled", "enabled: false")])
        self.assertIn(("Assigned", "1 rule"), [(k.cget("text"), v.cget("text")) for k, v in
                                               zip(self.page.facts.winfo_children()[::2], self.page.facts.winfo_children()[1::2])])
        self.page.search.set("missing"); self.page.render_soon.flush(); self.assertFalse(self.page.tree.get_children())
        self.page.search.set(""); self.page.view.set("Rules")
        self.assertEqual(len(self.page.tree.get_children()), 1)
        self.page.tree.selection_set("0"); self.page.selected()
        self.assertEqual(self.assigned(), [("example-primary", "Disabled", "enabled: false")])
        self.assertIn('"Example sign in"', self.page.details.get("1.0", "end"))

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
        # The reason is a danger banner in the open source form, with the way to fix it.
        self.assertTrue(self.page.error_banner.winfo_manager()); self.assertTrue(self.page.form.winfo_manager())
        self.assertIn("Access denied", self.page.error.get())
        self.assertEqual(self.page.error_banner.action_button.cget("text"), "Choose folder…")
        self.page.source.set("another folder")
        self.assertFalse(self.page.error_banner.winfo_manager())

    def test_source_folds_away_once_loaded(self):
        self.page.source.set("first folder"); callbacks = []
        self.assertTrue(self.page.form.winfo_manager())  # A folder that hasn't been read keeps the form open.
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: callbacks.append(done)):
            self.page.load()
        snapshot = catalog(fixtures(), "synthetic", "one"); snapshot["loaded_at"] = "2026-01-02 03:04:05 UTC"
        callbacks[0]((snapshot, None))
        self.assertTrue(self.page.compact.winfo_manager()); self.assertFalse(self.page.form.winfo_manager())
        self.assertTrue(self.page.status.get().startswith("first folder · loaded "))
        self.assertNotIn("Local folder", self.page.status.get())
        self.page.change_button.invoke()
        self.assertTrue(self.page.form.winfo_manager()); self.assertTrue(self.page.cancel_button.winfo_manager())
        self.page.cancel_button.invoke()
        self.assertTrue(self.page.compact.winfo_manager())

    def test_saved_github_library_stays_folded_until_it_fails(self):
        self.restart(github_owner="example", github_repo="detections")
        self.assertEqual(self.page.mode.get(), "GitHub repository")
        self.assertTrue(self.page.compact.winfo_manager())
        self.assertIn("example/detections", self.page.status.get())
        self.page.failed("GitHub read failed (HTTP 404)")
        self.assertTrue(self.page.form.winfo_manager())
        self.assertIn("GitHub couldn't find example/detections for your account", self.page.error.get())  # Plain advice.
        self.assertEqual(self.page.error_banner.action_button.cget("text"), "Go to Connect")
        self.page.source.set("example/detection")  # Fixing the name: the form stays open under the typing.
        self.assertTrue(self.page.form.winfo_manager()); self.assertFalse(self.page.error_banner.winfo_manager())
        self.assertFalse(self.page.compact.winfo_manager())

    def test_source_form_stays_open_while_a_repository_is_typed(self):
        self.page.mode.set("GitHub repository")  # Nothing saved on Connect: the form asks for a repository.
        self.assertTrue(self.page.form.winfo_manager())
        self.page.source.set("e")
        self.assertTrue(self.page.form.winfo_manager()); self.assertFalse(self.page.compact.winfo_manager())
        callbacks = []
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: callbacks.append(done)):
            self.page.load()
        callbacks[0]((library(), None))  # A successful read folds it away.
        self.assertTrue(self.page.compact.winfo_manager()); self.assertFalse(self.page.form.winfo_manager())

    def test_enter_in_the_search_runs_the_pending_filter_first(self):
        page = self.page; page.snapshot = library(); page.render()
        self.root.deiconify(); self.app.show_page("rules"); self.app.rule_view.set("repository"); self.root.update()
        page.search_entry.focus_force(); self.root.update()
        page.search.set("zeta")  # Enter at once, before the debounced filter has run.
        page.search_entry.event_generate("<Return>"); self.root.update()
        self.assertEqual([row["name"] for row in page.visible], ["Zeta rule"])
        self.assertEqual(page.tree.selection(), ("0",)); self.assertEqual(page.detail_card.title_var.get(), "Zeta rule")

    def test_list_takes_the_room_of_the_commands_until_a_library_loads(self):
        page = self.page
        self.root.deiconify(); self.app.show_page("rules"); self.app.rule_view.set("repository"); self.root.update()
        self.assertFalse(page.commands.winfo_manager()); self.assertFalse(page.filter_bar.winfo_manager())
        page.say("Set the rule library once on Connect, then come back here.")
        self.assertTrue(page.message.winfo_manager())  # Guidance still shows, right above the list.
        page.snapshot = library(); page.render(); self.root.update()
        self.assertTrue(page.commands.winfo_ismapped()); self.assertTrue(page.filter_bar.winfo_ismapped())
        order = page.commands.master.pack_slaves()
        self.assertLess(order.index(page.message), order.index(page.commands))
        self.assertLess(order.index(page.commands), order.index(page.table_frame))
        page.snapshot = None; page.render(); self.root.update()
        self.assertFalse(page.commands.winfo_manager()); self.assertFalse(page.filter_bar.winfo_manager())

    def test_opening_a_dialog_clears_old_guidance(self):
        page = self.page
        snapshot = dict(library(), repository="example/detections", revision="one", private=True, can_push=True)
        page.snapshot = snapshot; page.render()
        with patch("sentinel_app.catalog_page.filedialog.askopenfilename", return_value="missing.assignment.json"):
            page.open_assignment_review()
        self.assertIn("couldn't be opened", page.note.get())
        self.assertEqual(page.message.cget("bg"), kit.TONES["danger"][1])
        with patch("sentinel_app.catalog_page.filedialog.askopenfilename", return_value="saved.assignment.json"),                 patch("sentinel_app.catalog_page.load_assignment_request", return_value={}),                 patch("sentinel_app.catalog_page.AssignmentDialog") as dialog:
            page.open_assignment_review()
        dialog.assert_called_once()
        self.assertEqual(page.note.get(), ""); self.assertFalse(page.message.winfo_manager())
        page.note.set("Assignment submitted.")  # As AssignmentDialog reports a submission.
        self.assertEqual(page.message.cget("bg"), kit.TONES["info"][1])
        page.tree.selection_set("0"); page.selected()
        for name, button in (("AssignmentDialog", page.assign_button), ("PreviewDialog", page.preview_button)):
            page.say("Old guidance", tone="warning")
            with patch("sentinel_app.catalog_page." + name) as dialog: button.invoke()
            dialog.assert_called_once(); self.assertEqual(page.note.get(), "")

    def test_rule_can_be_opened_for_editing_from_the_list(self):
        self.page.view.set("Clients")
        self.page.snapshot = catalog(fixtures(), "synthetic", "one"); self.page.render(); self.root.update()
        self.assertFalse(self.page.edit_button.winfo_manager())  # Clients view: nothing to edit.
        self.page.view.set("Rules"); self.root.update()
        self.assertTrue(self.page.edit_button.winfo_manager())
        for button in (self.page.edit_button, self.page.assign_button, self.page.preview_button):
            self.assertEqual(str(button.cget("state")), "disabled")
        self.page.tree.selection_set("0"); self.page.selected()
        for button in (self.page.edit_button, self.page.assign_button, self.page.preview_button):
            self.assertEqual(str(button.cget("state")), "normal")  # Enabled on selection; the command explains limits.
        with patch.object(self.app.rule_builder, "edit_rule", return_value=True) as edit:
            self.page.open_row()  # Enter or double-click on the row.
        self.assertEqual(edit.call_args.args[0], self.page.visible[0]["raw"])
        self.root.update(); self.assertEqual(self.app.current_page(), "builder")
        self.app.set_busy(True, immediate=True)
        self.assertEqual(str(self.page.edit_button.cget("state")), "disabled")
        self.app.set_busy(False)

    def test_command_guidance_appears_above_the_commands(self):
        self.page.snapshot = catalog(fixtures(), "synthetic", "one"); self.page.render()
        self.page.tree.selection_set("0"); self.page.selected()
        with patch("sentinel_app.catalog_page.PreviewDialog") as dialog: self.page.preview_button.invoke()
        dialog.assert_not_called()
        self.assertTrue(self.page.message.winfo_manager()); self.assertIn("private GitHub", self.page.note.get())
        self.assertEqual(self.page.message.action_button.cget("text"), "Change source…")
        self.page.view.set("Clients")
        self.assertFalse(self.page.message.winfo_manager())

    def test_sorting_and_selection_survive_a_search(self):
        page = self.page
        self.assertEqual(page.tree.heading("name", "text"), "Rule ▲")  # Sorted by name from the start.
        page.snapshot = library(); page.render()
        names = lambda: [page.tree.set(item, "name") for item in page.tree.get_children()]
        self.assertEqual(names(), ["Alpha rule", "Example sign in", "Zeta rule"])
        page.tree.toggle_sort("info")  # Severity, most severe first.
        self.assertEqual(names(), ["Zeta rule", "Example sign in", "Alpha rule"])
        self.assertTrue(page.tree.heading("info", "text").endswith("▲"))
        page.tree.selection_set("1"); page.selected()  # Example sign in.
        page.search.set("zeta"); page.render_soon.flush()
        self.assertEqual(names(), ["Zeta rule"])
        self.assertEqual(page.tree.selection(), ()); self.assertEqual(page.detail_card.title_var.get(), "Details")
        page.search.set(""); page.render_soon.flush()
        page.tree.selection_set("2"); page.selected()  # Alpha rule, last by severity.
        page.search.set("alpha"); page.render_soon.flush()
        self.assertEqual(page.tree.selection(), ("0",))  # Found again by its file, now on the first row.
        self.assertEqual(page.visible[0]["name"], "Alpha rule"); self.assertEqual(page.detail_card.title_var.get(), "Alpha rule")
        page.view.set("Clients")  # The headings follow the view and keep the arrow.
        self.assertEqual(page.tree.heading("info", "text"), "Workspace ▲")
        self.assertEqual(page.view_bar.items["Clients"].cget("text"), "Clients 2")

    def test_issues_open_a_dialog_and_the_pill_opens_them(self):
        page = self.page; page.snapshot = dict(library(), issues=["clients/x.yml: broken"]); page.render()
        self.assertIn("1 issue", page.summary_pill.cget("text"))
        self.root.deiconify(); self.app.show_page("rules"); self.app.rule_view.set("repository"); self.root.update()
        with patch("sentinel_app.catalog_page.kit.alert") as alert:
            page.summary_pill.event_generate("<Button-1>"); self.root.update()
        alert.assert_called_once()
        self.assertEqual(alert.call_args.kwargs["details"], "clients/x.yml: broken")
        self.assertEqual(alert.call_args.kwargs["tone"], "warning")

    def test_library_menu_holds_the_library_wide_commands(self):
        page = self.page
        with patch.object(page.menu, "tk_popup") as popup: page.library_menu()
        popup.assert_called_once()
        labels = lambda: [page.menu.entrycget(i, "label") for i in range(page.menu.index("end") + 1)
                          if page.menu.type(i) == "command"]
        self.assertEqual(labels(), ["Library issues", "Open saved assignment…", "Preview & deployment history…"])
        self.assertEqual(page.menu.entrycget(0, "state"), "disabled")  # Nothing loaded yet.
        page.snapshot = dict(library(), issues=["a", "b"]); page.render()
        with patch.object(page.menu, "tk_popup"): page.library_menu()
        self.assertEqual(labels()[0], "Library issues (2)"); self.assertEqual(page.menu.entrycget(0, "state"), "normal")

    def test_row_menu_offers_copy_and_edit(self):
        page = self.page; page.snapshot = library(); page.render()
        menu = page.tree.build_menu("0")
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
        self.assertEqual(labels, ["Copy", "Edit in Rule builder", "Copy name", "Copy rule ID", "Copy file path"])
        page.row_menu("0")[3][1]()
        self.assertEqual(self.root.clipboard_get(), "33333333-3333-4333-8333-333333333333")

    def test_assignment_details_for_a_disabled_target(self):
        page = self.page; page.snapshot = library(); page.render()
        zeta = next(item for item in page.tree.get_children() if page.tree.set(item, "name") == "Zeta rule")
        page.tree.selection_set(zeta); page.selected()
        self.assertEqual(self.assigned(), [("other-primary (target disabled)", "Disabled", "None")])
        page.view.set("Clients"); page.tree.selection_set("1"); page.selected()  # 'other': no overrides.
        self.assertEqual(page.detail_card.title_var.get(), "other")
        self.assertTrue(page.state_hint.winfo_manager())


if __name__ == "__main__": unittest.main()
