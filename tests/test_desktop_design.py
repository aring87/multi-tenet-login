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
        page.search.set("missing");page.render_soon.flush();self.assertFalse(page.tree.get_children())
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
        page.filter.set("Enabled"); page.search.set("missing"); page.render_soon.flush()
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
                # The Rules page fills the window instead of scrolling: its frame is the visible area.
                canvas=self.app.page_canvases[index] or self.app.page_contents[index].master
                self.assertLessEqual(self.app.page_contents[index].winfo_width(),canvas.winfo_width())
                for widget in descendants(self.app.page_contents[index]):
                    if widget.winfo_ismapped() and isinstance(widget,(ui.ttk.Button,ui.ttk.Entry,ui.ttk.Combobox)):
                        self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),canvas.winfo_rootx()+canvas.winfo_width()+2,str(widget))

    def sample_rules(self, count=12):
        severities=("High","Medium","Low","Informational")
        return [dict(name=f"rule-{i}",kind="Scheduled",properties=dict(
            displayName=f"Rule {i:02d}",enabled=i%3!=0,severity=severities[i%4],query=f"SigninLogs | take {i}",
            lastModifiedUtc=f"2026-10-{10+i:02d}T09:00:00Z")) for i in range(count)]

    def show_rules(self, rows):
        app=self.app;app.session=object();app.workspace=dict(WORKSPACE)
        with patch.object(app,"work",side_effect=lambda title,task,done,**kw:done(dict(records=rows,status="collected"))):
            app.rules_page.load()
        app.show_page("rules");app.rule_view.set("workspace");self.root.update()
        return app.rules_page

    def test_rules_list_fills_window_with_details_beside_it(self):
        for width,height,least in ((1120,740,8),(1360,900,11)):
            self.root.geometry(f"{ui.px(width)}x{ui.px(height)}");page=self.show_rules(self.sample_rules(20))
            page.tree.selection_set("1");page.selected();self.root.update()
            self.assertIsNone(self.app.page_canvases[1])  # Nothing to scroll: the page fills the window.
            table,detail=page.table_card.frame,page.detail_card.frame
            self.assertGreater(detail.winfo_rootx(),table.winfo_rootx()+table.winfo_width()-2)
            self.assertEqual(detail.winfo_rooty(),table.winfo_rooty())
            page_frame=self.app.page_contents[1].master
            for card in (table,detail):
                self.assertLessEqual(card.winfo_rooty()+card.winfo_height(),page_frame.winfo_rooty()+page_frame.winfo_height())
            top=page.tree.bbox(page.tree.get_children()[0])[1]  # Under the headings.
            rows=(page.tree.winfo_height()-top)//int(self.root.tk.call("ttk::style","lookup","Treeview","-rowheight"))
            self.assertGreaterEqual(rows,least,(width,height))
            self.assertTrue(page.query_frame.winfo_ismapped())
            # The shown columns fill the table exactly: no clipped name column, no sideways scrolling.
            shown=page.tree["displaycolumns"]
            used=int(page.tree.column("#0","width"))+sum(int(page.tree.column(key,"width")) for key in shown)
            self.assertLessEqual(abs(used-page.tree.winfo_width()),1,(width,shown))
            self.assertEqual("modified" in shown,width>=1360)

    def test_rules_split_and_sort_are_remembered(self):
        page=self.show_rules(self.sample_rules())
        self.assertAlmostEqual(page.split.sashpos(0)/page.split.winfo_width(),.6,places=2)
        page.tree.toggle_sort("severity");self.root.update()
        severities=[page.tree.set(item,"severity") for item in page.tree.get_children()]
        self.assertEqual(severities,sorted(severities,key=ui.kit.SEVERITY_RANK.get))
        self.assertIn("▲",page.tree.heading("severity","text"))
        page.tree.toggle_sort("severity");self.root.update()
        self.assertEqual(page.tree.set(page.tree.get_children()[0],"severity"),"Informational")
        page.split.sashpos(0,int(page.split.winfo_width()*.5));page.split.dragged()
        self.app.save_state()
        state=ui.json.loads((Path(self.temp.name)/"ui-state.json").read_text())
        self.assertEqual(state["rules_sort"],["severity",True]);self.assertAlmostEqual(state["rules_split"],.5,places=2)
        root=tk.Tk();self.addCleanup(lambda:([root.after_cancel(t) for t in root.tk.call("after","info")],root.destroy()))
        again=ui.App(root).rules_page
        self.assertEqual(again.sort,("severity",True));self.assertIn("▼",again.tree.heading("severity","text"))
        self.assertAlmostEqual(again.split.share,.5,places=2)

    def test_rules_last_modified_column_sorts_by_time(self):
        rows=self.sample_rules(3);rows[0]["properties"]["lastModifiedUtc"]="2025-01-05T09:00:00Z"
        rows[1]["properties"].pop("lastModifiedUtc")
        page=self.show_rules(rows);page.sort_by("modified",True)
        self.assertEqual(page.tree.get_children(),("2","0","1"))  # Newest first; no date sorts last.
        self.assertIn("2025",page.tree.set("0","modified"))
        self.assertEqual(page.tree.set("1","modified"),"")

    def test_rules_filters_show_counts_until_reset(self):
        page=self.show_rules(self.sample_rules(6))
        self.assertEqual(page.filter_bar.items["All states"].cget("text"),"All 6")
        self.assertEqual(page.filter_bar.items["Disabled"].cget("text"),"Disabled 2")
        self.assertFalse(hasattr(page,"tiles"))
        page.reset()
        self.assertEqual(page.filter_bar.items["Disabled"].cget("text"),"Disabled")
        self.assertEqual(page.summary.get(),"Select Refresh to read the rules from Sentinel.")
        self.app.workspace=None;page.reset()
        self.assertEqual(page.summary.get(),"Select a workspace to load its rules.")

    def test_rules_row_menu_copies_name_id_and_query(self):
        page=self.show_rules(self.sample_rules(2))
        menu=page.tree.build_menu("1")
        commands=[i for i in range(menu.index("end")+1) if menu.type(i)=="command"]
        self.assertEqual([menu.entrycget(i,"label") for i in commands],["Copy","Copy name","Copy rule ID","Copy query"])
        menu.invoke(commands[2]);self.assertEqual(self.root.clipboard_get(),"rule-1")
        menu.invoke(commands[3]);self.assertEqual(self.root.clipboard_get(),"SigninLogs | take 1")

    def test_rule_details_keyboard_and_full_configuration(self):
        page=self.show_rules(self.sample_rules(2))
        page.tree.focus_set();page.tree.selection_set("1");page.selected();self.root.update()
        self.assertEqual(page.open_row(),"break");self.root.update()
        self.assertIs(self.root.focus_lastfor(),page.query_label.button)
        self.assertFalse(page.raw.frame.winfo_manager())  # The JSON waits behind its disclosure.
        page.raw.button.invoke();self.root.update()
        self.assertTrue(page.raw.frame.winfo_ismapped());self.assertIn('"rule-1"',page.details.get("1.0","end"))
        self.assertTrue(page.raw.button.cget("text").startswith("−"))
        page.tree.selection_set("0");page.selected();self.root.update()
        self.assertTrue(page.raw.frame.winfo_ismapped())  # It stays open for the next rule.
        keys=[label.cget("text") for label in page.overview.winfo_children()[::2]]
        self.assertEqual(keys[-2:],["Last modified","Rule ID"])

    def test_rules_search_is_debounced_and_keeps_selection(self):
        page=self.show_rules(self.sample_rules(12))
        page.tree.selection_set("5");page.selected()
        page.search.set("rule 0")
        self.assertEqual(len(page.tree.get_children()),12)  # Not filtered on the keystroke itself.
        page.render_soon.flush()
        self.assertEqual(page.tree.get_children(),tuple(str(i) for i in range(10)))
        self.assertEqual(page.tree.selection(),("5",));self.assertEqual(page.detail_card.title_var.get(),"Rule 05")
        self.assertTrue(page.search_entry.bind("<Down>"))  # Joined to the list (kit.link_search).

    def test_enter_in_rules_search_runs_the_pending_filter_first(self):
        page=self.show_rules(self.sample_rules(12))
        page.search_entry.focus_force();self.root.update()
        page.search.set("rule 07")  # Enter at once, before the debounced filter has run.
        page.search_entry.event_generate("<Return>");self.root.update()
        self.assertEqual(page.tree.get_children(),("7",));self.assertEqual(page.tree.selection(),("7",))
        self.assertEqual(page.detail_card.title_var.get(),"Rule 07");self.assertIs(self.root.focus_get(),page.tree)

    def test_rule_details_scroll_to_the_keyboard_focus(self):
        rows=self.sample_rules(2);rows[1]["properties"]["description"]=" ".join(["A long rule description."]*80)
        self.root.geometry(f"{ui.px(1120)}x{ui.px(740)}");page=self.show_rules(rows)
        page.tree.focus_force();page.tree.selection_set("1");page.selected();self.root.update()
        canvas=page.detail_area.canvas
        def in_view(widget):
            top=widget.winfo_rooty()
            return canvas.winfo_rooty()<=top and top+widget.winfo_height()<=canvas.winfo_rooty()+canvas.winfo_height()
        self.assertFalse(in_view(page.query_label.button))
        page.open_row();self.root.update()  # Enter on the rule.
        self.assertGreater(canvas.yview()[0],0);self.assertTrue(in_view(page.query_label.button))
        canvas.yview_moveto(0);self.root.update();self.assertFalse(in_view(page.raw.button))
        page.raw.button.focus_force();self.root.update()  # Tab onwards: the focus comes into view too.
        self.assertTrue(in_view(page.raw.button))

    def test_library_split_and_view_are_remembered(self):
        page=self.app.catalog_page
        self.assertEqual(page.view.get(),"Rules")  # Editing rules is the common task.
        self.app.show_page("rules");self.app.rule_view.set("repository");self.root.update()
        page.view.set("Clients")
        page.split.sashpos(0,int(page.split.winfo_width()*.55));page.split.dragged()
        self.app.save_state()
        state=ui.json.loads((Path(self.temp.name)/"ui-state.json").read_text())
        self.assertEqual(state["library_view"],"Clients");self.assertAlmostEqual(state["library_split"],.55,places=2)
        root=tk.Tk();self.addCleanup(lambda:([root.after_cancel(t) for t in root.tk.call("after","info")],root.destroy()))
        again=ui.App(root).catalog_page
        self.assertEqual(again.view.get(),"Clients");self.assertAlmostEqual(again.split.share,.55,places=2)

    def test_footer_slot_gives_its_height_back_after_the_builder(self):
        self.app.show_page("builder");self.root.update()
        self.assertGreater(self.app.footer_slot.winfo_height(),10)
        self.app.show_page("rules");self.root.update()
        # The builder hides the slot itself; if it stays packed it must be empty and flat.
        slot=self.app.footer_slot
        self.assertTrue(not slot.winfo_ismapped() or slot.winfo_height()<=1)
        bar=self.root.nametowidget(self.root.nametowidget(self.app.activity_badge.winfo_parent()).winfo_parent())
        tabs=self.app.tabs
        self.assertGreaterEqual(tabs.winfo_rooty()+tabs.winfo_height(),bar.winfo_rooty()-1)


if __name__=="__main__":unittest.main()
