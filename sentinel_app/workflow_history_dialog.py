"""Search saved preview and deployment requests and refresh their run status on request."""
import json
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk, filedialog
import webbrowser

from . import ui_kit as kit
from .desktop_theme import COLORS, FONTS, px
from .workflow_history import list_receipts, load_receipt, summary, workflow_url, WorkflowHistoryService


def plural(count, word):
    return f"{count} {word}{'s' if count != 1 else ''}"


def parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def local_time(value):
    """A receipt's UTC time shown in local time, '2026-11-02 15:20' (it sorts as text); other text unchanged."""
    moment = parse_time(value)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else str(value or "")


def utc_time(value):
    """'2026-11-02 14:20 UTC', for the tooltip on a local time; '' when the value isn't a time."""
    moment = parse_time(value)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if moment else ""


def cell_tooltips(tree, texts):
    """Show ``texts[column](iid)`` beside the pointer after it rests on a cell of that column (the UTC
    time behind a local one). The timer is registered on the root, where tests cancel timers."""
    state = dict(cell=None, timer=None, window=None)
    root = tree._root()
    def hide(event=None):
        if state["timer"] is not None:
            root.after_cancel(state["timer"]); state["timer"] = None
        if state["window"] is not None:
            try: state["window"].destroy()
            except tk.TclError: pass
            state["window"] = None
    def show(x, y, text):
        state["timer"] = None
        if not tree.winfo_exists(): return
        window = state["window"] = tk.Toplevel(tree); window.overrideredirect(True)
        try: window.attributes("-topmost", True)
        except tk.TclError: pass
        window.configure(bg=COLORS["line_strong"])
        tk.Label(window, text=text, font=FONTS["small"], bg=COLORS["card"], fg=COLORS["ink"], padx=px(8), pady=px(4),
                 bd=0).pack(padx=1, pady=1)
        window.geometry(f"+{x}+{y}")
    def motion(event):
        row, column = tree.identify_row(event.y), tree.identify_column(event.x)
        key = tree.column(column, "id") if row and column not in ("", "#0") else None
        if (row, key) == state["cell"]: return
        hide(); state["cell"] = (row, key)
        text = texts[key](row) if key in texts else ""
        if text: state["timer"] = root.after(600, lambda: show(event.x_root + px(12), event.y_root + px(18), text))
    def leave(event=None):
        hide(); state["cell"] = None
    tree.bind("<Motion>", motion, add="+")
    for sequence in ("<Leave>", "<ButtonPress>", "<Destroy>"):
        tree.bind(sequence, leave, add="+")


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
        kit.placeholder(self.search_box, self.search, "Search by rule, client, workspace or library", search=True)
        self.mode = tk.StringVar(value="All requests")
        self.mode_box = ttk.Combobox(bar, textvariable=self.mode, values=("All requests", "Preview", "Deploy"), state="readonly", width=14)
        self.mode_box.pack(side="right"); self.mode_box.bind("<MouseWheel>", lambda event: "break")
        # Bottom-anchored controls are packed first so they always have room; the lists share what remains.
        self.actions, buttons = kit.action_row(body, primary=("Refresh status", self.refresh_status), close=("Close", self.close),
                                               aux=(("Open in GitHub", self.open_run), ("Reload", self.reload),
                                                    ("Open receipt file…", self.open_file)))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.status_button, self.close_button, self.open_button, self.reload_button, self.file_button = buttons
        self.buttons = (self.reload_button, self.status_button, self.open_button, self.file_button, self.close_button)
        self.notice = tk.StringVar(value="Loading saved requests…")
        self.banner = kit.Banner(body, self.notice); self.banner.pack(side="bottom", fill="x", pady=(px(10), 0))
        # Shown between the banner and the buttons while the selected request has run receipts, so the
        # details keep their room (selected()). A fixed two rows (more scroll), so arrowing through requests
        # with one batch or three doesn't make the list above grow and shrink.
        self.runs_frame, self.runs, _ = kit.table(body, (("batch", "Batch", 60, False), ("run", "Run ID", 105, False),
                                                         ("state", "Last checked result", 465, True),
                                                         ("checked", "Checked", 150, False)), height=2)
        self.runs_label = ttk.Label(body, text="Runs", style="Section.TLabel")
        frame, self.table, self.empty = kit.table(body, (("mode", "Type", 75, False), ("rule", "Rule", 190, True),
                                                         ("repo", "Library", 180, True), ("clients", "Workspaces", 90, False),
                                                         ("state", "Saved status", 240, True), ("created", "Requested", 140, False)),
                                                  height=6, sort=True)
        frame.pack(fill="both", expand=True)
        ttk.Label(body, text="Details", style="Section.TLabel").pack(anchor="w", pady=(px(10), px(6)))
        detail_frame, self.detail = kit.text_area(body, 8, readonly=True,
                                                  placeholder="Select a saved request to see its clients and run receipts.")
        detail_frame.pack(fill="both", expand=True)
        # Packed first to keep their room at the bottom; Tab reaches them after the details, as they read.
        self.runs_label.lift(); self.runs_frame.lift(); self.banner.lift(); self.actions.lift()
        # Times show in local time; pointing at one shows it in UTC, as the receipts store it.
        cell_tooltips(self.table, {"created": lambda iid: utc_time(self.visible[iid]["record"]["created_at"])
                                   if iid in self.visible else ""})
        cell_tooltips(self.runs, {"checked": lambda iid: utc_time(self.current["record"]["results"][int(iid)].get("checked_at"))
                                  if self.current else ""})
        # Filter once typing pauses; Down or Enter in the search box filters now, then moves into the list.
        self.filter = kit.debounce(self.window, 150, self.render)
        for key in ("<Down>", "<Return>"): self.search_box.bind(key, lambda event: self.filter.flush(), add="+")
        kit.link_search(self.search_box, self.table)
        self.search.trace_add("write", self.filter)
        self.mode.trace_add("write", lambda *args: self.render())
        self.table.bind("<<TreeviewSelect>>", self.selected)
        kit.place_dialog(self.window, app.root, 1080, 800, 920, 720)
        kit.modal(self.window, app.root, self.close, primary=self.status_button, focus=self.search_box)
        self.controls(); self.reload()

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
        # Refresh status leads only while a request is selected; otherwise no button is blue.
        kit.promote(self.window, *((self.status_button,) if self.current and not busy else (None, self.status_button)))

    def work(self, title, task, done):
        kit.run_in_dialog(self.app, self.window, title, task, done, controls=self.buttons + (self.search_box, self.mode_box),
                          say=self.say, page="rules", after=self.controls, log_as="Workflow history:")

    def reload(self):
        def done(result):
            self.rows, issues = result; self.render()
            message = ("No previews or deployments have been saved on this PC yet." if not self.rows else
                       plural(len(self.rows), "saved request") + ". Select one, then Refresh status to read its latest "
                       "result from GitHub.")
            self.say(message + (" " + " ".join(issues[:3]) if issues else ""), "warning" if issues else "info")
        self.work("Reading the saved receipts on this PC…", lambda: list_receipts(self.folder), done)

    def render(self):
        self.filter.cancel()
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
                                                            len(p["targets"]), summary(record), local_time(record["created_at"])))
        kit.sort_items(self.table)  # Keep the order chosen with the headings.
        if self.visible: self.empty.hide()
        elif self.rows: self.empty.show("No requests match your search.", ("Clear search", lambda: self.search.set("")))
        else: self.empty.show("No previews or deployments have been requested from this PC yet.")
        self.selected()

    def selected(self, event=None):
        selection = self.table.selection(); self.current = self.visible.get(selection[0]) if selection else None
        self.runs.delete(*self.runs.get_children())
        text = ""  # The placeholder says what appears here.
        if self.current:
            r = self.current["record"]; p = r["plan"]
            lines = ["Library: " + p["repository"], "Requested by: " + p["identity"],
                     "Requested: " + local_time(r["created_at"]) + (" (" + utc_time(r["created_at"]) + ")" if utc_time(r["created_at"]) else ""),
                     "Request ID: " + r["request_id"],
                     "Rule: " + p["rule_path"], "Checked against main revision: " + p["revision"], "Receipt: " + str(self.current["file"]),
                     "", "Saved intended workspaces (a successful workflow is not a fresh Sentinel inventory):"]
            for c in p["clients"]:
                lines.extend([c["name"] + " | " + c["workspace"] + " | " + c["target"] + " | rule " + c["rule_state"],
                              "Subscription: " + c["subscription_id"] + " | Resource group: " + c["resource_group"]])
            for i, result in enumerate(r["results"]):
                state = result.get("history_error") or result["state"]
                self.runs.insert("", "end", iid=str(i), values=(i + 1, result.get("run_id", "Unknown"), state,
                                                                local_time(result["checked_at"]) if result.get("checked_at")
                                                                else "Not checked"))
                lines.extend(["", "Batch " + str(i + 1) + ": " + json.dumps(result["inputs"]),
                              "Actual run revision: " + result.get("actual_revision", "Not checked")])
                if not result.get("run_id"):
                    lines.append("No confirmed run ID. Check GitHub Actions before assuming the request failed; do not retry blindly.")
            text = "\n".join(lines)
        count = len(self.runs.get_children())
        if count and not self.runs_frame.winfo_manager():
            self.runs_frame.pack(side="bottom", fill="x", before=self.banner)
            self.runs_label.pack(side="bottom", anchor="w", pady=(px(10), px(6)), before=self.banner)
        elif not count:
            self.runs_frame.pack_forget(); self.runs_label.pack_forget()
        kit.set_text(self.detail, text)
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
