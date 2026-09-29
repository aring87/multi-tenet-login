"""Offline checks for the redesigned desktop's navigation and state."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import desktop_app as ui
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

    def test_collapsed_logs_preserve_selections_and_expose_summary(self):
        self.app.tabs.select(3);self.root.update()
        toggle=next(w for w in descendants(self.root) if isinstance(w,ui.ttk.Button) and "Add logs" in str(w.cget("text")))
        body=toggle.master.winfo_children()[0]
        self.assertFalse(body.winfo_manager())
        toggle.invoke();self.root.update();self.assertTrue(body.winfo_manager())
        self.app.audit_flags["audit"].set(True)
        self.app.vars["audit_start"].set("2024-01-01")
        toggle.invoke();self.root.update()
        self.assertTrue(self.app.audit_options()["audit"])
        self.assertEqual(self.app.vars["audit_start"].get(),"2024-01-01")
        self.assertIn("1 Sentinel activity logs",self.app.audit_scope_summary.get())

    def test_workspace_context_updates_and_clears(self):
        self.app.workspace=dict(WORKSPACE);self.app.identity.set("Synthetic workspace")
        self.assertIn(WORKSPACE["workspace_name"],self.app.workspace_context.get())
        self.app.clear_selection()
        self.assertIn("No workspace selected",self.app.workspace_context.get())

    def test_busy_state_disables_controls_and_restores_readonly_fields(self):
        self.app.set_busy(True)
        self.assertEqual(self.app.activity_badge.cget("text"),"WORKING")
        self.assertEqual(str(self.app.subbox.cget("state")),"disabled")
        self.app.set_busy(False)
        self.assertEqual(str(self.app.subbox.cget("state")),"readonly")
        self.assertEqual(self.app.activity_badge.cget("text"),"READY")

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

    def test_all_pages_fit_horizontally_at_minimum_size(self):
        for index in range(5):
            self.app.tabs.select(index);self.root.update()
            canvas=self.app.page_canvases[index]
            self.assertLessEqual(self.app.page_contents[index].winfo_width(),canvas.winfo_width())
            for widget in descendants(self.app.page_contents[index]):
                if widget.winfo_ismapped() and isinstance(widget,(ui.ttk.Button,ui.ttk.Entry,ui.ttk.Combobox)):
                    self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),canvas.winfo_rootx()+canvas.winfo_width()+2,str(widget))


if __name__=="__main__":unittest.main()
