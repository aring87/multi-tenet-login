"""Review a concrete deployment, submit it explicitly, and follow GitHub runs."""
import tkinter as tk
from tkinter import ttk
import json
import webbrowser

from deployment_workflow import DeploymentService
from preview_workflow import WORKFLOW


class DeploymentDialog:
    def __init__(self, parent):
        self.parent, self.app = parent, parent.app
        self.service = DeploymentService(parent.service.request)
        self.folder = self.app.rule_builder.data_dir / "deployment-requests"
        self.window = tk.Toplevel(parent.window)
        self.window.title("Review deployment")
        self.window.geometry("940x690"); self.window.minsize(800, 620)
        self.window.transient(parent.window); self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.window, padding=18); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Review deployment", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(body, text="This can create or update the selected rule in the listed Sentinel workspaces.",
                  wraplength=730).pack(anchor="w", pady=(6, 12))
        frame = ttk.Frame(body); frame.pack(fill="both", expand=True)
        self.review = tk.Text(frame, height=13, width=60, wrap="word", state="disabled", font=("Consolas", 10))
        scroll = ttk.Scrollbar(frame, command=self.review.yview)
        self.review.configure(yscrollcommand=scroll.set); scroll.pack(side="right", fill="y")
        self.review.pack(fill="both", expand=True)
        self.notice = tk.StringVar(value="Checking successful previews and current repository settings...")
        ttk.Label(body, textvariable=self.notice, wraplength=730).pack(fill="x", pady=10)
        frame = ttk.Frame(body); frame.pack(fill="x")
        self.runs = ttk.Treeview(frame, columns=("batch", "run", "state"), show="headings", height=4)
        for key, label, width in (("batch", "Batch", 60), ("run", "GitHub run", 120), ("state", "Last checked state", 530)):
            self.runs.heading(key, text=label); self.runs.column(key, width=width, minwidth=45)
        scroll = ttk.Scrollbar(frame, command=self.runs.yview)
        self.runs.configure(yscrollcommand=scroll.set); scroll.pack(side="right", fill="y")
        self.runs.pack(fill="x")
        actions = ttk.Frame(body); actions.pack(fill="x", pady=(12, 0))
        self.start_button = ttk.Button(actions, text="Start reviewed deployment", command=self.start, state="disabled")
        self.status_button = ttk.Button(actions, text="Refresh status", command=self.refresh, state="disabled")
        self.open_button = ttk.Button(actions, text="Open GitHub", command=self.open_run)
        self.close_button = ttk.Button(actions, text="Close", command=self.close)
        self.buttons = (self.start_button, self.status_button, self.open_button, self.close_button)
        for button in self.buttons: button.pack(side="left", padx=(0, 8))
        if self.record:
            self.render(); self.controls()
            self.notice.set("Saved request for this preview. Submission and run status are shown below.")
        else:
            self.work("Checking previews and preparing deployment...",
                      lambda:self.service.prepare(parent.record), self.prepared)

    @property
    def record(self):
        return self.parent.deployment_record

    def close(self):
        if not self.app.busy:
            self.window.destroy()
            if self.parent.window.winfo_exists(): self.parent.window.grab_set()

    def prepared(self, record):
        self.parent.deployment_record = record
        self.render()
        self.notice.set("Review the exact clients and configured rule states. No deployment has been submitted.")

    def controls(self):
        self.start_button.configure(state="normal" if self.record and not self.record["attempted"] else "disabled")
        self.status_button.configure(state="normal" if self.record and any(r.get("run_id") for r in self.record["results"]) else "disabled")
        self.open_button.configure(state="normal"); self.close_button.configure(state="normal")

    def work(self, title, task, done):
        if self.app.busy:return
        for button in self.buttons: button.configure(state="disabled")
        self.notice.set(title)
        def wrapped():
            try:return task(), None
            except Exception as error:return None, str(error)
        def finished(result):
            value, error = result
            if error:self.notice.set(error)
            else:done(value)
            if self.record:self.render_runs()
            self.controls()
        self.app.work(title, wrapped, finished, page=5)

    def render(self):
        plan = self.record["plan"]
        lines = ["Repository: " + plan["repository"], "GitHub account: " + plan["identity"],
                 "Reviewed main revision: " + plan["revision"], "Rule: " + plan["rule_path"],
                 "Mode: DEPLOY | Workspaces: " + str(len(plan["targets"])) + " | Batches: " + str(len(plan["batches"])), ""]
        for client in plan["clients"]:
            lines += [client["name"] + " | " + client["workspace"] + " | " + client["target"],
                      "  Configured rule state: " + client["rule_state"],
                      "  Subscription: " + client["subscription_id"] + " | Resource group: " + client["resource_group"]]
            if client["overrides"]:lines.append("  Assignment overrides: " + json.dumps(client["overrides"], ensure_ascii=False))
        lines += ["", "Successful preview runs: " + ", ".join(str(r["run_id"]) for r in plan["preview"]["results"]), "",
                  "GitHub runs the repository's deployment workflow and enforces its configured environment protections. If no approval is required, deployment may start immediately.",
                  "The supplied workflow performs new preview checks before deployment. This does not assign the rule to other clients or change its configured enabled state.",
                  "GitHub resolves main when each run starts. It can change after this review; check each run's revision and new preview artifacts before approving production."]
        self.review.configure(state="normal"); self.review.delete("1.0", "end")
        self.review.insert("1.0", "\n".join(lines)); self.review.configure(state="disabled")
        self.render_runs()

    def render_runs(self):
        self.runs.delete(*self.runs.get_children())
        for i, result in enumerate(self.record["results"]):
            self.runs.insert("", "end", iid=str(i), values=(i + 1, result.get("run_id", "Unknown"), result["state"]))

    def start(self):
        if self.app.busy or not self.record or self.record["attempted"]:return
        self.work("Submitting reviewed deployment requests...", lambda:self.service.dispatch(self.record, self.folder),
                  lambda _:self.notice.set("Requests submitted. Refresh status and open GitHub for jobs and any required environment approvals. Submission does not mean deployment succeeded."))

    def refresh(self):
        if self.app.busy or not self.record:return
        self.work("Reading deployment run status...", lambda:self.service.refresh(self.record, self.folder),
                  lambda _:self.notice.set("Status refreshed. Open GitHub for each workspace's jobs, preview artifacts and any required approvals."))

    def open_run(self):
        if self.app.busy:return
        repository = self.parent.snapshot["repository"]
        url = "https://github.com/" + repository + "/actions/workflows/" + WORKFLOW
        selected = self.runs.selection()
        if selected and self.record:
            result = self.record["results"][int(selected[0])]
            if result.get("run_id"):url = "https://github.com/" + repository + "/actions/runs/" + str(result["run_id"])
        webbrowser.open(url)
