"""Local review-request inventory. Listing and reopening do not contact GitHub."""
from datetime import datetime
from pathlib import Path
import stat
import tkinter as tk
from tkinter import ttk

from repository_reviews import load_request
from rule_review_dialog import ReviewDialog

MAX_REQUESTS = 200


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def read_entry(path):
    path = Path(path)
    if linked(path):
        raise ValueError("Linked request files are not supported.")
    record = load_request(path)
    plan = record["plan"]
    for key in ("name", "repository", "path", "identity", "base_branch", "before"):
        if not isinstance(plan.get(key), str):
            raise ValueError("Incomplete review request. Open the original app-created request.")
    if not isinstance(plan.get("impact"), list) or not isinstance(plan.get("warnings"), list):
        raise ValueError("Incomplete review request.")
    return record


def list_requests(folder):
    folder = Path(folder)
    if not folder.exists():
        return [], []
    if linked(folder):
        raise ValueError("Linked review-request folders are not supported.")
    candidates, issues = [], []
    for path in folder.glob("*.review.json"):
        try:
            if linked(path) or not path.is_file():
                raise ValueError("Not an ordinary request file")
            candidates.append((path.stat().st_mtime, path))
        except (OSError, ValueError):
            issues.append(path.name + ": unavailable or linked file.")
    candidates.sort(key=lambda item: (-item[0], item[1].name))
    if len(candidates) > MAX_REQUESTS:
        issues.append("Showing the newest " + str(MAX_REQUESTS) + " saved requests. Use Open request file for older requests.")
    rows = []
    for modified, path in candidates[:MAX_REQUESTS]:
        try:
            record = read_entry(path)
            plan = record["plan"]
            rows.append(dict(file=path, name=plan["name"], repository=plan["repository"],
                rule_path=plan["path"], request_id=plan["request_id"],
                modified=datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M"),
                status="PR link saved" if record.get("pull_request") else "Submission unconfirmed"))
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
            issues.append(path.name + ": unreadable or invalid request; the file was left unchanged.")
    return rows, issues


class ReviewHistoryDialog:
    def __init__(self, builder):
        self.builder, self.app = builder, builder.app
        self.folder = builder.data_dir / "review-requests"
        self.rows, self.visible, self.issues = [], {}, []
        self.window = tk.Toplevel(self.app.root)
        self.window.title("Saved rule reviews")
        self.window.geometry("960x560"); self.window.minsize(760, 420)
        self.window.transient(self.app.root); self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.window, padding=18); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(4, weight=1)
        ttk.Label(body, text="Saved rule reviews", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Requests saved on this computer. GitHub review, checks and merge status are available through the PR link.",
            wraplength=700).grid(row=1, column=0, sticky="ew", pady=(6, 12))
        self.search = tk.StringVar()
        ttk.Label(body, text="Search rule, repository or request ID").grid(row=2, column=0, sticky="w")
        self.search_box = ttk.Entry(body, textvariable=self.search); self.search_box.grid(row=3, column=0, sticky="ew", pady=(4, 10))
        frame = ttk.Frame(body); frame.grid(row=4, column=0, sticky="nsew")
        columns = ("name", "repository", "status", "modified")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=9)
        for key, label, width in zip(columns, ("Rule", "Repository", "Saved state", "Updated locally"), (250, 210, 180, 145)):
            self.table.heading(key, text=label); self.table.column(key, width=width, minwidth=100)
        scroll = ttk.Scrollbar(frame, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); self.table.pack(fill="both", expand=True)
        self.notice = tk.StringVar()
        ttk.Label(body, textvariable=self.notice, wraplength=700).grid(row=5, column=0, sticky="ew", pady=10)
        self.detail = tk.StringVar(value="Select a request to inspect its saved review.")
        ttk.Label(body, textvariable=self.detail, wraplength=700).grid(row=6, column=0, sticky="ew", pady=(0, 10))
        actions = ttk.Frame(body); actions.grid(row=7, column=0, sticky="ew")
        self.refresh_button = ttk.Button(actions, text="Refresh list", command=self.refresh)
        self.open_button = ttk.Button(actions, text="Open saved review", command=self.open_selected, state="disabled")
        self.file_button = ttk.Button(actions, text="Open request file", command=self.open_file)
        self.close_button = ttk.Button(actions, text="Close", command=self.close)
        for button in (self.refresh_button, self.open_button, self.file_button, self.close_button):
            button.pack(side="left", padx=(0, 8))
        self.search.trace_add("write", lambda *args: self.render())
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        self.table.bind("<Double-1>", lambda event: self.open_selected())
        self.refresh()

    def close(self):
        if not self.app.busy: self.window.destroy()

    def refresh(self):
        if self.app.busy: return
        self.open_button.configure(state="disabled")
        for button in (self.refresh_button, self.file_button, self.close_button): button.configure(state="disabled")
        self.notice.set("Reading locally saved requests...")
        def task():
            try: return list_requests(self.folder), None
            except (OSError, ValueError) as error: return None, str(error)
        def done(result):
            value, error = result
            self.rows, self.issues = value if value else ([], [error])
            for button in (self.refresh_button, self.file_button, self.close_button): button.configure(state="normal")
            self.render()
        self.app.work("Reading saved rule reviews...", task, done, page=6)

    def render(self):
        self.table.delete(*self.table.get_children()); self.visible = {}
        term = self.search.get().strip().casefold()
        for index, row in enumerate(self.rows):
            if term and term not in " ".join(str(row[key]) for key in ("name", "repository", "rule_path", "request_id")).casefold():
                continue
            item = str(index); self.visible[item] = row
            self.table.insert("", "end", iid=item, values=tuple(row[key] for key in ("name", "repository", "status", "modified")))
        message = (str(len(self.visible)) + " of " + str(len(self.rows)) + " saved requests." if self.rows else
                   "No saved requests yet. Use Review for GitHub in the rule builder to submit your first review.")
        if self.issues: message += "\n" + "\n".join(self.issues[:3]) + ("\nAdditional files need attention." if len(self.issues) > 3 else "")
        self.notice.set(message); self.selection_changed()

    def selection_changed(self, event=None):
        selected = self.table.selection()
        row = self.visible.get(selected[0]) if selected else None
        self.open_button.configure(state="normal" if row and not self.app.busy else "disabled")
        self.detail.set(row["rule_path"] + "\nRequest: " + row["request_id"] if row else "Select a request to inspect its saved review.")

    def open_selected(self):
        if self.app.busy: return
        selected = self.table.selection()
        row = self.visible.get(selected[0]) if selected else None
        if not row: return
        try:
            record = read_entry(row["file"])
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError):
            self.notice.set("Could not open this saved request. Refresh the list or select the original request file.")
            return
        ReviewDialog(self.builder, record)
        self.window.destroy()

    def open_file(self):
        if self.app.busy: return
        self.window.destroy()
        self.builder.recover_review()
