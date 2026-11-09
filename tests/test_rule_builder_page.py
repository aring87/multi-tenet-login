import tempfile
import tkinter as tk
from pathlib import Path
import unittest
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app import rule_drafts as drafts
from sentinel_app import rule_schema
from sentinel_app.rule_builder_page import collect_issues, locate, tactic_label
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
        with patch("sentinel_app.rule_builder_page.messagebox.showerror"), patch("sentinel_app.rule_builder_page.filedialog.asksaveasfilename") as save:
            self.page.export(); save.assert_not_called()

    def test_cancel_new_keeps_unsaved_form(self):
        self.page.vars["name"].set("Keep this")
        identity = self.page.vars["id"].get()
        with patch("sentinel_app.rule_builder_page.messagebox.askyesno", return_value=False): self.page.new()
        self.assertEqual(self.page.vars["name"].get(), "Keep this")
        self.assertEqual(self.page.vars["id"].get(), identity)

    def test_save_incomplete_then_reopen_and_export(self):
        self.page.vars["name"].set("Incomplete")
        with patch("sentinel_app.rule_builder_page.filedialog.asksaveasfilename") as dialog: self.page.save()
        dialog.assert_not_called()  # Drafts save under their rule name without a prompt.
        path = Path(self.temp.name) / "drafts" / "incomplete.rule-draft.json"
        self.assertFalse(self.page.dirty()); self.assertEqual(drafts.read_draft(path)["name"], "Incomplete")
        self.assertEqual([p.name for p in self.page.recent_drafts()], ["incomplete.rule-draft.json"])
        self.page.load_form(valid_form()); self.root.update()
        output = Path(self.temp.name) / "export.yml"
        with patch("sentinel_app.rule_builder_page.filedialog.asksaveasfilename", return_value=str(output)): self.page.export()
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
        with patch("sentinel_app.rule_builder_page.messagebox.askyesno", return_value=False), patch.object(self.root, "destroy") as destroy:
            self.app.close(); destroy.assert_not_called()

    def test_rule_builder_has_no_azure_signin_requirement(self):
        self.app.show_page("builder"); self.root.update()
        # The context chip may name the client, but the builder never asks for an Azure sign-in.
        self.assertNotIn("sign in", self.app.workspace_context.get().lower())

    def test_busy_disables_authoring(self):
        form = self.page.form(); self.app.set_busy(True)
        self.page.new(); self.page.save(); self.page.export()
        self.assertEqual(self.page.form(), form)
        self.assertEqual(str(self.page.texts["query"].cget("state")), "disabled")

    def test_all_builder_steps_fit_at_minimum_width(self):
        from test_desktop_design import descendants
        self.root.deiconify(); self.root.geometry("1120x740"); self.app.show_page("builder")
        for index in range(5):
            self.page.steps.select(index); self.root.update()
            canvas = self.app.page_canvases[2]
            for widget in descendants(self.app.page_contents[2]):
                if widget.winfo_ismapped() and isinstance(widget, (ui.ttk.Button, ui.ttk.Entry, ui.ttk.Combobox)):
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                         canvas.winfo_rootx() + canvas.winfo_width() + 2, str(widget))
                if widget.winfo_ismapped() and isinstance(widget, ui.ttk.Checkbutton):  # Tactic names aren't cut off.
                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(), widget.cget("text"))

    def test_tactic_boxes_round_trip_the_tactics_field(self):
        page, ticked = self.page, self.page.tactic_vars
        labels = [box.cget("text") for box in page.widgets["tactics"].winfo_children()]
        self.assertEqual(sorted(labels), sorted(tactic_label(name) for name in rule_schema.TACTICS))
        self.assertIn("Defense Evasion", labels); self.assertIn("Command And Control", labels)
        page.vars["tactics"].set("Impact, NotATactic, DefenseEvasion"); self.root.update()
        self.assertEqual([name for name, var in ticked.items() if var.get()], ["DefenseEvasion", "Impact"])
        self.assertTrue(page.unknown_tactics.winfo_manager())
        self.assertEqual(page.unknown_text.get(), "Not a MITRE tactic: NotATactic")
        ticked["Execution"].set(True); ticked["Impact"].set(False)
        self.assertEqual(page.vars["tactics"].get(), "NotATactic, DefenseEvasion, Execution")  # Unknown values are kept.
        ticked["Execution"].set(True)
        self.assertEqual(page.vars["tactics"].get(), "NotATactic, DefenseEvasion, Execution")  # No duplicates.
        page.remove_unknown_tactics(); self.root.update()
        self.assertEqual(page.vars["tactics"].get(), "DefenseEvasion, Execution")
        self.assertFalse(page.unknown_tactics.winfo_manager())
        # Drafts keep the comma-separated field unchanged.
        page.vars["name"].set("Tactics round trip"); page.save()
        saved = drafts.read_draft(Path(self.temp.name) / "drafts" / "tactics-round-trip.rule-draft.json")
        self.assertEqual(saved["tactics"], "DefenseEvasion, Execution")
        self.assertEqual(drafts.to_rule(page.form())["tactics"], ["DefenseEvasion", "Execution"])
        rule = drafts.to_rule(valid_form()); rule["tactics"] = ["Persistence", "Bogus"]
        self.assertTrue(page.edit_rule(rule)); self.root.update()
        self.assertEqual([name for name, var in ticked.items() if var.get()], ["Persistence"])
        self.assertEqual(page.unknown_text.get(), "Not a MITRE tactic: Bogus")
        self.assertIsNone(page.validate())
        self.assertEqual(list(page.flagged), ["tactics"]); self.assertIn("Remove", page.flagged["tactics"])
        grid = page.widgets["tactics"]
        self.assertEqual((grid.cget("highlightbackground"), int(grid.cget("highlightthickness"))), (ui.COLORS["danger"], 2))
        ticked["Persistence"].set(False)  # Any edit of the tactics clears their flag.
        self.assertEqual(page.flagged, {}); self.assertEqual(int(grid.cget("highlightthickness")), 1)

    def test_quick_checks_agree_with_the_full_check(self):
        for changes in ({}, dict(kind="NRT", triggerThreshold="x"), dict(allow_missing_mitre=True, tactics="", techniques="",
                                                                          subTechniques=""),
                        dict(owner="soc.team@contoso-1"), dict(techniques="T0849, T1562", tactics="Impact, Execution")):
            form = valid_form(); form.update(changes)
            self.assertEqual(collect_issues(form), [], changes); drafts.validated_yaml(form)
        for changes in (dict(name=" "), dict(description=""), dict(description="Too short"), dict(owner=""),
                        dict(owner="SOC Team"), dict(query=""), dict(query="SigninLogs |"), dict(triggerThreshold="x"),
                        dict(queryFrequency="PT1M"), dict(queryPeriod="P15D"), dict(queryFrequency="P1D", queryPeriod="PT1H"),
                        dict(tactics="Bogus"), dict(tactics=""), dict(techniques="T1562.004"), dict(techniques="T0000"),
                        dict(subTechniques="T1562"), dict(techniques="T1003"), dict(status="retired", enabled=True)):
            form = valid_form(); form.update(changes)
            flagged = dict(collect_issues(form))
            self.assertTrue(flagged, changes)
            with self.assertRaises(ValueError, msg=str(changes)) as raised: drafts.validated_yaml(form)
            # The full check's first complaint is about a field the quick checks flagged too.
            self.assertIn(locate(form, str(raised.exception))[0], flagged, changes)

    def test_check_flags_every_problem_at_once_and_marks_the_steps(self):
        page = self.page; status = page.stepper.status
        form = valid_form(); form.update(name="", owner="Not Valid", query="SigninLogs |", tactics="DefenseEvasion, Bogus")
        page.load_form(form); self.root.update()
        with patch("sentinel_app.rule_builder_page.messagebox.showerror") as dialog: self.assertIsNone(page.validate())
        dialog.assert_not_called()
        self.assertEqual(list(page.flagged), ["name", "owner", "query", "tactics"])
        self.assertEqual(page.steps.index("current"), 0)  # The first failing step opens.
        self.assertEqual(page.note.get(), "Needs attention — 4 fields: Rule name, Owner (Basics); Query (Query & schedule); "
                                          "Tactics (MITRE ATT&CK)")
        self.assertEqual(status, {0: ("error", "2 issues"), 1: ("error", "1 issue"), 2: ("error", "1 issue")})
        self.assertEqual(str(page.widgets["owner"].cget("style")), "Error.TEntry")
        self.assertTrue(page.widgets["query"].master.error)
        self.assertEqual(set(page.flag_labels), {"name", "owner", "query", "tactics"})
        page.vars["owner"].set("soc-team")  # An edit clears only that field and updates its step.
        self.assertEqual(list(page.flagged), ["name", "query", "tactics"])
        self.assertEqual(str(page.widgets["owner"].cget("style")), "TEntry")
        self.assertEqual(status, {0: ("error", "1 issue"), 1: ("error", "1 issue"), 2: ("error", "1 issue")})
        self.assertTrue(page.note.get().startswith("Needs attention — 3 fields"))
        page.vars["name"].set("Fixed name")
        self.assertNotIn(0, status); self.assertEqual(len(status), 2)
        kit_text = page.texts["query"]; kit_text.delete("1.0", "end"); kit_text.insert("1.0", "SigninLogs"); self.root.update()
        page.vars["tactics"].set("DefenseEvasion")
        self.assertEqual((page.flagged, status), ({}, {}))
        self.assertIsNotNone(page.validate())
        self.assertEqual(status, {index: ("done", None) for index in range(4)})
        self.assertEqual(page.steps.index("current"), 4)
        page.vars["name"].set("Edited again")  # The ticks belong to the check that just went stale.
        self.assertEqual(status, {})

    def test_back_and_next_follow_the_current_step(self):
        page = self.page
        self.root.deiconify(); self.app.show_page("builder"); self.root.update()
        self.assertFalse(page.back_button.winfo_ismapped())
        self.assertEqual(page.next_button.cget("text"), "Next: Query & schedule  →")
        page.next_button.invoke(); self.root.update()
        self.assertEqual(page.steps.index("current"), 1)
        self.assertTrue(page.back_button.winfo_ismapped()); self.assertEqual(page.back_button.cget("text"), "←  Back")
        self.assertEqual(page.next_button.cget("text"), "Next: MITRE ATT&CK  →")
        page.steps.select(4); self.root.update()
        self.assertFalse(page.next_button.winfo_ismapped()); self.assertTrue(page.back_button.winfo_ismapped())
        page.back_button.invoke(); self.root.update()
        self.assertEqual(page.steps.index("current"), 3)
        entry = page.widgets["name"]
        entry.event_generate("<Control-Prior>"); self.root.update()
        self.assertEqual(page.steps.index("current"), 2)
        entry.event_generate("<Control-Next>"); self.root.update()
        self.assertEqual(page.steps.index("current"), 3)
        self.app.show_page("connect"); self.root.update()
        entry.event_generate("<Control-Next>"); self.root.update()
        self.assertEqual(page.steps.index("current"), 3)  # Only while the builder is showing.
        self.app.set_busy(True); page.step_by(1)
        self.assertEqual(page.steps.index("current"), 3)

    def test_edit_after_submitting_drops_open_in_github(self):
        self.page.load_form(valid_form())
        self.page.submitted("https://github.com/example/detections/pull/7")
        self.assertEqual(self.page.banner.action_button.cget("text"), "Open in GitHub")
        self.assertTrue(self.page.state_pill.cget("text").endswith("Submitted"))
        self.page.vars["name"].set("Edited after submitting")
        self.assertIsNone(self.page.banner.action_button)
        self.assertIsNone(self.page.submitted_url)
        self.page.submitted("https://github.com/example/detections/pull/8")
        self.page.load_form(valid_form())  # New rule and Open… load a form the same way.
        self.assertIsNone(self.page.banner.action_button)

    def test_state_pill_says_draft_while_the_banner_says_unsaved(self):
        self.page.load_form(valid_form())
        self.assertTrue(self.page.state_pill.cget("text").endswith("New draft"))
        self.page.vars["name"].set("Edited")
        self.assertTrue(self.page.note.get().startswith("Unsaved changes"))
        self.assertTrue(self.page.state_pill.cget("text").endswith("Draft"))
        self.assertNotIn("Unsaved", self.page.state_pill.cget("text"))

    def test_footer_slot_leaves_with_the_builder_and_lines_up_with_the_content(self):
        self.root.deiconify(); self.root.geometry("1120x740")
        self.app.show_page("builder"); self.root.update()
        slot, footer, layout = self.app.footer_slot, self.page.footer.frame, self.page.layout
        self.assertTrue(slot.winfo_ismapped())
        self.assertEqual((footer.winfo_rootx(), footer.winfo_rootx() + footer.winfo_width()),
                         (layout.winfo_rootx(), layout.winfo_rootx() + layout.winfo_width()))
        self.app.show_page("connect"); self.root.update()  # The page change arrives as a queued virtual event.
        self.assertFalse(slot.winfo_ismapped()); self.assertFalse(slot.winfo_manager())
        canvas, status_bar, tabs = self.app.page_canvases[0], self.app.activity_badge.master.master, self.app.tabs
        # No band: the page notebook reaches the status bar, and the canvas fills it (inside its 2px client border).
        self.assertEqual(tabs.winfo_rooty() + tabs.winfo_height(), status_bar.winfo_rooty())
        self.assertLessEqual(status_bar.winfo_rooty() - (canvas.winfo_rooty() + canvas.winfo_height()), 2)
        self.app.show_page("builder"); self.root.update()
        self.assertTrue(footer.winfo_ismapped())
        self.assertEqual(slot.winfo_rooty() + slot.winfo_height(), status_bar.winfo_rooty())

    def test_mouse_wheel_over_a_drop_down_leaves_its_value(self):
        units = next(w for w in self.page.widgets["queryFrequency"].master.winfo_children() if isinstance(w, ui.ttk.Combobox))
        for combo, var in ((units, None), (self.page.widgets["status"], self.page.vars["status"])):
            before = combo.get(), var.get() if var else None
            combo.event_generate("<MouseWheel>", delta=-120); self.root.update()
            self.assertEqual((combo.get(), var.get() if var else None), before)

    def test_schedule_is_in_view_on_the_query_step_at_minimum_size(self):
        self.root.deiconify(); self.root.geometry("1120x740"); self.app.show_page("builder")
        self.page.github_checked("analyst", None)  # Signed in, as analysts usually are.
        self.page.load_form(valid_form()); self.page.steps.select(1); self.root.update()
        canvas = self.app.page_canvases[2]
        self.assertEqual(canvas.yview()[0], 0.0)
        for key in ("queryFrequency", "queryPeriod", "triggerOperator", "triggerThreshold"):
            widget = self.page.widgets[key]
            self.assertTrue(widget.winfo_ismapped(), key)
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), canvas.winfo_rooty() + canvas.winfo_height(), key)
