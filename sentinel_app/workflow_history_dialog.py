"""Search saved preview and deployment requests and refresh their run status on request."""
import json
import tkinter as tk
from tkinter import ttk, filedialog
import webbrowser

from . import ui_kit as kit
from .desktop_theme import COLORS, px
from .workflow_history import list_receipts, load_receipt, summary, workflow_url, WorkflowHistoryService


class WorkflowHistoryDialog:
    def __init__(self, app):
        self.app = app; self.folder = app.rule_builder.data_dir
        self.service = WorkflowHistoryService(); self.rows = []; self.visible = {}; self.current = None
        self.window = tk.Toplevel(app.root)
        self.window.title("Preview and deployment history"); self.window.configure(bg=COLORS["card"])
        body = ttk.Frame(self.window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Preview and deployment history", style="CardTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(body, text="Saved on this PC. Select Refresh status to read the latest result from GitHub; "
                                                "this screen cannot start or retry deployments.",
                                     style="CardSubtitle.TLabel", justify="left", width=1), minimum=px(200)).pack(
            fill="x", pady=(px(2), px(14)))
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=(0, px(10)))
        self.search = tk.StringVar(); self.search_box = ttk.Entry(bar, textvariable=self.search)
        self.search_box.pack(side="left", fill="x", expand=True, padx=(0, px(12)))
        kit.placeholder(self.search_box, self.search, "Search by rule, client, workspace or library")
        self.mode = tk.StringVar(value="All requests")
        self.mode_box = ttk.Combobox(bar, textvariable=self.mode, values=("All requests", "Preview", "Deploy"), state="readonly", width=14)
        self.mode_box.pack(side="right"); self.mode_box.bind("<MouseWheel>", lambda event: "break")
        # Bottom-anchored controls are packed first so they always have room; the lists share what remains.
        self.actions, buttons = kit.action_row(body, primary=("Refresh status", self.refresh_status),
                                               secondary=(("Open in GitHub", self.open_run), ("Reload", self.reload),
                                                          ("Open receipt file…", self.open_file)),
                                               close=("Close", self.close))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.status_button, self.open_button, self.reload_button, self.file_button, self.close_button = buttons
        self.buttons = (self.reload_button, self.status_button, self.open_button, self.file_button, self.close_button)
        runs_frame, self.runs, self.runs_empty = kit.table(body, (("batch", "Batch", 60, False), ("run", "Run ID", 105, False),
                                                                  ("state", "Last checked result", 465, True),
                                                                  ("checked", "Checked (UTC)", 180, False)), height=4)
        runs_frame.pack(side="bottom", fill="x")
        ttk.Label(body, text="Runs", style="Section.TLabel").pack(side="bottom", anchor="w", pady=(px(10), px(6)))
        self.notice = tk.StringVar(value="Loading saved requests…")
        self.banner = kit.Banner(body, self.notice); self.banner.pack(side="bottom", fill="x", pady=(px(10), 0))
        frame, self.table, self.empty = kit.table(body, (("mode", "Type", 75, False), ("rule", "Rule", 190, True),
                                                         ("repo", "Library", 180, True), ("clients", "Workspaces", 90, False),
                                                         ("state", "Saved status", 240, True), ("created", "Requested (UTC)", 170, False)),
                                                  height=6)
        frame.pack(fill="both", expand=True)
        ttk.Label(body, text="Details", style="Section.TLabel").pack(anchor="w", pady=(px(10), px(6)))
        detail_frame, self.detail = kit.text_area(body, 8, readonly=True); detail_frame.pack(fill="both", expand=True)
        self.search.trace_add("write", lambda *args: self.render())
        self.mode.trace_add("write", lambda *args: self.render())
        self.table.bind("<<TreeviewSelect>>", self.selected)
        kit.place_dialog(self.window, app.root, 1080, 800, 920, 720)
        kit.modal(self.window, app.root, self.close, primary=self.status_button, focus=self.search_box)
        self.reload()

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()

    def controls(self):
        busy = self.app.busy
        for button in self.buttons: button.configure(state="disabled" if busy else "normal")
        if not self.current or busy:
            self.status_button.configure(state="disabled"); self.open_button.configure(state="disabled")
        self.search_box.configure(state="disabled" if busy else "normal")
        self.mode_box.configure(state="disabled" if busy else "readonly")

    def work(self, title, task, done):
        kit.run_in_dialog(self.app, self.window, title, task, done, controls=self.buttons + (self.search_box, self.mode_box),
                          say=self.say, page="rules", after=self.controls, log_as="Workflow history:")

    def reload(self):
        def done(result):
            self.rows, issues = result; self.render()
            self.say(str(len(self.rows)) + " saved requests. Select Refresh status to read the latest result from GitHub."
                     + (" " + " ".join(issues[:3]) if issues else ""), "warning" if issues else "info")
        self.work("Reading the saved receipts on this PC…", lambda: list_receipts(self.folder), done)

    def render(self):
        self.table.delete(*self.table.get_children()); self.visible = {}
        term = self.search.get().strip().casefold(); mode = self.mode.get()
        for i, row in enumerate(self.rows):
            record = row["record"]; p = record["plan"]
            if mode != "All requests" and row["mode"] != mode.lower(): continue
            haystack = " ".join([p["rule_path"], p["repository"], record["request_id"], *p["targets"],
                                 *[c[k] for c in p["clients"] for k in ("name", "workspace")]]).casefold()
            if term and term not in haystack: continue
            self.visible[str(i)] = row
            self.table.insert("", "end", iid=str(i), values=(row["mode"].title(), p["rule_path"].rsplit("/", 1)[-1], p["repository"],
                                                            len(p["targets"]), summary(record), record["created_at"]))
        if self.visible: self.empty.hide()
        elif self.rows: self.empty.show("No requests match your search.", ("Clear search", lambda: self.search.set("")))
        else: self.empty.show("No previews or deployments have been requested from this PC yet.")
        self.selected()

    def selected(self, event=None):
        selection = self.table.selection(); self.current = self.visible.get(selection[0]) if selection else None
        self.runs.delete(*self.runs.get_children())
        text = "Select a saved request to see its clients and run receipts."
        if self.current:
            r = self.current["record"]; p = r["plan"]
            lines = ["Library: " + p["repository"], "Requested by: " + p["identity"], "Request ID: " + r["request_id"],
                     "Rule: " + p["rule_path"], "Checked against main revision: " + p["revision"], "Receipt: " + str(self.current["file"]),
                     "", "Saved intended workspaces (a successful workflow is not a fresh Sentinel inventory):"]
            for c in p["clients"]:
                lines.extend([c["name"] + " | " + c["workspace"] + " | " + c["target"] + " | rule " + c["rule_state"],
                              "Subscription: " + c["subscription_id"] + " | Resource group: " + c["resource_group"]])
            for i, result in enumerate(r["results"]):
                state = result.get("history_error") or result["state"]
                self.runs.insert("", "end", iid=str(i), values=(i + 1, result.get("run_id", "Unknown"), state,
                                                                result.get("checked_at", "Not checked")))
                lines.extend(["", "Batch " + str(i + 1) + ": " + json.dumps(result["inputs"]),
                              "Actual run revision: " + result.get("actual_revision", "Not checked")])
                if not result.get("run_id"):
                    lines.append("No confirmed run ID. Check GitHub Actions before assuming the request failed; do not retry blindly.")
            text = "\n".join(lines)
        if self.runs.get_children(): self.runs_empty.hide()
        else: self.runs_empty.show("Select a request to see its runs." if not self.current else "No run receipts.")
        self.detail.configure(state="normal"); self.detail.delete("1.0", "end"); self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
        self.controls()

    def refresh_status(self):
        if self.app.busy or not self.current: return
        row = self.current; path = row["file"]
        def done(result):
            record, mode, issues = result; row.update(record=record, mode=mode)
            item = next((key for key, value in self.visible.items() if value is row), None)
            self.render()
            if item in self.visible: self.table.selection_set(item); self.selected()
            self.say("Status refreshed from GitHub." if not issues else "Some runs couldn't be refreshed: " + " ".join(issues[:3]),
                     "info" if not issues else "warning")
        self.work("Reading the run status from GitHub. Nothing is started…", lambda: self.service.refresh(path), done)

    def open_run(self):
        if self.app.busy or not self.current: return
        selection = self.runs.selection(); index = int(selection[0]) if selection else None
        webbrowser.open(workflow_url(self.current["record"], index))

    def open_file(self):
        if self.app.busy: return
        path = filedialog.askopenfilename(parent=self.window, title="Open workflow receipt", initialdir=str(self.folder),
                                          filetypes=[("Workflow receipts", "*.preview.json *.deployment.json")])
        if not path: return
        try:
            record, mode = load_receipt(path)
            row = dict(file=path, record=record, mode=mode); self.rows = [row] + [r for r in self.rows if str(r["file"]) != path]
            self.search.set(""); self.mode.set("All requests"); self.render(); self.table.selection_set("0"); self.selected()
            self.say("Opened a local receipt. No GitHub requests were made.")
        except Exception as error:
            self.say("Receipt unavailable: " + str(error), "danger")
