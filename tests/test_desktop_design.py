"""Offline checks for the redesigned desktop's navigation and state."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from test_desktop import WORKSPACE


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(ui,"DATA",Path(self.temp.name));p.start();self.addCleanup(p.stop)
        self.root=tk.Tk();self.app=ui.App(self.root);self.root.geometry("1120x740")
        self.root.update()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for timer in self.root.tk.call("after","info"):
            self.root.after_cancel(timer)
        self.root.destroy()

    def test_workspace_context_updates_and_clears(self):
        self.app.workspace=dict(WORKSPACE);self.app.identity.set("Synthetic workspace")
        self.assertIn(WORKSPACE["workspace_name"],self.app.workspace_context.get())
        self.app.clear_selection()
        self.assertIn("No workspace selected",self.app.workspace_context.get())

    def test_busy_state_disables_controls_and_restores_readonly_fields(self):
        self.app.set_busy(True)
        self.assertIn("Working",self.app.activity_badge.cget("text"))
        self.assertEqual(str(self.app.subbox.cget("state")),"disabled")
        self.app.subscriptions=[{"id":"synthetic"}]
        self.app.set_busy(False)
        self.assertEqual(str(self.app.subbox.cget("state")),"readonly")
        self.assertIn("Ready",self.app.activity_badge.cget("text"))

    def test_rules_filter_and_clear_on_client_change(self):
        page=self.app.rules_page
        page.rows=[dict(name="one",properties=dict(displayName="First",enabled=True)),
                   dict(name="two",properties=dict(displayName="Second",enabled=False))]
        page.render();self.assertEqual(len(page.tree.get_children()),2)
        page.filter.set("Disabled");self.assertEqual(page.tree.get_children(),("1",))
        page.search.set("missing");self.assertFalse(page.tree.get_children())
        self.app.clear_selection();self.assertFalse(page.rows)

    def test_stale_rule_response_discarded(self):
        app=self.app;app.session=object();app.workspace=dict(WORKSPACE)
        callbacks=[]
        with patch.object(app,"work",side_effect=lambda title,task,done,**kw:callbacks.append(done)):
            app.rules_page.load()
        app.workspace=None
        callbacks[0](dict(records=[dict(name="stale")],status="collected"))
        self.assertFalse(app.rules_page.rows)

    def load_rules_for_export(self, status="collected"):
        app=self.app; app.session=object(); app.workspace=dict(WORKSPACE)
        result=dict(records=[dict(name="one",properties=dict(enabled=True)),
                             dict(name="two",properties=dict(enabled=False))],status=status)
        with patch.object(app,"work",side_effect=lambda title,task,done,**kw:done(result)):
            app.rules_page.load()
        return app.rules_page

    def test_export_includes_hidden_rules_without_another_azure_call(self):
        page=self.load_rules_for_export()
        page.filter.set("Enabled"); page.search.set("missing")
        self.assertFalse(page.tree.get_children())
        with patch("sentinel_app.rules_page.filedialog.asksaveasfilename",return_value="audit.zip"), \
             patch("sentinel_app.rules_page.export_rules",return_value=dict(complete=True,record_count=2)) as export, \
             patch("sentinel_app.rules_page.list_rules") as azure, \
             patch.object(self.app,"work",side_effect=lambda title,task,done,**kw:done(task())):
            page.export()
        self.assertEqual(len(export.call_args.args[2]["records"]),2)
        self.assertEqual(export.call_args.args[1],WORKSPACE)
        azure.assert_not_called()
        self.assertIn("Exported 2 rules",self.app.status.get())

    def test_export_rejects_changed_workspace_session_and_reset(self):
        for change in ("workspace", "session", "reset"):
            page=self.load_rules_for_export()
            if change=="workspace":self.app.workspace=dict(WORKSPACE,workspace_id="other")
            elif change=="session":self.app.session=object()
            else:page.reset()
            with patch("sentinel_app.rules_page.messagebox.showinfo") as info, \
                 patch("sentinel_app.rules_page.filedialog.asksaveasfilename") as dialog:
                page.export()
            info.assert_called_once(); dialog.assert_not_called()

    def test_partial_export_requires_acknowledgment_and_cancel_writes_nothing(self):
        page=self.load_rules_for_export("partial")
        with patch("sentinel_app.rules_page.messagebox.askyesno",return_value=False) as confirm, \
             patch("sentinel_app.rules_page.filedialog.asksaveasfilename") as dialog, \
             patch("sentinel_app.rules_page.export_rules") as export:
            page.export()
        confirm.assert_called_once(); dialog.assert_not_called(); export.assert_not_called()
        with patch("sentinel_app.rules_page.messagebox.askyesno",return_value=True), \
             patch("sentinel_app.rules_page.filedialog.asksaveasfilename",return_value=""), \
             patch("sentinel_app.rules_page.export_rules") as export:
            page.export()
        export.assert_not_called()

    def test_repository_view_needs_no_workspace_and_switches_back(self):
        self.app.workspace=dict(WORKSPACE);self.app.show_page("rules");self.root.update()
        self.assertIn(WORKSPACE["workspace_name"],self.app.workspace_context.get())
        self.app.rule_view.set("repository");self.root.update()
        self.assertIn("Azure sign-in is not required",self.app.workspace_context.get())
        self.assertTrue(self.app.catalog_page.tree.winfo_ismapped())
        self.assertFalse(self.app.rules_page.tree.winfo_ismapped())
        self.app.rule_view.set("workspace");self.root.update()
        self.assertTrue(self.app.rules_page.tree.winfo_ismapped())

    def test_rule_detail_summarises_selected_rule(self):
        page=self.app.rules_page
        page.rows=[dict(name="rule-1",kind="Scheduled",properties=dict(displayName="First",enabled=True,severity="High",
                                                                      query="SigninLogs | take 1",tactics=["InitialAccess"]))]
        page.render();page.tree.selection_set("0");page.selected()
        self.assertEqual(page.detail_card.title_var.get(),"First")
        self.assertIn("InitialAccess",[label.cget("text") for label in page.overview.values])
        self.assertIn("SigninLogs",page.query.get("1.0","end"))
        page.reset();self.assertEqual(page.detail_card.title_var.get(),"Rule details")
        self.assertFalse(page.overview.values)

    def test_all_pages_fit_horizontally_at_minimum_size(self):
        for index in range(len(self.app.page_contents)):
            for view in ("workspace","repository"):
                self.app.rule_view.set(view)
                self.app.tabs.select(index);self.root.update()
                canvas=self.app.page_canvases[index]
                self.assertLessEqual(self.app.page_contents[index].winfo_width(),canvas.winfo_width())
                for widget in descendants(self.app.page_contents[index]):
                    if widget.winfo_ismapped() and isinstance(widget,(ui.ttk.Button,ui.ttk.Entry,ui.ttk.Combobox)):
                        self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),canvas.winfo_rootx()+canvas.winfo_width()+2,str(widget))


if __name__=="__main__":unittest.main()
