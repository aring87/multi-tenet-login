"""Workspace-scoped analytics rule inventory view."""
import json
from copy import deepcopy
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from rule_export import export_rules
from workspace_tools import list_rules, rule_values

class RulesPage:
    def __init__(self, app, parent):
        self.app = app
        self.rows = []
        self.collection = None
        self.loaded_session = None
        self.loaded_workspace = None
        card, body = app.card(parent, "Analytics rules", "All configured rules in the selected workspace, including disabled rules. Select a rule to inspect its configuration.")
        card.pack(fill="both", expand=True, pady=(0,16))
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x")
        ttk.Label(body,text="Search name, rule ID, severity or type",style="Field.TLabel").pack(before=bar,anchor="w",pady=(0,6))
        self.search = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.search); entry.pack(side="left", fill="x", expand=True, padx=(0,10))
        self.filter = tk.StringVar(value="All states")
        combo = ttk.Combobox(bar, textvariable=self.filter, values=("All states", "Enabled", "Disabled", "Not specified"), state="readonly", width=16)
        combo.pack(side="left", padx=(0,10))
        app.button(bar, "Refresh rules", self.load, "Primary.TButton").pack(side="right")
        export_bar = ttk.Frame(body, style="Card.TFrame")
        export_bar.pack(fill="x", pady=(10, 0))
        app.button(export_bar, "Export all rules", self.export).pack(side="left", padx=(0, 10))
        hint = ttk.Label(export_bar, text="ZIP with raw JSON, CSV and audit details. Includes all loaded rules.",
                         style="Muted.TLabel", wraplength=380)
        hint.pack(side="left", fill="x", expand=True)
        self.summary = tk.StringVar(value="Select a workspace to load its rules.")
        label = ttk.Label(body, textvariable=self.summary, style="Muted.TLabel", wraplength=650)
        label.pack(fill="x", pady=12); app.wrap_to_parent(label)
        frame = ttk.Frame(body); frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("name", "state", "severity", "kind"), show="headings", height=11, selectmode="browse")
        for key, title, width in (("name","Rule name",320),("state","State",100),("severity","Severity",85),("kind","Type",135)):
            self.tree.heading(key,text=title); self.tree.column(key,width=width,minwidth=70)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set,xscrollcommand=horizontal.set)
        self.tree.grid(row=0,column=0,sticky="nsew"); scroll.grid(row=0,column=1,sticky="ns")
        horizontal.grid(row=1,column=0,sticky="ew"); frame.columnconfigure(0,weight=1); frame.rowconfigure(0,weight=1)
        ttk.Label(body,text="Rule configuration",style="Section.TLabel").pack(anchor="w",pady=(16,8))
        self.details = tk.Text(body,height=10,wrap="word",state="disabled",font=("Consolas",10))
        self.details.pack(fill="both",expand=True)
        self.tree.bind("<<TreeviewSelect>>",self.selected)
        self.search.trace_add("write",lambda *args:self.render())
        self.filter.trace_add("write",lambda *args:self.render())

    def detail(self, text=""):
        self.details.configure(state="normal"); self.details.delete("1.0","end")
        self.details.insert("1.0",text); self.details.configure(state="disabled")

    def reset(self):
        self.collection=None; self.loaded_session=None; self.loaded_workspace=None
        self.rows=[]; self.render(); self.summary.set("Select a workspace, then refresh its rules.")

    def render(self):
        self.tree.delete(*self.tree.get_children()); self.detail()
        needle=self.search.get().strip().casefold()
        for index,row in enumerate(self.rows):
            values=rule_values(row)
            if self.filter.get() not in ("All states",values[1]): continue
            if needle and needle not in " ".join(map(str,values)).casefold() and needle not in row.get("name", "").casefold(): continue
            self.tree.insert("", "end", iid=str(index), values=values)

    def selected(self,event=None):
        selection=self.tree.selection()
        if selection:self.detail(json.dumps(self.rows[int(selection[0])],indent=2,ensure_ascii=False))

    def load(self):
        app=self.app
        if app.busy:return
        if not app.session or not app.workspace:
            messagebox.showinfo("Select workspace","Sign in and select a workspace first.",parent=app.root); return
        session, workspace=app.session,dict(app.workspace)
        self.reset(); self.summary.set("Loading rules from " + workspace["workspace_name"] + "...")
        def task():
            try:return list_rules(session,workspace)
            except Exception as error:return dict(records=[],status="error",error=str(error))
        def done(result):
            if app.session is not session or app.workspace != workspace:return
            self.collection=deepcopy(result)
            self.collection.setdefault("collected_at_utc", datetime.now(timezone.utc).isoformat())
            self.loaded_session=session; self.loaded_workspace=deepcopy(workspace)
            self.rows=self.collection["records"]; self.render()
            enabled=sum(rule_values(r)[1]=="Enabled" for r in self.rows)
            disabled=sum(rule_values(r)[1]=="Disabled" for r in self.rows)
            text=f"{len(self.rows)} rules | {enabled} enabled | {disabled} disabled | {len(self.rows)-enabled-disabled} unspecified"
            if result["status"] not in ("collected","no_records"):
                text="Incomplete inventory: " + text + ". " + result.get("error",result["status"])
            elif not self.rows:text="No analytics rules returned for this workspace."
            self.summary.set(text); app.status.set("Rule inventory refreshed for " + workspace["workspace_name"] + ".")
        app.work("Reading workspace analytics rules...",task,done,page=4)

    def export(self):
        app=self.app
        if app.busy:return
        if (self.collection is None or app.session is not self.loaded_session
                or not app.session or app.workspace != self.loaded_workspace):
            messagebox.showinfo("Refresh rules", "Load rules for the current workspace before exporting.", parent=app.root)
            return
        complete=self.collection.get("status") in ("collected", "no_records")
        if not complete:
            if not self.rows:
                messagebox.showinfo("No inventory", "The rule collection failed. Refresh rules before exporting.", parent=app.root)
                return
            if not messagebox.askyesno("Incomplete rule inventory",
                    "Some rules could not be collected. Export the available rules as an explicitly incomplete audit?",
                    parent=app.root):return
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename="analytics-rules-"+self.loaded_workspace["workspace_id"]+"-"+stamp+".zip"
        path=filedialog.asksaveasfilename(parent=app.root, title="Export all workspace analytics rules",
                initialfile=filename, defaultextension=".zip", filetypes=[("Rule audit ZIP", "*.zip")])
        if not path:return
        workspace, collection=deepcopy(self.loaded_workspace), deepcopy(self.collection)
        def done(manifest):
            status="complete collection" if manifest["complete"] else "INCOMPLETE collection"
            app.status.set(f"Exported {manifest['record_count']} rules ({status}) to {path}")
        app.work("Exporting the loaded rule inventory...", lambda:export_rules(path,workspace,collection),done,page=4)
