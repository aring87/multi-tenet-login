"""Select concrete workspaces, review a preview request, and follow returned runs."""
import copy
import tkinter as tk
from tkinter import ttk
import webbrowser

from preview_workflow import PreviewService, eligible_clients, WORKFLOW


class PreviewDialog:
    def __init__(self, app, snapshot, rule):
        self.app, self.snapshot, self.rule = app, copy.deepcopy(snapshot), copy.deepcopy(rule)
        self.service = PreviewService()
        self.folder = app.rule_builder.data_dir / "preview-requests"
        self.record, self.locked = None, False
        self.selected, self.visible = set(), {}
        self.eligible = {c["target"] for c in eligible_clients(snapshot, rule["path"])}
        self.window = tk.Toplevel(app.root); self.window.title("Preview rule for clients")
        self.window.geometry("1000x760"); self.window.minsize(850, 680)
        self.window.transient(app.root); self.window.grab_set(); self.window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.window, padding=18); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(4, weight=2); body.rowconfigure(6, weight=1)
        ttk.Label(body, text="Preview rule for clients", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=rule["name"] + "\n" + rule["path"], wraplength=800).grid(row=1, column=0, sticky="ew", pady=(6, 10))
        self.search = tk.StringVar()
        search_frame = ttk.Frame(body); search_frame.grid(row=2, column=0, sticky="ew")
        ttk.Label(search_frame, text="Search clients or workspaces").pack(side="left", padx=(0, 10))
        self.search_box = ttk.Entry(search_frame, textvariable=self.search); self.search_box.pack(side="left", fill="x", expand=True)
        bar = ttk.Frame(body); bar.grid(row=3, column=0, sticky="ew", pady=8)
        self.all_button = ttk.Button(bar, text="Select all eligible", command=self.select_all)
        self.clear_button = ttk.Button(bar, text="Clear selection", command=self.clear)
        self.all_button.pack(side="left"); self.clear_button.pack(side="left", padx=8)
        self.count = tk.StringVar(); ttk.Label(bar, textvariable=self.count).pack(side="left")
        frame = ttk.Frame(body); frame.grid(row=4, column=0, sticky="nsew")
        self.clients = ttk.Treeview(frame, columns=("selected", "client", "workspace", "target", "reason"), show="headings", height=7)
        for key, name, width in (("selected", "Pick", 45), ("client", "Client", 160), ("workspace", "Workspace", 150), ("target", "Target", 225), ("reason", "Availability", 180)):
            self.clients.heading(key, text=name); self.clients.column(key, width=width, minwidth=40)
        scroll = ttk.Scrollbar(frame, command=self.clients.yview); self.clients.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); self.clients.pack(fill="both", expand=True)
        self.clients.bind("<Button-1>", self.clicked)
        self.clients.bind("<space>", self.toggle_focused)
        ttk.Label(body, text="Click a row or press Space to select. Only enabled targets already assigned this rule can be previewed.",
                  wraplength=800).grid(row=5, column=0, sticky="ew", pady=8)
        review_frame = ttk.Frame(body); review_frame.grid(row=6, column=0, sticky="nsew")
        self.review = tk.Text(review_frame, height=7, width=50, wrap="word", state="disabled", font=("Consolas", 10))
        review_scroll = ttk.Scrollbar(review_frame, command=self.review.yview)
        self.review.configure(yscrollcommand=review_scroll.set)
        review_scroll.pack(side="right", fill="y"); self.review.pack(fill="both", expand=True)
        self.notice = tk.StringVar(value="Choose clients, then prepare the request. No GitHub run starts until you select Start previews.")
        ttk.Label(body, textvariable=self.notice, wraplength=800).grid(row=7, column=0, sticky="ew", pady=10)
        runs_frame = ttk.Frame(body); runs_frame.grid(row=8, column=0, sticky="ew")
        self.runs = ttk.Treeview(runs_frame, columns=("batch", "run", "state"), show="headings", height=3)
        for key, name, width in (("batch", "Batch", 60), ("run", "GitHub run", 120), ("state", "Last checked state", 600)):
            self.runs.heading(key, text=name); self.runs.column(key, width=width, minwidth=50)
        runs_scroll = ttk.Scrollbar(runs_frame, command=self.runs.yview)
        self.runs.configure(yscrollcommand=runs_scroll.set)
        runs_scroll.pack(side="right", fill="y"); self.runs.pack(fill="both", expand=True)
        actions = ttk.Frame(body); actions.grid(row=9, column=0, sticky="ew", pady=(12, 0))
        self.prepare_button = ttk.Button(actions, text="Prepare request", command=self.prepare)
        self.start_button = ttk.Button(actions, text="Start previews", command=self.start, state="disabled")
        self.status_button = ttk.Button(actions, text="Refresh status", command=self.refresh_status, state="disabled")
        self.open_button = ttk.Button(actions, text="Open GitHub", command=self.open_run)
        self.close_button = ttk.Button(actions, text="Close", command=self.close)
        for b in (self.prepare_button, self.start_button, self.status_button, self.open_button, self.close_button): b.pack(side="left", padx=(0, 8))
        self.search.trace_add("write", lambda *args: self.render_clients())
        self.render_clients()

    def close(self):
        if not self.app.busy: self.window.destroy()

    def render_clients(self):
        self.clients.delete(*self.clients.get_children()); self.visible = {}
        term = self.search.get().strip().casefold()
        for i, client in enumerate(self.snapshot["clients"]):
            if term and term not in " ".join((client["name"], client["workspace"], client["target"])).casefold(): continue
            target = client["target"]; self.visible[str(i)] = target
            reason = "Ready" if target in self.eligible else "Target disabled" if not client["raw"]["enabled"] else "Rule not assigned"
            self.clients.insert("", "end", iid=str(i), values=("Yes" if target in self.selected else "", client["name"], client["workspace"], target, reason))
        self.count.set(str(len(self.selected)) + " selected / " + str(len(self.eligible)) + " eligible")

    def invalidate(self):
        self.record = None; self.start_button.configure(state="disabled")
        self.show_review(""); self.notice.set("Selection changed. Prepare the request again.")
        self.render_clients()

    def toggle(self, item):
        if self.app.busy or self.locked: return
        target = self.visible.get(item)
        if target not in self.eligible: return
        if target in self.selected: self.selected.remove(target)
        else: self.selected.add(target)
        self.invalidate()
        if self.clients.exists(item): self.clients.focus(item); self.clients.selection_set(item)

    def clicked(self, event):
        item = self.clients.identify_row(event.y)
        if item: self.toggle(item); return "break"

    def toggle_focused(self, event):
        self.toggle(self.clients.focus()); return "break"

    def select_all(self):
        if not self.app.busy and not self.locked: self.selected = set(self.eligible); self.invalidate()

    def clear(self):
        if not self.app.busy and not self.locked: self.selected.clear(); self.invalidate()

    def show_review(self, text):
        self.review.configure(state="normal"); self.review.delete("1.0", "end"); self.review.insert("1.0", text); self.review.configure(state="disabled")

    def controls(self):
        for widget in (self.search_box, self.all_button, self.clear_button, self.prepare_button): widget.configure(state="disabled" if self.locked else "normal")
        self.start_button.configure(state="normal" if self.record and not self.locked else "disabled")
        self.status_button.configure(state="normal" if self.record and any(r.get("run_id") for r in self.record["results"]) else "disabled")
        self.open_button.configure(state="normal"); self.close_button.configure(state="normal")

    def work(self, title, task, done):
        if self.app.busy: return
        for widget in (self.search_box, self.all_button, self.clear_button, self.prepare_button, self.start_button, self.status_button, self.open_button, self.close_button): widget.configure(state="disabled")
        self.notice.set(title)
        def wrapped():
            try: return task(), None
            except Exception as error: return None, str(error)
        def finish(result):
            value, error = result
            if error: self.notice.set(error)
            else: done(value)
            if self.record:
                self.locked = self.record["attempted"]
                self.render_runs()
            self.controls()
        self.app.work(title, wrapped, finish, page=5)

    def prepare(self):
        if self.app.busy or self.locked: return
        self.record = None; self.show_review("")
        selected = sorted(self.selected)
        def done(record):
            self.record = record; plan = record["plan"]
            lines = ["Repository: " + plan["repository"], "GitHub account: " + plan["identity"],
                     "Reviewed main revision: " + plan["revision"], "Rule: " + plan["rule_path"],
                     "Mode: preview | Workflow runs: " + str(len(plan["batches"])), "", "Selected workspaces:"]
            lines += [c["name"] + " | " + c["workspace"] + " | " + c["target"] for c in plan["clients"]]
            lines += ["", "GitHub resolves main when each run starts. Refresh status to check the actual revision.",
                      "This runs the repository's configured preview workflow; it does not assign rules to new clients."]
            self.show_review("\n".join(lines)); self.notice.set("Request prepared. Review the exact targets above, then select Start previews.")
        self.work("Checking repository and workflow inputs...",
            lambda: self.service.prepare(self.snapshot["repository"], self.snapshot["revision"], self.rule["path"], selected), done)

    def start(self):
        if self.app.busy or self.locked or not self.record: return
        self.work("Submitting reviewed preview requests...", lambda: self.service.dispatch(self.record, self.folder),
                  lambda value: self.notice.set("Preview requests submitted. Refresh status for results. Saved locally in " + str(self.folder)))

    def render_runs(self):
        self.runs.delete(*self.runs.get_children())
        for i, result in enumerate(self.record["results"]):
            self.runs.insert("", "end", iid=str(i), values=(i + 1, result.get("run_id", "Unknown"), result["state"]))

    def refresh_status(self):
        if self.app.busy or not self.record: return
        self.work("Reading GitHub preview run status...", lambda: self.service.refresh(self.record, self.folder),
                  lambda value: self.notice.set("Status refreshed from GitHub. Open a run to inspect its jobs, errors and preview artifacts."))

    def open_run(self):
        if self.app.busy: return
        url = "https://github.com/" + self.snapshot["repository"] + "/actions/workflows/" + WORKFLOW
        selected = self.runs.selection()
        if selected and self.record:
            result = self.record["results"][int(selected[0])]
            if result.get("run_id"): url = "https://github.com/" + self.snapshot["repository"] + "/actions/runs/" + str(result["run_id"])
        webbrowser.open(url)
