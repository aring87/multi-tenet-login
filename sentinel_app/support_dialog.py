"""Check this PC: read-only checks of what Sentinel Workspace needs, each with a plain fix."""
import threading
import tkinter as tk
from tkinter import ttk

from .desktop_theme import COLORS, px
from . import support_tools as support
from . import ui_kit as kit

WIDTH = 620
ACTIONS = dict(github_login="Sign in to GitHub", open_update_folder="Open folder")


class CheckDialog:
    """One row per check (Azure CLI, GitHub CLI, GitHub sign-in, data folder, app version): what was
    found, OK or Needs action, and what to do. Rows fill in as each check finishes."""

    def __init__(self, app):
        self.app, self.checks, self.running, self.again = app, {}, False, False
        self.window = window = tk.Toplevel(app.root)
        window.title("Check this PC"); window.resizable(False, False); window.configure(bg=COLORS["card"])
        body = ttk.Frame(window, style="Card.TFrame", padding=(kit.space("xl"), px(22), kit.space("xl"), px(20)))
        body.pack(fill="both", expand=True)
        head = ttk.Frame(body, style="Card.TFrame"); head.pack(fill="x")
        head.columnconfigure(1, weight=1)
        kit.icon_tile(head, "check", "info").grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, px(14)))
        ttk.Label(head, text="Check this PC", style="CardTitle.TLabel").grid(row=0, column=1, sticky="w")
        kit.wrap_to_parent(ttk.Label(head, text="Read-only checks of what Sentinel Workspace needs. Nothing on this "
                                                "PC or in any tenant is changed.", style="CardSubtitle.TLabel",
                                     justify="left", width=1, wraplength=px(WIDTH - 110)), minimum=px(200)).grid(
            row=1, column=1, sticky="ew", pady=(px(2), 0))
        self.summary = tk.StringVar()
        self.banner = kit.Banner(body, self.summary, "info")
        self.banner.pack(fill="x", pady=(px(16), px(6)))
        self.rows = {}
        for index, (key, title) in enumerate(support.CHECKS):
            if index: kit.divider(body, 10)
            self.rows[key] = self.row(body, title)
        self.actions, buttons = kit.action_row(body, secondary=(("Check again", self.refresh),),
                                               close=("Close", self.close), aux=(("Copy support info", self.copy),))
        self.actions.pack(fill="x", pady=(px(20), 0))
        self.again_button, self.close_button, self.copy_button = buttons
        kit.place_dialog(window, app.root, width=WIDTH)
        kit.modal(window, app.root, self.close, primary=self.close_button, focus=self.close_button)
        self.refresh()

    def row(self, parent, title):
        frame = ttk.Frame(parent, style="Card.TFrame"); frame.pack(fill="x")
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=title, style="Field.TLabel").grid(row=0, column=0, sticky="w")
        pill = kit.Pill(frame); pill.grid(row=0, column=1, sticky="ne", padx=(px(12), 0))
        # width=1: the column, not the text, sets the width (see Card); the text wraps to it.
        detail = kit.wrap_to_parent(ttk.Label(frame, style="Body.TLabel", justify="left", width=1,
                                              wraplength=px(WIDTH - 190)), minimum=px(200))
        detail.grid(row=1, column=0, sticky="ew", pady=(px(3), 0))
        button = ttk.Button(frame, style="Small.Secondary.TButton")
        return dict(pill=pill, detail=detail, button=button)

    # ---------------------------------------------------------------- checks
    def refresh(self):
        """Run every check again on a worker thread; the rows say Checking… until each one is back.
        Asked while a run is going (the update folder was just set), it runs again after that one."""
        if self.running:
            self.again = True; return
        self.running, self.checks = True, {}
        for row in self.rows.values():
            row["pill"].set("Checking…", "neutral"); row["detail"].configure(text="Checking…")
            row["button"].grid_remove()
        self.banner.tone("info"); self.summary.set("Checking this PC. Azure CLI can take a few seconds to answer.")
        for button in (self.again_button, self.copy_button): button.configure(state="disabled")
        data, folder = self.app.data_folder(), self.app.update_folder()
        def run():
            try:
                support.run_checks(data, folder, report=lambda check: self.app.call_soon(lambda: self.show(check)))
            except Exception as error:  # The rows left saying Checking… are named in the banner.
                self.app.log("Check this PC stopped: " + (str(error) or type(error).__name__))
            finally:
                self.app.call_soon(self.finished)
        threading.Thread(target=run, daemon=True).start()

    def show(self, check):
        if not self.window.winfo_exists(): return
        self.checks[check.key] = check
        row = self.rows[check.key]
        row["pill"].set("OK" if check.ok else "Needs action", "success" if check.ok else "warning")
        row["detail"].configure(text=check.detail)
        label = self.action_label(check)
        if label:
            row["button"].configure(text=label, command=lambda c=check: self.act(c))
            row["button"].grid(row=1, column=1, sticky="ne", padx=(px(12), 0), pady=(px(3), 0))
        else:
            row["button"].grid_remove()
        self.fit()

    def action_label(self, check):
        if check.action == "set_update_folder":
            return "Change folder…" if self.app.update_folder() else "Set update folder…"
        return ACTIONS.get(check.action, "")

    def finished(self):
        self.running = False
        if not self.window.winfo_exists(): return
        if self.again:
            self.again = False; self.refresh(); return
        for button in (self.again_button, self.copy_button): button.configure(state="normal")
        missing = [title for key, title in support.CHECKS if key not in self.checks]
        failing = [check for check in self.checks.values() if not check.ok]
        if missing:
            self.banner.tone("danger")
            self.summary.set("Some checks couldn't run: " + ", ".join(missing) + ". Select Check again.")
        elif failing:
            self.banner.tone("warning")
            count = len(failing)
            self.summary.set(f"{count} check{' needs' if count == 1 else 's need'} action. Each one says what to do.")
        else:
            self.banner.tone("success"); self.summary.set("Everything Sentinel Workspace needs is in place.")
        self.fit()

    def fit(self):
        """Grow or shrink to the content's height (a fix can take two lines), keeping the width."""
        window = self.window
        window.update_idletasks()
        width = window.winfo_width() if window.winfo_ismapped() else window.winfo_reqwidth()
        _, _, _, area_h = kit.work_area(window, window.winfo_rootx() + width // 2, window.winfo_rooty())
        window.geometry(f"{width}x{min(window.winfo_reqheight(), area_h - px(60))}")

    # ---------------------------------------------------------------- actions
    def act(self, check):
        if check.action == "github_login":
            self.close(); self.app.github_login()
        elif check.action == "open_update_folder":
            self.app.open_path(self.app.update_folder(), "Open update folder")
        elif check.action == "set_update_folder":
            if self.app.ask_update_folder(self.window): self.refresh()
            self.regrab()

    def copy(self):
        """Copy the support info made from these results (the dialog's own checks, not a second run)."""
        checks = [self.checks[key] for key, _ in support.CHECKS if key in self.checks]
        self.app.show_support_info(self.app.support_info(checks), parent=self.window)
        self.regrab()

    def regrab(self):
        """A dialog opened over this one took the input grab; take it back once it has closed."""
        if self.window.winfo_exists():
            try: self.window.grab_set(); self.close_button.focus_set()
            except tk.TclError: pass

    def close(self):
        self.window.destroy()
