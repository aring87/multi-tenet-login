"""Local submission inventory ("My submissions"). Listing and reopening do not contact
GitHub; the latest approval status is read separately, with GET requests only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import re
import stat
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import ui_kit as kit
from .desktop_theme import COLORS, px
from .repository_reviews import api, load_request
from .rule_review_dialog import ReviewDialog

MAX_REQUESTS = 200
MAX_STATUS_CHECKS = 30
PULL_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)")
NOT_FINISHED = "Not finished — open to resume"
# A mark before each status so the list reads at a glance.
STATUS_MARKS = {"Merged": "✓", "Approved — ready to merge": "✓", "Changes requested": "⚠",
                "Draft — open in GitHub and select Ready for review": "✎", NOT_FINISHED: "○",
                "Waiting for a teammate's review": "…", "Closed without merging": "✕"}


def pull_status(url, request=None):
    """Plain-language state of a submission's pull request, read from GitHub."""
    request = request or api
    match = PULL_URL.fullmatch(url or "")
    if not match:
        raise ValueError("Unrecognized pull request link.")
    prefix = "repos/" + match.group(1) + "/pulls/" + match.group(2)
    pull = request("GET", prefix)
    if pull.get("merged") or pull.get("merged_at"):
        return "Merged"
    if pull.get("state") == "closed":
        return "Closed without merging"
    if pull.get("draft"):
        return "Draft — open in GitHub and select Ready for review"
    latest = {}  # Each reviewer's most recent decision counts.
    for review in request("GET", prefix + "/reviews?per_page=100"):
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[(review.get("user") or {}).get("login")] = review["state"]
    if "CHANGES_REQUESTED" in latest.values():
        return "Changes requested"
    if "APPROVED" in latest.values():
        return "Approved — ready to merge"
    return "Waiting for a teammate's review"


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
        issues.append("Showing the newest " + str(MAX_REQUESTS) + " saved requests. Use Open from file for older requests.")
    rows = []
    for modified, path in candidates[:MAX_REQUESTS]:
        try:
            record = read_entry(path)
            plan = record["plan"]
            rows.append(dict(file=path, name=plan["name"], repository=plan["repository"],
                rule_path=plan["path"], request_id=plan["request_id"], pull_request=record.get("pull_request"),
                modified=datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M"),
                status="Submitted — awaiting approval" if record.get("pull_request") else NOT_FINISHED))
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
            issues.append(path.name + ": unreadable or invalid request; the file was left unchanged.")
    return rows, issues


class ReviewHistoryDialog:
    def __init__(self, builder):
        self.builder, self.app = builder, builder.app
        self.folder = builder.data_dir / "review-requests"
        self.rows, self.visible, self.issues = [], {}, []
        self.status_reader = pull_status
        self.window = tk.Toplevel(self.app.root)
        self.window.title("My submissions"); self.window.configure(bg=COLORS["card"])
        body = ttk.Frame(self.window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="My submissions", style="CardTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(body, text="Rules you submitted for approval from this PC. Open one to see its changes "
                                                "or finish an unfinished submission; Open in GitHub shows the approval.",
                                     style="CardSubtitle.TLabel", justify="left", width=1), minimum=px(200)).pack(
            fill="x", pady=(px(2), px(14)))
        self.progress = ttk.Progressbar(body, mode="indeterminate", maximum=100)
        self.notice = tk.StringVar()
        self.banner = kit.Banner(body, self.notice); self.banner.pack(fill="x", pady=(0, px(12)))
        self.search = tk.StringVar()
        self.search_box = ttk.Entry(body, textvariable=self.search); self.search_box.pack(fill="x", pady=(0, px(10)))
        kit.placeholder(self.search_box, self.search, "Search by rule, library or request ID")
        # Bottom-anchored controls are packed first so they always have room; the list takes what remains.
        self.actions, buttons = kit.action_row(body, primary=("View changes", self.open_selected),
                                               secondary=(("Open in GitHub", self.open_github), ("Refresh", self.refresh),
                                                          ("Open from file…", self.open_file)),
                                               close=("Close", self.close))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.open_button, self.github_button, self.refresh_button, self.file_button, self.close_button = buttons
        self.detail = tk.StringVar(value="Select a submission to see its details.")
        kit.wrap_to_parent(ttk.Label(body, textvariable=self.detail, style="Hint.TLabel", justify="left", width=1),
                           minimum=px(200)).pack(side="bottom", fill="x", pady=(px(10), 0))
        frame, self.table, self.empty = kit.table(body, (("name", "Rule", 250, True), ("repository", "Library", 170, False),
                                                        ("status", "Status", 300, True), ("modified", "Last updated", 125, False)),
                                                  height=5)
        frame.pack(fill="both", expand=True)
        self.table.tag_configure("quiet", foreground=COLORS["muted"])
        kit.tooltip(self.refresh_button, "Read the saved submissions again and ask GitHub for their status (F5)")
        self.search.trace_add("write", lambda *args: self.render())
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        self.table.bind("<Double-1>", lambda event: self.open_selected())
        self.window.bind("<F5>", lambda event: self.refresh())
        kit.place_dialog(self.window, self.app.root, 980, 660, 760, 420)
        kit.modal(self.window, self.app.root, self.close, primary=self.open_button, focus=self.search_box)
        self.refresh()

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()

    def set_enabled(self, enabled):
        """One switch for every control, so nothing looks clickable while a step is running."""
        selected = self.table.selection()
        row = self.visible.get(selected[0]) if selected else None
        for button in (self.refresh_button, self.file_button, self.close_button):
            button.configure(state="normal" if enabled else "disabled")
        self.search_box.configure(state="normal" if enabled else "disabled")
        self.open_button.configure(state="normal" if enabled and row else "disabled")
        self.github_button.configure(state="normal" if enabled and row and row.get("pull_request") else "disabled")

    def refresh(self):
        if self.app.busy: return
        def task():
            try: return list_requests(self.folder), None
            except (OSError, ValueError) as error: return None, str(error)
        def done(result):
            value, error = result
            self.rows, self.issues = value if value else ([], [error])
            self.render()
            self.check_status()
        kit.run_in_dialog(self.app, self.window, "Reading saved submissions…", task, done,
                          controls=(self.refresh_button, self.open_button, self.github_button, self.file_button,
                                    self.close_button, self.search_box),
                          say=self.say, page="builder", progress=self.progress, after=lambda: self.set_enabled(True))

    def check_status(self):
        """Replace the local status of submitted rows with what GitHub reports now."""
        rows = [row for row in self.rows if row.get("pull_request")][:MAX_STATUS_CHECKS]
        if not rows or self.app.busy: return
        reader = self.status_reader
        def read(row):
            try: return reader(row["pull_request"])
            except Exception: return "Couldn't check — open in GitHub"
        def task():
            with ThreadPoolExecutor(max_workers=8) as pool:
                return list(pool.map(read, rows))
        def done(statuses):
            for row, status in zip(rows, statuses): row["status"] = status
            self.render()
        kit.run_in_dialog(self.app, self.window, "Checking GitHub for the latest status of your submissions…", task, done,
                          controls=(self.refresh_button, self.open_button, self.github_button, self.file_button,
                                    self.close_button, self.search_box),
                          say=self.say, page="builder", progress=self.progress, after=lambda: self.set_enabled(True))

    def render(self):
        self.table.delete(*self.table.get_children()); self.visible = {}
        term = self.search.get().strip().casefold()
        for index, row in enumerate(self.rows):
            if term and term not in " ".join(str(row[key]) for key in ("name", "repository", "rule_path", "request_id")).casefold():
                continue
            item = str(index); self.visible[item] = row
            status = STATUS_MARKS.get(row["status"], "•") + "  " + row["status"]
            self.table.insert("", "end", iid=item, values=(row["name"], row["repository"], status, row["modified"]),
                              tags=("quiet",) if row["status"] == "Closed without merging" else ())
        if self.visible: self.empty.hide()
        elif self.rows: self.empty.show("No submissions match your search.", ("Clear search", lambda: self.search.set("")))
        else: self.empty.show("No submissions yet. Use Submit for approval in the Rule builder to send your first rule.")
        message = (str(len(self.visible)) + " of " + str(len(self.rows)) + " submissions." if self.rows else
                   "Submissions you make will be listed here.")
        tone = "info"
        if self.issues:
            message += " " + " ".join(self.issues[:3]) + (" Additional files need attention." if len(self.issues) > 3 else "")
            tone = "warning"
        self.say(message, tone); self.selection_changed()

    def selection_changed(self, event=None):
        selected = self.table.selection()
        row = self.visible.get(selected[0]) if selected else None
        if not self.app.busy: self.set_enabled(True)
        self.detail.set((row["rule_path"] + "   ·   request " + row["request_id"][:8] + "…") if row else
                        "Select a submission to see its details.")

    def open_selected(self):
        if self.app.busy: return
        selected = self.table.selection()
        row = self.visible.get(selected[0]) if selected else None
        if not row: return
        try:
            record = read_entry(row["file"])
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError):
            self.say("That submission couldn't be opened. Select Refresh, or choose its file with Open from file.", "danger")
            return
        ReviewDialog(self.builder, record)
        self.window.destroy()

    def open_github(self):
        selected = self.table.selection()
        row = self.visible.get(selected[0]) if selected else None
        if row and PULL_URL.fullmatch(row.get("pull_request") or ""):
            webbrowser.open(row["pull_request"])

    def open_file(self):
        if self.app.busy: return
        self.window.destroy()
        self.builder.recover_review()
