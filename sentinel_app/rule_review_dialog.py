"""Concrete diff review before a user explicitly creates a draft PR."""
import copy
import json
import re
import tkinter as tk
from tkinter import ttk
import webbrowser

from .repository_reviews import ReviewService, diff
from .rule_drafts import validated_yaml


class ReviewDialog:
    def __init__(self, builder, record=None):
        self.builder, self.app = builder, builder.app
        self.record = record
        self.form = copy.deepcopy(builder.form())
        self.source = copy.deepcopy(builder.source)
        self.service = ReviewService()
        self.folder = builder.data_dir / "review-requests"
        self.window = tk.Toplevel(self.app.root)
        self.window.title("Review rule for GitHub"); self.window.geometry("900x740"); self.window.minsize(720, 580)
        self.window.transient(self.app.root); self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.window, padding=18); body.pack(fill="both", expand=True)
        settings = self.app.vars
        repository = (self.source or {}).get("repository") or "/".join(
            settings[k].get().strip() for k in ("github_owner", "github_repo"))
        filename = re.sub(r"[^a-z0-9]+", "-", self.form["name"].lower()).strip("-")[:80] or "new-rule"
        self.repository = tk.StringVar(value=record["plan"]["repository"] if record else repository.strip("/"))
        self.path = tk.StringVar(value=record["plan"]["path"] if record else (self.source or {}).get("path", "rules/sentinel/" + filename + ".yml"))
        self.controls = []
        for label, variable in (("Private detection repository (owner/name)", self.repository), ("Rule file path", self.path)):
            ttk.Label(body, text=label).pack(anchor="w", pady=(0, 4))
            entry = ttk.Entry(body, textvariable=variable); entry.pack(fill="x", pady=(0, 10))
            self.controls.append(entry)
        self.notice = tk.StringVar(value="Prepare a review to check the repository and see the exact file diff. This reads GitHub without changing it.")
        label = ttk.Label(body, textvariable=self.notice, wraplength=820)
        label.pack(fill="x", pady=8)
        body.bind("<Configure>", lambda event: label.configure(wraplength=max(400, event.width)), add="+")
        frame = ttk.Frame(body); frame.pack(fill="both", expand=True)
        self.text = tk.Text(frame, width=60, height=20, wrap="word", state="disabled", font=("Consolas", 10))
        scroll = ttk.Scrollbar(frame, command=self.text.yview); self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); self.text.pack(fill="both", expand=True)
        actions = ttk.Frame(body); actions.pack(fill="x", pady=(14, 0))
        self.prepare_button = ttk.Button(actions, text="Prepare review", command=self.prepare)
        self.submit_button = ttk.Button(actions, text="Create / recover draft PR", command=self.submit, state="disabled")
        self.link_button = ttk.Button(actions, text="Open pull request", command=self.open_link, state="disabled")
        self.close_button = ttk.Button(actions, text="Close", command=self.close)
        for button in (self.prepare_button, self.submit_button, self.link_button, self.close_button): button.pack(side="left", padx=(0, 8))
        self.locked = record is not None
        self.repository.trace_add("write", self.invalidate); self.path.trace_add("write", self.invalidate)
        if record: self.render()

    def close(self):
        if not self.app.busy: self.window.destroy()

    def invalidate(self, *args):
        self.record = None; self.submit_button.configure(state="disabled"); self.link_button.configure(state="disabled")
        self.show(""); self.notice.set("Destination changed. Prepare a fresh review.")

    def show(self, text):
        self.text.configure(state="normal"); self.text.delete("1.0", "end")
        self.text.insert("1.0", text); self.text.configure(state="disabled")

    def refresh_controls(self):
        for entry in self.controls: entry.configure(state="disabled" if self.locked else "normal")
        self.prepare_button.configure(state="disabled" if self.locked else "normal")
        self.submit_button.configure(state="normal" if self.record and not self.record.get("pull_request") else "disabled")
        self.link_button.configure(state="normal" if self.record and self.record.get("pull_request") else "disabled")
        self.close_button.configure(state="normal")

    def run(self, title, task, done):
        if self.app.busy: return
        for widget in self.controls + [self.prepare_button, self.submit_button, self.link_button, self.close_button]: widget.configure(state="disabled")
        self.notice.set(title)
        def wrapped():
            try: return task(), None
            except Exception as error: return None, str(error)
        def finished(result):
            value, error = result
            if error:
                self.notice.set(error + (" Recovery files are in " + str(self.folder) if self.locked else ""))
            else: done(value)
            self.refresh_controls()
        self.app.work(title, wrapped, finished, page=6)

    def prepare(self):
        if self.app.busy or self.locked: return
        self.record = None
        self.show("")
        repository, path = self.repository.get().strip(), self.path.get().strip()
        def task():
            content, _ = validated_yaml(self.form)
            return self.service.prepare(repository, path, content, self.form["allow_missing_mitre"], self.source)
        def done(record): self.record = record; self.render()
        self.run("Reading repository and preparing the diff...", task, done)

    def render(self):
        plan = self.record["plan"]
        lines = ["Repository: " + plan["repository"], "Signed in as: " + plan["identity"],
                 "Base: " + plan["base_branch"] + " at " + plan["base_sha"], "File: " + plan["path"], "",
                 "Existing client references (repository configuration, not live deployment state):"]
        for target in plan["impact"]:
            lines.append(target["target"] + " | target enabled=" + str(target["target_enabled"]) +
                         " | resulting rule enabled=" + str(target["rule_enabled"]) + " | overrides=" + json.dumps(target["overrides"]))
        if not plan["impact"]: lines.append("None. This request does not assign the rule to clients.")
        lines += ["", *plan["warnings"], "", diff(plan)]
        self.show("\n".join(lines))
        self.notice.set("Review the diff, then create a draft PR. Creating a branch/PR can trigger configured repository workflows. The app does not merge or dispatch deployments.")
        if self.record.get("pull_request"): self.notice.set("Pull request: " + self.record["pull_request"])
        self.refresh_controls()

    def submit(self):
        if self.app.busy or not self.record or self.record.get("pull_request"): return
        self.locked = True
        def done(url):
            self.notice.set("Draft pull request available: " + url)
            self.builder.note.set("Draft pull request: " + url)
        self.run("Creating or recovering the reviewed draft pull request...",
                 lambda: self.service.submit(self.record, self.folder), done)

    def open_link(self):
        if self.record and self.record.get("pull_request"):
            url = self.record["pull_request"]
            if re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+", url):
                webbrowser.open(url)
