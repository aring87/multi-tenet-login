"""Sentinel Workspace: local Windows desktop interface."""
import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import ttk, messagebox
from . import __version__
from .desktop_theme import configure_theme, app_icon, COLORS, FONTS, TONES, px
from . import ui_kit as kit
from .auth_recovery import AuthenticationRequired, SignInCancelled
from .signin_process import check_cancelled
from .rules_page import RulesPage
from .catalog_page import CatalogPage
from .rule_builder_page import RuleBuilderPage
from .signin_window import create_handoff
from .desktop_backend import (BASE, DATA, Session, Stop, require, guid, validate_tenant_hint, CYBERQP_PORTALS)

# Only the GitHub repository is edited here. Any other keys found in an existing
# settings.json (the command-line onboarding tools keep theirs there) are preserved verbatim.
SETTINGS_KEYS = ("github_owner", "github_repo")

# Page keys in notebook order, with navigation and header copy.
PAGES = ("connect", "rules", "builder")
NAVIGATION = dict(
    connect=("Connect", "Pick a client and sign in", "connect",
             "Connect", "Sign in with your Microsoft account, then choose the client's workspace."),
    rules=("Rules", "Live rules and the library", "rules",
           "Rules", "Browse the live rules in the selected workspace, or the rule library."),
    builder=("Rule builder", "Create and edit rules", "builder",
             "Rule builder", "Create or edit a rule and submit it for a teammate's approval."))

EVENT_POLL_MS = 100
SIGNIN_FOCUS_SECONDS = 90
SIGNIN_FOCUS_POLL_MS = 750
GITHUB_LOGIN_TIMEOUT_SECONDS = 900
STATUS_PREVIEW_CHARS = 180
CONTENT_MAX_WIDTH = 1320
SCROLL_TARGET_OFFSET = 140
PROGRESS_REPORT_EVERY = 5
FLASH_SECONDS = 8


def write_json(path, value):
    """Write JSON through a temporary file so a failure never truncates the original."""
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


def set_aside(path):
    """Rename an unreadable file out of the way instead of overwriting it later."""
    target = path.with_name(path.name + ".corrupt-" + datetime.now().strftime("%Y%m%d%H%M%S"))
    try: path.replace(target); return target
    except OSError: return None


class App:
    def __init__(self, root):
        self.root = root
        root.title("Sentinel Workspace")
        configure_theme(root)
        self.state_file = DATA / "ui-state.json"
        DATA.mkdir(exist_ok=True)
        self.ui_state = self.read_state()
        self.apply_geometry(self.ui_state.get("geometry"))
        self.events = queue.Queue()
        self.busy = False
        self.session = None
        self.auth_hint = None
        self.subscriptions = []
        self.workspaces = []
        self.workspace = None
        self.clientfile = DATA / "clients.json"
        self.settingsfile = DATA / "settings.json"
        self.startup_notes = []
        self.clients = self.read_clients()
        self.settings = self.read_settings()
        self.vars = {}
        self.controls = []
        self.operation_page = "connect"
        self.operation_title = ""
        self.flash_timer = None
        self.signin_handoff = None
        self.signin_timer = None
        self.signin_active = False
        self.signin_cancel = None
        self.auth_recovery_attempts = set()
        self.build()
        remembered = self.ui_state.get("client")
        if isinstance(remembered, str) and any(c["name"] == remembered for c in self.clients):
            self.vars["client_name"].set(remembered)
        self.refresh_clients()
        self.refresh_first_run()
        for note in self.startup_notes: self.log(note)
        if self.startup_notes: self.status.set(self.startup_notes[-1])
        if "/" in self.github_repository():
            self.root.after(300, self.rule_builder.check_github)
        self.root.after(EVENT_POLL_MS, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    # ---------------------------------------------------------------- storage
    def read_state(self):
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self):
        state = dict(self.ui_state)
        try:
            if self.root.state() == "normal": state["geometry"] = self.root.geometry()
            state["client"] = self.vars["client_name"].get()
            state["rule_view"] = self.rule_view.get()
            write_json(self.state_file, state)
        except (OSError, tk.TclError):
            pass

    def apply_geometry(self, remembered):
        width, height = px(1360), px(900)
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = y = None
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", str(remembered or ""))
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            x, y = int(match.group(3)), int(match.group(4))
        width = max(px(1120), min(width, screen_w - px(16)))
        height = max(px(740), min(height, screen_h - px(80)))
        if x is None or not (0 <= x <= screen_w - px(200)) or not (0 <= y <= screen_h - px(200)):
            x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 2 - px(20))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(px(1120), screen_w), min(px(740), screen_h))

    def read_clients(self):
        if not self.clientfile.exists(): return []
        try:
            clients = json.loads(self.clientfile.read_text(encoding="utf-8"))
            require(isinstance(clients, list), "The saved client list is not a list.")
            for c in clients:
                require(isinstance(c, dict) and {"name", "tenant"} <= set(c) <= {"name", "tenant", "slug"}
                        and all(isinstance(v, str) for v in c.values()), "A saved client entry is incomplete.")
            return [dict(name=c["name"], tenant=c["tenant"]) for c in clients]
        except Exception as error:
            kept = set_aside(self.clientfile)
            self.startup_notes.append("The saved client list couldn't be read" +
                                      (f"; it was kept as {kept.name}" if kept else "") + ". Add your clients again.")
            self.log(str(error))
            return []

    def read_settings(self):
        if not self.settingsfile.exists(): return {}
        try:
            value = json.loads(self.settingsfile.read_text(encoding="utf-8"))
            require(isinstance(value, dict), "Saved settings are not an object.")
        except Exception as error:
            kept = set_aside(self.settingsfile)
            self.startup_notes.append("Saved settings couldn't be read" +
                                      (f"; they were kept as {kept.name}" if kept else "") + ". Set the rule library again.")
            self.log(str(error))
            return {}
        skipped = [k for k, v in value.items() if not isinstance(v, str)]
        if skipped: self.startup_notes.append("Ignored settings that aren't text: " + ", ".join(skipped) + ".")
        return {k: v for k, v in value.items() if isinstance(v, str)}

    def save_settings(self):
        value = dict(self.settings)
        value.update({k: self.vars[k].get().strip() for k in SETTINGS_KEYS if k in self.vars})
        try:
            write_json(self.settingsfile, value)
        except OSError as error:
            messagebox.showerror("Save rule library", "The settings couldn't be saved.\n\n" + str(error), parent=self.root)
            return
        self.settings = value
        for key, current in value.items():
            if key in self.vars:
                self.vars[key].set(current)
        self.flash("Rule library saved.")
        self.refresh_first_run()
        self.rule_builder.github_user = None
        if "/" in self.github_repository():
            self.github_status.set("Checking GitHub sign-in…")
            self.rule_builder.check_github(force=True)
        else:
            self.github_status.set("Enter the organization and repository, then select Save.")
            self.github_pill.set("Not set")

    def github_repository(self):
        return "/".join(self.vars[k].get().strip() for k in ("github_owner", "github_repo")).strip("/")

    # ---------------------------------------------------------------- helpers
    def var(self, name, default=""):
        self.vars[name] = tk.StringVar(value=default)
        return self.vars[name]

    def button(self, parent, text, command, style="Secondary.TButton"):
        w = ttk.Button(parent, text=text, command=command, style=style)
        self.controls.append((w, "normal"))
        return w

    def form(self, parent, label, name, default="", values=None, hint=None, pady=14):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.pack(fill="x", pady=(0, px(pady)))
        kit.field_label(box, label, hint)
        v = self.var(name, default)
        if values is None:
            w = ttk.Entry(box, textvariable=v)
        else:
            w = ttk.Combobox(box, textvariable=v, values=values, state="readonly")
        w.pack(fill="x")
        self.controls.append((w, "readonly" if values is not None else "normal"))
        return w

    def form_pair(self, parent, fields, weights=(1, 1)):
        row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x")
        for index, (field, weight) in enumerate(zip(fields, weights)):
            row.columnconfigure(index, weight=weight, uniform="pair" if weights[0] == weights[1] else "")
            column = ttk.Frame(row, style="Card.TFrame")
            column.grid(row=0, column=index, sticky="new", padx=(0, px(12)) if index == 0 else 0)
            self.form(column, *field)
        return row

    def hint(self, parent, text, pady=(0, 14)):
        label = kit.wrap_to_parent(ttk.Label(parent, text=text, style="Hint.TLabel", justify="left", wraplength=px(320)))
        label.pack(fill="x", pady=tuple(px(p) for p in pady))
        return label

    def current_page(self):
        return PAGES[self.tabs.index("current")] if hasattr(self, "tabs") else PAGES[0]

    def show_page(self, name):
        self.tabs.select(PAGES.index(name))

    def flash(self, text, tone="success", seconds=FLASH_SECONDS):
        """Confirm something in the status bar, then return to Ready."""
        if self.flash_timer is not None:
            self.root.after_cancel(self.flash_timer); self.flash_timer = None
        self.status.set(text)
        self.activity_badge.set("Done" if tone == "success" else "Note", tone)
        def revert():
            self.flash_timer = None
            if not self.busy:
                self.status.set("Ready."); self.activity_badge.set("Ready", "success")
        self.flash_timer = self.root.after(int(seconds * 1000), revert)

    # ------------------------------------------------------------------ shell
    def build(self):
        self.icon = app_icon(self.root)
        self.root.iconphoto(True, self.icon)
        self.build_sidebar()
        main = tk.Frame(self.root, bg=COLORS["canvas"]); main.pack(side="left", fill="both", expand=True)
        self.build_header(main)
        self.build_statusbar(main)
        # Pages may pin a footer here so their primary actions stay visible while the page scrolls.
        self.footer_slot = tk.Frame(main, bg=COLORS["canvas"]); self.footer_slot.pack(side="bottom", fill="x")
        self.tabs = ttk.Notebook(main, style="Hidden.TNotebook", takefocus=0); self.tabs.pack(fill="both", expand=True)
        self.page_contents, self.page_canvases = [], []
        for name in PAGES:
            page = ttk.Frame(self.tabs); self.tabs.add(page, text=NAVIGATION[name][0])
            canvas = tk.Canvas(page, bg=COLORS["canvas"], highlightthickness=0, bd=0)
            scroll = ttk.Scrollbar(page, orient="vertical", command=canvas.yview, style="Canvas.Vertical.TScrollbar")
            canvas.pack(side="left", fill="both", expand=True)
            canvas.configure(yscrollcommand=kit.autohide(scroll, lambda s=scroll, c=canvas: s.pack(side="right", fill="y", before=c)))
            content = ttk.Frame(canvas, padding=(kit.space("page"), kit.space("xl"), kit.space("page"), kit.space("page")))
            window = canvas.create_window((0, 0), window=content, anchor="nw")
            content.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda e, c=canvas, w=window: self.fit_page(c, w, e.width))
            self.page_canvases.append(canvas); self.page_contents.append(content)
        connect, rules, builder = self.page_contents
        self.build_connect(connect)
        self.build_rules(rules)
        self.rule_builder = RuleBuilderPage(self, builder, DATA)
        self.last_page = None
        self.tabs.bind("<<NotebookTabChanged>>", self.page_changed)
        self.page_changed()
        self.root.bind("<MouseWheel>", self.scroll_page)
        self.bind_shortcuts()

    @staticmethod
    def fit_page(canvas, window, width):
        # Keep line lengths readable on wide monitors by centring a capped column.
        column = min(width, px(CONTENT_MAX_WIDTH))
        canvas.itemconfigure(window, width=column)
        canvas.coords(window, max(0, (width - column) // 2), 0)

    def bind_shortcuts(self):
        """Windows-conventional shortcuts; dialogs have their own bindings and are unaffected."""
        for index, name in enumerate(PAGES, start=1):
            self.root.bind(f"<Control-Key-{index}>", lambda event, n=name: self.show_page(n))
        self.root.bind("<F5>", lambda event: self.refresh_current())
        self.root.bind("<Control-n>", lambda event: (self.show_page("builder"), self.rule_builder.new()))
        self.root.bind("<Control-s>", lambda event: self.rule_builder.save() if self.current_page() == "builder" else None)
        self.root.bind("<Control-f>", lambda event: self.focus_search())

    def refresh_current(self):
        if self.busy: return
        page = self.current_page()
        if page == "connect":
            if self.session is not None: self.refresh_subscriptions()
        elif page == "rules":
            (self.rules_page.load if self.rule_view.get() == "workspace" else self.catalog_page.load)()
        else:
            self.rule_builder.check_github(force=True)

    def focus_search(self):
        if self.current_page() == "rules":
            page = self.rules_page if self.rule_view.get() == "workspace" else self.catalog_page
            page.search_entry.focus_set(); page.search_entry.select_range(0, "end")
        elif self.current_page() == "connect":
            self.clientbox.focus_set()
        return "break"

    def build_sidebar(self):
        c = COLORS
        side = tk.Frame(self.root, bg=c["sidebar"], width=px(244))
        side.pack(side="left", fill="y"); side.pack_propagate(False)
        brand = tk.Frame(side, bg=c["sidebar"]); brand.pack(fill="x", padx=px(16), pady=(px(26), px(28)))
        logo = tk.Frame(brand, bg=c["accent"], width=px(36), height=px(36)); logo.pack(side="left")
        logo.pack_propagate(False)
        glyph = kit.icon_label(logo, "shield", c["accent"], "#FFFFFF", "icon_large")
        if glyph: glyph.pack(expand=True)
        words = tk.Frame(brand, bg=c["sidebar"]); words.pack(side="left", padx=(px(12), 0))
        tk.Label(words, text="Sentinel Workspace", font=FONTS["brand"], bg=c["sidebar"], fg="#FFFFFF", bd=0).pack(anchor="w")
        tk.Label(words, text="Version " + __version__, font=FONTS["small"], bg=c["sidebar"], fg=c["sidebar_muted"],
                 bd=0).pack(anchor="w")

        footer = tk.Frame(side, bg=c["sidebar"]); footer.pack(side="bottom", fill="x", padx=px(20), pady=(0, px(20)))
        for text, command, tip in (("Help & prerequisites", self.open_help, "Open the desktop guide"),
                                   ("Open app data folder", lambda: os.startfile(str(DATA)), "Clients, drafts and submissions saved on this PC")):
            item = kit.link(footer, text, command, c["sidebar_muted"], c["sidebar_ink"], c["sidebar"],
                            focus_color=c["sidebar_accent"])
            item.pack(fill="x", pady=(px(6), 0))
            kit.tooltip(item, tip)
        session = tk.Frame(side, bg=c["sidebar_hover"], bd=0, highlightthickness=1,
                           highlightbackground=c["sidebar_line"], highlightcolor=c["sidebar_line"])
        session.pack(side="bottom", fill="x", padx=px(14), pady=(0, px(14)))
        top = tk.Frame(session, bg=c["sidebar_hover"]); top.pack(fill="x", padx=px(12), pady=(px(10), 0))
        self.session_dot = tk.Label(top, text="●", font=FONTS["small"], bg=c["sidebar_hover"], fg=c["subtle"], bd=0)
        self.session_dot.pack(side="left")
        self.session_title = tk.Label(top, text="Not signed in", font=FONTS["strong_small"], bg=c["sidebar_hover"],
                                      fg=c["sidebar_ink"], bd=0)
        self.session_title.pack(side="left", padx=(px(6), 0))
        self.session_detail = tk.Label(session, text="", font=FONTS["small"], bg=c["sidebar_hover"],
                                       fg=c["sidebar_muted"], bd=0, justify="left", anchor="w", wraplength=px(196))
        self.session_detail.pack(fill="x", padx=px(12), pady=(px(3), px(10)))
        kit.wrap_to_parent(self.session_detail, minimum=px(120))
        self.signout_link = kit.link(session, "Sign out", lambda: self.logout(), c["sidebar_accent"], "#FFFFFF", c["sidebar_hover"],
                                     font=FONTS["strong_small"], focus_color=c["sidebar_accent"])

        self.nav = []
        for index, name in enumerate(PAGES):
            title, description, icon = NAVIGATION[name][:3]
            row = tk.Frame(side, bg=c["sidebar"], cursor="hand2", takefocus=1, bd=0,
                           highlightthickness=1, highlightbackground=c["sidebar"], highlightcolor=c["sidebar_accent"])
            row.pack(fill="x", padx=px(12), pady=px(2))
            marker = tk.Frame(row, bg=c["sidebar"], width=px(3)); marker.pack(side="left", fill="y")
            glyph = kit.icon_label(row, icon, c["sidebar"], c["sidebar_muted"])
            if glyph: glyph.pack(side="left", padx=(px(12), 0))
            text = tk.Frame(row, bg=c["sidebar"], cursor="hand2"); text.pack(side="left", fill="x", padx=px(12), pady=px(9))
            label = tk.Label(text, text=title, font=FONTS["strong"], bg=c["sidebar"], fg=c["sidebar_ink"],
                             anchor="w", cursor="hand2", bd=0)
            label.pack(anchor="w")
            detail = tk.Label(text, text=description, font=FONTS["small"], bg=c["sidebar"], fg=c["sidebar_muted"],
                              anchor="w", cursor="hand2", bd=0)
            detail.pack(anchor="w")
            parts = [row, marker, text, label, detail] + ([glyph] if glyph else [])
            for part in parts:
                part.bind("<Button-1>", lambda event, n=name: self.show_page(n))
                part.bind("<Enter>", lambda event, i=index: self.paint_nav(i, hover=True))
                part.bind("<Leave>", lambda event, i=index: self.paint_nav(i))
            row.bind("<Return>", lambda event, n=name: self.show_page(n))
            row.bind("<space>", lambda event, n=name: self.show_page(n))
            kit.tooltip(row, f"{title}  (Ctrl+{index + 1})")
            self.nav.append(dict(row=row, title=label, description=detail, marker=marker, parts=parts, icon=glyph))

    @staticmethod
    def open_help():
        os.startfile(str(BASE / "docs" / "DESKTOP-START-HERE.md"))

    def paint_nav(self, index, hover=False):
        c = COLORS
        item = self.nav[index]
        selected = hasattr(self, "tabs") and index == self.tabs.index("current")
        background = c["sidebar_active"] if selected else c["sidebar_hover"] if hover else c["sidebar"]
        for part in item["parts"]:
            if part is not item["marker"]:
                part.configure(bg=background)
        item["row"].configure(highlightbackground=background)
        item["marker"].configure(bg=c["sidebar_accent"] if selected else background)
        item["title"].configure(fg="#FFFFFF" if selected else c["sidebar_ink"])
        if item["icon"]:
            item["icon"].configure(fg=c["sidebar_accent"] if selected else c["sidebar_muted"])

    def build_header(self, main):
        c = COLORS
        header = tk.Frame(main, bg=c["card"]); header.pack(fill="x")
        inner = ttk.Frame(header, style="Card.TFrame", padding=(kit.space("page"), px(20), kit.space("page"), px(18)))
        inner.pack(fill="x")
        inner.columnconfigure(0, weight=1)
        titles = ttk.Frame(inner, style="Card.TFrame"); titles.grid(row=0, column=0, sticky="ew")
        self.page_title = tk.StringVar(); self.page_subtitle = tk.StringVar()
        ttk.Label(titles, textvariable=self.page_title, style="PageTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(titles, textvariable=self.page_subtitle, style="PageSubtitle.TLabel", width=1,
                                     justify="left"), minimum=px(160)).pack(anchor="w", fill="x", pady=(px(3), 0))
        chip = tk.Frame(inner, bg=c["raised"], bd=0, highlightthickness=1,
                        highlightbackground=c["line"], highlightcolor=c["line"])
        chip.grid(row=0, column=1, sticky="ne", padx=(kit.space("xl"), 0))
        self.context_dot = tk.Label(chip, text="●", font=FONTS["small"], bg=c["raised"], fg=c["subtle"], bd=0)
        self.context_dot.pack(side="left", padx=(px(12), px(6)), pady=px(7))
        self.workspace_context = tk.StringVar(value="No workspace selected  ·  Sign in to begin")
        tk.Label(chip, textvariable=self.workspace_context, font=FONTS["small"], bg=c["raised"], fg=c["ink_2"],
                 bd=0, wraplength=px(300), justify="left").pack(side="left", padx=(0, px(12)), pady=px(7))
        tk.Frame(main, bg=c["line"], height=1).pack(fill="x")
        holder = tk.Frame(main, bg=c["canvas"], height=px(3)); holder.pack(fill="x"); holder.pack_propagate(False)
        self.progress = ttk.Progressbar(holder, mode="indeterminate", maximum=100)

    def build_statusbar(self, main):
        c = COLORS
        bar = tk.Frame(main, bg=c["card"]); bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=c["line"], height=1).pack(fill="x")
        row = ttk.Frame(bar, style="Card.TFrame", padding=(kit.space("xl"), kit.space("sm"))); row.pack(fill="x")
        self.activity_badge = kit.Pill(row, "Ready", "success")
        self.activity_badge.pack(side="left", anchor="n")
        self.status = tk.StringVar(value="Ready.")
        self.status_preview = tk.StringVar()
        def show_status(*args):
            # A long diagnostic must not grow the footer; the full text stays in status and the log.
            text = " ".join(self.status.get().split())
            self.status_preview.set(text if len(text) <= STATUS_PREVIEW_CHARS else text[:STATUS_PREVIEW_CHARS - 3] + "…")
        self.status.trace_add("write", show_status); show_status()
        status = ttk.Label(row, textvariable=self.status_preview, style="Muted.TLabel")
        status.pack(side="left", fill="x", expand=True, padx=(px(12), 0))
        kit.wrap_to_parent(status, 0)

    # --------------------------------------------------------------- connect
    def build_connect(self, page):
        c = COLORS
        page.columnconfigure((0, 1), weight=1, uniform="connect")
        gap = kit.space("sm")
        self.first_run_text = tk.StringVar()
        self.first_run_banner = kit.Banner(page, self.first_run_text, "info")
        # Two independent columns, so a tall card on one side leaves no gap on the other.
        left, right = ttk.Frame(page), ttk.Frame(page)
        left.grid(row=1, column=0, sticky="new", padx=(0, gap)); right.grid(row=1, column=1, sticky="new", padx=(gap, 0))

        signin = kit.Card(left, "Sign in", "Choose the client, then sign in with Microsoft.", icon="key")
        signin.frame.pack(fill="x", pady=(0, kit.space("lg")))
        body = signin.body
        head = kit.field_label(body, "Client")
        self.button(head, "+ New client…", self.add_client, "Small.Ghost.TButton").pack(side="right")
        row = ttk.Frame(body, style="Card.TFrame"); row.pack(fill="x", pady=(0, px(14)))
        self.clientbox = ttk.Combobox(row, textvariable=self.var("client_name"), values=[], state="readonly", width=12)
        self.clientbox.pack(side="left", fill="x", expand=True)
        self.controls.append((self.clientbox, "readonly"))
        self.clientbox.bind("<<ComboboxSelected>>", self.client_changed)
        self.client_menu = tk.Menu(self.root, tearoff=0, font=FONTS["body"], bg=c["card"], fg=c["ink"], bd=0,
                                   activebackground=c["accent_soft"], activeforeground=c["ink"])
        self.client_menu.add_command(label="Save this tenant to the client", command=self.save_client)
        self.client_menu.add_separator()
        self.client_menu.add_command(label="Remove client…", command=self.delete_client)
        more = self.button(row, "⋯", lambda: self.show_menu(self.client_menu, more))
        more.configure(width=3); more.pack(side="left", padx=(px(8), 0))
        kit.tooltip(more, "More client actions")
        # The tenant is filled in from the client; the field only opens when it needs attention.
        self.tenant_row = ttk.Frame(body, style="Card.TFrame"); self.tenant_row.pack(fill="x", pady=(0, px(14)))
        self.tenant_summary = tk.StringVar()
        kit.wrap_to_parent(ttk.Label(self.tenant_row, textvariable=self.tenant_summary, style="Hint.TLabel",
                                     justify="left", width=1), minimum=px(120)).pack(side="left", fill="x", expand=True)
        self.tenant_change = ttk.Button(self.tenant_row, text="Change", style="Small.Ghost.TButton",
                                        command=lambda: self.toggle_tenant(True))
        self.tenant_change.pack(side="right"); self.controls.append((self.tenant_change, "normal"))
        self.tenant_box = ttk.Frame(body, style="Card.TFrame")
        tenant = self.form(self.tenant_box, "Tenant ID or domain", "tenant", hint="Optional", pady=6)
        tenant.bind("<Return>", lambda event: self.login())
        self.tenant_entry = tenant
        self.hint(self.tenant_box, "Leave it blank to discover every directory your account can access.")
        self.vars["tenant"].trace_add("write", lambda *args: self.render_tenant())
        kit.field_label(body, "Sign-in method")
        self.var("login_method", "Browser")
        kit.Segmented(body, self.vars["login_method"], [("Browser", "Browser"), ("Windows account window", "Windows account")],
                      enabled=lambda: not self.busy).pack(anchor="w", pady=(0, kit.space("lg")))
        self.signin_main = self.button(body, "Sign in with Microsoft", self.login, "Primary.TButton")
        self.signin_main.pack(fill="x")
        self.signin_panel = tk.Frame(body, bg=c["accent_soft"], bd=0, highlightthickness=1,
                                     highlightbackground=c["accent_line"], highlightcolor=c["accent_line"])
        panel = tk.Frame(self.signin_panel, bg=c["accent_soft"]); panel.pack(fill="x", padx=px(14), pady=px(12))
        tk.Label(panel, text="Waiting for Microsoft sign-in", font=FONTS["strong"], bg=c["accent_soft"],
                 fg=c["ink"], bd=0, anchor="w").pack(fill="x")
        waiting = tk.Label(panel, text="Finish signing in in the browser window. If you closed it, select Cancel sign-in.",
                           font=FONTS["small"], bg=c["accent_soft"], fg=c["ink_2"], bd=0, anchor="w", justify="left",
                           width=1)  # width=1: the wrapped text must not feed its width back into the layout.
        waiting.pack(fill="x", pady=(px(2), px(10))); kit.wrap_to_parent(waiting)
        actions = tk.Frame(panel, bg=c["accent_soft"]); actions.pack(fill="x")
        self.signin_button = ttk.Button(actions, text="Bring window forward", command=self.bring_signin_forward,
                                        state="disabled", style="Small.Secondary.TButton")
        self.signin_button.pack(side="left")
        self.cancel_signin_button = ttk.Button(actions, text="Cancel sign-in", command=self.cancel_signin,
                                               state="disabled", style="Small.Secondary.TButton")
        self.cancel_signin_button.pack(side="left", padx=(px(8), 0))
        kit.divider(body, 14)
        access = ttk.Frame(body, style="Card.TFrame"); access.pack(fill="x")
        self.button(access, "Open CyberQP", self.open_cyberqp, "Small.Secondary.TButton").pack(side="right", anchor="center")
        words = ttk.Frame(access, style="Card.TFrame"); words.pack(side="left", fill="x", expand=True)
        ttk.Label(words, text="Need temporary access?", style="Field.TLabel").pack(anchor="w")
        ttk.Label(words, text="Activate it in CyberQP before signing in.", style="Hint.TLabel").pack(anchor="w", pady=(px(2), 0))

        workspace = kit.Card(right, "Workspace", "Choose the subscription and Log Analytics workspace to work in.",
                             icon="cloud")
        workspace.frame.pack(fill="x", pady=(0, kit.space("lg")))
        self.connection_pill = kit.Pill(workspace.actions, "Not connected")
        self.connection_pill.pack()
        body = workspace.body
        self.connection_note = tk.StringVar()
        kit.wrap_to_parent(ttk.Label(body, textvariable=self.connection_note, style="Body.TLabel",
                                     justify="left", wraplength=px(320))).pack(fill="x", pady=(0, px(14)))
        self.subbox, self.sub_refresh = self.picker(body, "Subscription", "subscription", self.refresh_subscriptions,
                                                    "Reload subscriptions from Azure (F5)")
        self.subbox.bind("<<ComboboxSelected>>", self.subscription_changed)
        self.missing_link = ttk.Button(body, text="Can't see a subscription?", style="Small.Ghost.TButton",
                                       command=self.check_subscription)
        self.wsbox, self.ws_refresh = self.picker(body, "Log Analytics workspace", "workspace", self.discover,
                                                  "Reload the workspaces in this subscription")
        self.wsbox.bind("<<ComboboxSelected>>", self.workspace_changed)
        for combo in (self.clientbox, self.subbox, self.wsbox):
            combo.bind("<MouseWheel>", lambda event: "break")  # Scrolling the page must not change a selection.
        details = tk.Frame(body, bg=c["raised"], bd=0, highlightthickness=1,
                           highlightbackground=c["line"], highlightcolor=c["line"])
        details.pack(fill="x", pady=(px(2), kit.space("lg")))
        inner = ttk.Frame(details, style="Raised.TFrame", padding=(px(16), px(14))); inner.pack(fill="x")
        self.details = kit.KeyValues(inner, surface="Raised", stacked=True)
        self.details_empty = ttk.Label(inner, style="RaisedMuted.TLabel", justify="left", wraplength=px(320))
        kit.wrap_to_parent(self.details_empty)
        self.identity = tk.StringVar(value="No workspace selected yet. Sign in to discover subscriptions and workspaces.")
        self.identity.trace_add("write", lambda *args: (self.render_details(), self.update_workspace_context(),
                                                        self.sync_connection()))
        actions = ttk.Frame(body, style="Card.TFrame"); actions.pack(fill="x")
        # Not in self.controls: sync_connection enables these only once a workspace is selected.
        self.copy_button = ttk.Button(actions, text="Copy details", command=self.copy_workspace, style="Secondary.TButton")
        self.workspace_actions = [
            self.copy_button,
            ttk.Button(actions, text="View analytics rules", command=lambda: self.show_page("rules"), style="Primary.TButton")]
        self.workspace_actions[0].pack(side="left"); self.workspace_actions[1].pack(side="right")

        github = kit.Card(right, "Rule library", "The GitHub repository where rules are stored and approved.",
                          icon="library", tone="neutral")
        github.frame.pack(fill="x")
        self.github_card = github
        self.github_pill = kit.Pill(github.actions, "Not set"); self.github_pill.pack()
        self.form_pair(github.body, [("Organization", "github_owner", self.settings.get("github_owner", ""), None, None, 10),
                                     ("Repository", "github_repo", self.settings.get("github_repo", ""), None, None, 10)])
        self.github_status = tk.StringVar(value="Checking GitHub sign-in…" if "/" in self.github_repository()
                                          else "Enter the organization and repository, then select Save.")
        kit.wrap_to_parent(ttk.Label(github.body, textvariable=self.github_status, style="Hint.TLabel",
                                     justify="left", width=1)).pack(fill="x", pady=(0, px(14)))
        row = ttk.Frame(github.body, style="Card.TFrame"); row.pack(fill="x")
        self.button(row, "Save", self.save_settings).pack(side="left")
        self.button(row, "Sign in to GitHub", self.github_login).pack(side="left", padx=(px(8), 0))

        log = kit.Card(left, "Session activity", "Sign-in progress and GitHub one-time codes.", icon="activity",
                       tone="neutral")
        log.frame.pack(fill="x")
        clear = ttk.Button(log.actions, text="Clear", style="Small.Ghost.TButton",
                           command=lambda: kit.set_text(self.logbox, ""))
        clear.pack(); kit.tooltip(clear, "Clear the activity log")
        frame, self.logbox = kit.text_area(log.body, 7, readonly=True)
        frame.pack(fill="both", expand=True)
        self.render_tenant()
        self.render_details()
        self.sync_connection()

    def picker(self, parent, label, name, refresh, tip):
        """A labelled read-only dropdown with a small refresh action beside its label."""
        box = ttk.Frame(parent, style="Card.TFrame"); box.pack(fill="x", pady=(0, px(14)))
        head = kit.field_label(box, label)
        button = ttk.Button(head, text="↻  Refresh", style="Small.Ghost.TButton", command=refresh)
        button.pack(side="right"); kit.tooltip(button, tip)
        combo = ttk.Combobox(box, textvariable=self.var(name), values=[], state="readonly")
        combo.pack(fill="x")
        self.controls.append((combo, "readonly"))
        return combo, button

    def show_menu(self, menu, widget):
        if self.busy: return
        has_client = bool(self.vars["client_name"].get())
        for index in (0, 2):
            menu.entryconfigure(index, state="normal" if has_client else "disabled")
        try: menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally: menu.grab_release()

    def toggle_tenant(self, show):
        if show and not self.tenant_box.winfo_manager():
            self.tenant_box.pack(fill="x", after=self.tenant_row); self.tenant_row.pack_forget()
            self.tenant_entry.focus_set()
        elif not show and self.tenant_box.winfo_manager():
            self.tenant_box.pack_forget(); self.tenant_row.pack(fill="x", pady=(0, px(14)), before=self.tenant_box)

    def render_tenant(self):
        value = self.vars["tenant"].get().strip()
        self.tenant_summary.set("Tenant  " + value if value else
                                "No tenant set — every directory your account can access will be discovered.")

    def refresh_first_run(self):
        """Point a new analyst at the two things to set up; hide once either exists."""
        if not hasattr(self, "first_run_banner"): return
        if self.clients or "/" in self.github_repository():
            self.first_run_banner.grid_forget(); return
        self.first_run_text.set("Welcome. Add a client and sign in with Microsoft to open its workspace. To submit "
                                "rules for approval, also set your team's rule library.")
        self.first_run_banner.set_action("Set up rule library", self.show_github_settings)
        self.first_run_banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, kit.space("lg")))

    def show_github_settings(self):
        """Open Connect at the rule library card, ready to type."""
        self.show_page("connect")
        self.root.after_idle(lambda: (self.scroll_into_view(self.github_card.frame),
                                      next(w for w, _ in self.controls if str(w.cget("textvariable")) == str(self.vars["github_owner"])).focus_set()))

    # ----------------------------------------------------------------- rules
    def build_rules(self, page):
        bar = ttk.Frame(page); bar.pack(fill="x", pady=(0, kit.space("lg")))
        self.rule_view = tk.StringVar(value="workspace")
        kit.Segmented(bar, self.rule_view, [("workspace", "Workspace rules"), ("repository", "Rule library")]).pack(side="left")
        self.rule_views = dict(workspace=ttk.Frame(page), repository=ttk.Frame(page))
        self.rules_page = RulesPage(self, self.rule_views["workspace"])
        self.catalog_page = CatalogPage(self, self.rule_views["repository"])
        if self.ui_state.get("rule_view") in self.rule_views: self.rule_view.set(self.ui_state["rule_view"])
        self.rule_view.trace_add("write", lambda *args: self.rule_view_changed())
        self.rule_view_changed()

    def open_rule_library(self):
        """Jump to the GitHub rule library, ready to pick a rule to edit."""
        if self.busy: return
        self.show_page("rules"); self.rule_view.set("repository")
        self.catalog_page.open_github_rules()

    def rule_view_changed(self):
        view = self.rule_view.get()
        for name, frame in self.rule_views.items():
            if name == view: frame.pack(fill="both", expand=True)
            else: frame.pack_forget()
        self.page_canvases[PAGES.index("rules")].yview_moveto(0)
        self.update_workspace_context()
        if view == "repository": self.catalog_page.auto_load()

    # ------------------------------------------------------------ navigation
    def scroll_into_view(self, widget):
        """Scroll the current page so ``widget`` sits near the top of the view."""
        index = self.tabs.index("current")
        canvas, content = self.page_canvases[index], self.page_contents[index]
        self.root.update_idletasks()
        height = content.winfo_height()
        if height > canvas.winfo_height():
            canvas.yview_moveto(max(0, (widget.winfo_rooty() - content.winfo_rooty() - px(SCROLL_TARGET_OFFSET)) / height))

    def scroll_page(self, event):
        widget = event.widget
        if widget.winfo_toplevel() != self.root or widget.winfo_class() == "TCombobox":
            return
        if widget.winfo_class() in ("Text", "Treeview"):
            # Let a scrollable inner widget scroll itself; pass the wheel through once it hits an edge.
            first, last = widget.yview()
            if not ((first <= 0 and event.delta > 0) or (last >= 1 and event.delta < 0)):
                return
        canvas = self.page_canvases[self.tabs.index("current")]
        if canvas.yview() != (0.0, 1.0): canvas.yview_scroll(int(-event.delta / 120), "units")

    def page_changed(self, event=None):
        name = self.current_page()
        self.page_title.set(NAVIGATION[name][3]); self.page_subtitle.set(NAVIGATION[name][4])
        self.update_workspace_context()
        for index in range(len(self.nav)):
            self.paint_nav(index)
        if hasattr(self, "rule_builder"):
            self.rule_builder.set_footer_visible(name == "builder")
            if name == "builder": self.rule_builder.check_github()
        if getattr(self, "last_page", None) not in (None, name):
            self.focus_page_default(name)
        self.last_page = name

    def focus_page_default(self, name):
        try:
            if name == "connect": self.clientbox.focus_set()
            elif name == "rules": self.focus_search()
            elif name == "builder": self.rule_builder.widgets["name"].focus_set()
        except (tk.TclError, KeyError, AttributeError):
            pass

    def update_workspace_context(self):
        if not hasattr(self, "tabs") or not hasattr(self, "rule_view"):
            return
        page = self.current_page()
        tone = "neutral"
        if page == "builder":
            text, tone = "Local drafts  ·  Azure sign-in is not required", "info"
        elif page == "rules" and self.rule_view.get() == "repository":
            text, tone = "Rule library  ·  Azure sign-in is not required", "info"
        elif self.workspace:
            tenant = self.workspace["tenant_id"]
            text, tone = self.workspace["workspace_name"] + "  ·  Tenant " + tenant.split("-")[0] + "…", "success"
        else:
            text = "No workspace selected  ·  Sign in to begin"
        self.workspace_context.set(text)
        self.context_dot.configure(fg=TONES[tone][0] if tone != "neutral" else COLORS["subtle"])

    def render_session(self):
        c = COLORS
        session = self.session
        signed_in = session is not None and getattr(session, "signed_in", False) is True
        account = getattr(session, "account", None)
        user = account.get("user", {}).get("name", "") if isinstance(account, dict) else ""
        if self.signin_active:
            title, detail, color, pill = "Signing in…", "Finish signing in in your browser.", c["warning"], ("Signing in", "warning")
        elif signed_in and self.workspace:
            title, detail, color, pill = "Signed in", "\n".join(filter(None, (user, self.workspace["workspace_name"]))), \
                c["sidebar_success"], ("Connected", "success")
        elif signed_in:
            title, detail, color, pill = "Signed in", user or "Choose a subscription and workspace.", \
                c["sidebar_accent"], ("Signed in", "info")
        else:
            title, detail, color, pill = "Not signed in", "Sign in to load workspaces.", c["subtle"], ("Not connected", "neutral")
        self.session_title.configure(text=title); self.session_detail.configure(text=detail)
        self.session_dot.configure(fg=color)
        self.connection_pill.set(*pill)
        if signed_in and not self.signin_active: self.signout_link.pack(fill="x", padx=px(12), pady=(0, px(10)))
        else: self.signout_link.pack_forget()

    # --------------------------------------------------------------- clients
    def delete_client(self):
        if self.busy:
            return
        name = self.vars["client_name"].get()
        match = next((c for c in self.clients if c["name"] == name), None)
        if match is None:
            messagebox.showinfo("Remove client", "Choose a client first.", parent=self.root)
            return
        if not messagebox.askyesno("Remove client",
                f"Remove {name} from the client list?\n\nThis only removes it from this PC. Nothing in Azure or GitHub changes.",
                parent=self.root):
            return
        remaining = [c for c in self.clients if c["name"] != name]
        try:
            write_json(self.clientfile, remaining)
        except OSError as error:
            messagebox.showerror("Remove client", "The client list couldn't be saved.\n\n" + str(error), parent=self.root)
            return
        self.clients = remaining
        self.auth_hint = None
        self.clear_selection()
        for key in ("client_name", "tenant"):
            self.vars[key].set("")
        self.refresh_clients()
        self.refresh_first_run()
        self.flash(f"Removed {name} from the client list.")

    def refresh_clients(self):
        selected = self.vars["client_name"].get()
        self.clients.sort(key=lambda c: (c["name"].casefold(), c["name"]))
        self.clientbox.configure(values=[c["name"] for c in self.clients])
        if self.clients:
            names = [c["name"] for c in self.clients]
            self.vars["client_name"].set(selected if selected in names else names[0])
            self.client_changed()
        self.last_client = self.vars["client_name"].get()

    def client_changed(self, event=None):
        name = self.vars["client_name"].get()
        previous = getattr(self, "last_client", "")
        if event is not None and name != previous and (self.workspace or self.subscriptions):
            current = self.workspace["workspace_name"] if self.workspace else "this subscription"
            if not messagebox.askyesno("Switch client?",
                    f"You are connected to {current}. Switching to {name} clears that selection, and you will need to sign in again.",
                    parent=self.root):
                self.vars["client_name"].set(previous); return
        self.last_client = name
        self.clear_selection()
        for c in self.clients:
            if c["name"] == name:
                self.vars["tenant"].set(c["tenant"])
                break
        self.toggle_tenant(not self.vars["tenant"].get().strip())

    def clear_selection(self):
        # The old session is not reused for another client's login.
        self.rules_page.reset()
        self.subscriptions = []; self.workspaces = []; self.workspace = None
        self.subbox.configure(values=[]); self.wsbox.configure(values=[])
        self.vars["subscription"].set(""); self.vars["workspace"].set("")
        self.identity.set("No workspace selected yet. Sign in to discover subscriptions and workspaces.")

    def add_client(self):
        if self.busy:
            return
        def validate(values):
            require(values["name"], "Enter a client name.")
            values["tenant"] = validate_tenant_hint(values["tenant"]) if values["tenant"] else ""
            return values
        values = kit.FormDialog(self.root, "New client",
            "Clients are saved on this PC only. They never contain credentials.",
            [("name", "Client name", "", "Shown in the client list."),
             ("tenant", "Tenant ID or verified domain", "", "Optional. Leave blank to discover after sign-in.")],
            submit="Add client", validate=validate).show()
        if not values:
            return
        self.vars["client_name"].set(values["name"]); self.vars["tenant"].set(values["tenant"])
        self.last_client = values["name"]
        self.clear_selection(); self.save_client()

    def save_client(self):
        v = self.values()
        try:
            v["tenant"] = validate_tenant_hint(v["tenant"]) if v["tenant"] else ""
            require(v["client_name"], "Enter a client name.")
        except Stop as error:
            messagebox.showerror("Save client", str(error), parent=self.root)
            return
        self.vars["tenant"].set(v["tenant"])
        entry = dict(name=v["client_name"], tenant=v["tenant"])
        clients = [c for c in self.clients if c["name"] != entry["name"]] + [entry]
        clients.sort(key=lambda c: (c["name"].casefold(), c["name"]))
        try:
            write_json(self.clientfile, clients)
        except OSError as error:
            messagebox.showerror("Save client", "The client list couldn't be saved.\n\n" + str(error), parent=self.root)
            return
        self.clients = clients
        self.clientbox.configure(values=[c["name"] for c in self.clients])
        self.toggle_tenant(not v["tenant"])
        self.refresh_first_run()
        self.flash("Client saved.")

    def values(self):
        return {k: v.get().strip() if isinstance(v.get(), str) else v.get() for k, v in self.vars.items()}

    # ---------------------------------------------------------------- worker
    def log(self, message):
        self.events.put(("log", str(message)))

    def call_soon(self, callback):
        """Run ``callback`` on the UI thread; safe to call from worker threads."""
        self.events.put(("ui", callback))

    def file_progress(self, show):
        """A thread-safe ``progress(done, total)`` that passes a readable message to ``show``."""
        def progress(done, total):
            if done == total or done % PROGRESS_REPORT_EVERY == 0:
                # Ignore late updates once the operation has finished.
                self.call_soon(lambda: self.busy and show(f"Reading the rule library: {done} of {total} files…"))
        return progress

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                try:
                    self.dispatch(kind, data)
                except Exception as error:  # One bad callback must not stop every later result.
                    self.log(repr(error))
                    self.status.set("Something went wrong. Details are in Session activity on the Connect page.")
        except queue.Empty:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(EVENT_POLL_MS, self.poll)

    def dispatch(self, kind, data):
        if kind == "log":
            if not hasattr(self, "logbox"): return
            self.logbox.configure(state="normal")
            self.logbox.insert("end", data + "\n"); self.logbox.see("end")
            self.logbox.configure(state="disabled")
        elif kind == "signin_finished":
            self.finish_signin()
        elif kind == "ui":
            data()
        elif kind == "done":
            callback, result, error = data
            # The callback retains its event after the worker has finished.
            # Cancellation also wins over an error already queued for the UI.
            cancel = getattr(callback, "_signin_cancel", None)
            if isinstance(cancel, threading.Event) and cancel.is_set():
                error = SignInCancelled()
            title = self.operation_title
            self.set_busy(False)
            if error:
                details = str(error)
                self.log(details); self.show_page(self.operation_page)
                if isinstance(error, SignInCancelled):
                    self.discard_cancelled_signin()
                elif isinstance(error, AuthenticationRequired):
                    self.status.set("Couldn't finish: " + details[:160])
                    self.recover_authentication(error)
                else:
                    self.status.set("Couldn't finish: " + details[:160])
                    self.activity_badge.set("Stopped", "danger")
                    messagebox.showerror(title.rstrip("….") or "Operation stopped",
                                         "This didn't finish.\n\n" + details +
                                         "\n\nThe full message is in Session activity on the Connect page.", parent=self.root)
            elif callback:
                callback(result)

    def set_busy(self, busy):
        self.busy = busy
        if busy:
            if self.flash_timer is not None:
                self.root.after_cancel(self.flash_timer); self.flash_timer = None
            self.activity_badge.set("Working", "info")
            self.progress.pack(fill="both", expand=True); self.progress.start(12)
        else:
            self.activity_badge.set("Ready", "success"); self.status.set("Ready.")
            self.progress.stop(); self.progress.pack_forget()
        for widget, state in self.controls:
            widget.configure(state="disabled" if busy else state)
        kit.refresh_all()
        self.signout_link.configure(fg=COLORS["sidebar_muted"] if busy else COLORS["sidebar_accent"],
                                    cursor="arrow" if busy else "hand2")
        self.sync_connection()
        if hasattr(self, "catalog_page"): self.catalog_page.sync_actions()
        if hasattr(self, "rules_page"): self.rules_page.sync()

    def sync_connection(self):
        """Enable selectors and workspace actions only when there is something to act on."""
        if not hasattr(self, "workspace_actions"): return
        for button in self.workspace_actions:
            button.configure(state="normal" if self.workspace and not self.busy else "disabled")
        self.subbox.configure(state="readonly" if self.subscriptions and not self.busy else "disabled")
        self.wsbox.configure(state="readonly" if self.workspaces and not self.busy else "disabled")
        signed_in = self.session is not None and (bool(self.subscriptions) or getattr(self.session, "signed_in", False) is True)
        self.sub_refresh.configure(state="normal" if signed_in and not self.busy else "disabled")
        self.ws_refresh.configure(state="normal" if self.subscriptions and not self.busy else "disabled")
        if signed_in and not self.missing_link.winfo_manager():
            self.missing_link.pack(anchor="w", pady=(0, px(10)), after=self.subbox.master)
        elif not signed_in and self.missing_link.winfo_manager():
            self.missing_link.pack_forget()
        if self.workspace:
            account = (getattr(self.session, "account", None) or {}).get("user", {}).get("name", "")
            self.connection_note.set(("Signed in as " + account + ". " if account else "") +
                                     "Selected " + self.workspace["workspace_name"] + ".")
        elif self.subscriptions: self.connection_note.set("Select a subscription and workspace to continue.")
        else: self.connection_note.set("Sign in to load your subscriptions and workspaces.")

    def work(self, title, task, done=None, page="connect"):
        if self.busy:
            self.flash("Wait for the current task to finish.", "warning", 3); return
        self.operation_page, self.operation_title = page, title
        self.set_busy(True); self.status.set(title); self.log(title)
        def run():
            try: result, error = task(), None
            except Exception as ex: result, error = None, ex
            self.events.put(("done", (done, result, error)))
        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------------------- portals
    def open_cyberqp(self):
        url = CYBERQP_PORTALS["US"]
        try:
            if not webbrowser.open(url, new=2):
                raise RuntimeError("Couldn't open the browser. Open " + url + " yourself.")
            self.flash("CyberQP opened in your browser. Activate access there, then sign in with Microsoft here.", "info")
        except Exception as error:
            messagebox.showerror("Open CyberQP", str(error), parent=self.root)

    # ---------------------------------------------------------------- sign-in
    def cancel_signin(self):
        if not self.signin_active or self.signin_cancel is None: return
        self.signin_cancel.set()
        self.finish_signin()
        self.status.set("Cancelling Microsoft sign-in…")

    def discard_cancelled_signin(self):
        self.finish_signin()
        if self.session:
            # Keep the isolated session only so retry/sign-out/close can clear
            # any CLI cache written just before cancellation won the race.
            self.session.account = None; self.session.signed_in = False
        self.clear_selection()
        self.flash("Sign-in cancelled. You can sign in again.", "info")
        self.show_page("connect")

    def bring_signin_forward(self):
        if not self.signin_active: return
        try:
            found = self.signin_handoff and self.signin_handoff.bring_forward(manual=True)
        except OSError: found = False
        if not found:
            self.status.set("The sign-in window is still opening, or Windows blocked the switch. Use Alt+Tab to find it.")

    def watch_signin(self):
        self.signin_timer = None
        if not self.signin_active: return
        if self.signin_handoff and time.monotonic() < self.signin_deadline:
            try: self.signin_handoff.bring_forward()
            except OSError: pass
            self.signin_timer = self.root.after(SIGNIN_FOCUS_POLL_MS, self.watch_signin)

    def finish_signin(self):
        self.signin_active = False
        if self.signin_timer is not None:
            self.root.after_cancel(self.signin_timer); self.signin_timer = None
        self.signin_handoff = None
        self.signin_button.configure(state="disabled")
        self.cancel_signin_button.configure(state="disabled")
        self.signin_cancel = None
        self.signin_panel.pack_forget()
        self.render_session()

    def ask_tenant(self):
        values = kit.FormDialog(self.root, "Client tenant", "Microsoft needs to know which directory to sign in to.",
            [("tenant", "Tenant ID or verified domain", "", "From the Azure portal: Microsoft Entra ID > Overview.")],
            submit="Continue", validate=lambda v: {"tenant": validate_tenant_hint(v["tenant"])}).show()
        return values["tenant"] if values else None

    def recover_authentication(self, error):
        self.finish_signin()
        tenant = error.tenant or self.vars["tenant"].get().strip()
        # A broker failure may omit the resource challenge; the browser flow can expose it.
        method = "Browser" if error.broker_failure else self.vars["login_method"].get()
        attempt = (tenant, error.resource, method, bool(error.claims))
        if attempt in self.auth_recovery_attempts:
            messagebox.showerror("Sign-in still required",
                "Microsoft still requires authentication after the fresh sign-in. Nothing was retried. "
                "Confirm Azure CLI is current. Check this client's Entra sign-in logs using the correlation ID below, "
                "including Authentication Details and Conditional Access results, "
                "and confirm the account and tenant. You can start another sign-in after resolving them.\n\n" + str(error),
                parent=self.root)
            return
        if not messagebox.askyesno("Sign in again?",
                ("The Windows sign-in component couldn't complete verification. Try a fresh browser sign-in?\n\n"
                 if error.broker_failure else "Microsoft requires additional verification. Open Microsoft sign-in now?\n\n") +
                ("Azure supplied an additional authentication challenge; it will be included in this sign-in.\n\n"
                 if error.claims else "") +
                "After signing in, check the workspace and start the task again. "
                "The interrupted task is not replayed; earlier steps may already have completed.\n\n" +
                str(error), parent=self.root):
            return
        if not tenant:
            tenant = self.ask_tenant()
            if not tenant: return
        try: tenant = validate_tenant_hint(tenant)
        except Stop as problem:
            messagebox.showerror("Client tenant", str(problem), parent=self.root); return
        self.auth_recovery_attempts.add((tenant, error.resource, method, bool(error.claims)))
        self.vars["login_method"].set(method)
        self.vars["tenant"].set(tenant)
        self.show_page("connect")
        self.login(recovery=error)

    def login(self, recovery=None):
        if self.busy: return
        if recovery is None: self.auth_recovery_attempts.clear()
        try:
            value = self.vars["tenant"].get().strip()
            hint = validate_tenant_hint(value) if value else ""
        except Stop as error:
            self.toggle_tenant(True)
            messagebox.showerror("Tenant", str(error), parent=self.root)
            return
        self.vars["tenant"].set(hint)
        self.clear_selection()
        self.auth_hint = hint
        old = self.session
        self.session = Session(logger=self.log)
        self.session.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"] = (
            "true" if self.vars["login_method"].get() == "Windows account window" else "false")
        self.finish_signin()
        cancel = threading.Event()
        self.signin_cancel = cancel
        session = self.session
        self.signin_handoff = create_handoff()
        self.signin_active = True
        self.cancel_signin_button.configure(state="normal")
        self.signin_deadline = time.monotonic() + SIGNIN_FOCUS_SECONDS
        self.signin_button.configure(state="normal" if self.signin_handoff else "disabled")
        self.signin_panel.pack(fill="x", pady=(px(12), 0), after=self.signin_main)
        self.render_session()
        self.watch_signin()
        if self.current_page() == "connect": self.scroll_into_view(self.signin_main)
        def task():
            try:
                check_cancelled(cancel)
                if old:
                    old._signin_cancel = cancel
                    try:
                        try: old.logout()
                        except Stop as error:
                            if not re.search(r"no (?:active )?accounts|account.*(?:not found|does not exist)", str(error), re.I): raise
                            self.log("The previous app session has no usable cached account. Continuing with a fresh sign-in.")
                    finally: old._signin_cancel = None
                check_cancelled(cancel)
                session._signin_cancel = cancel
                if recovery is not None:
                    rows = session.login(hint, resource=recovery.resource, claims=recovery.claims)
                else:
                    rows = session.login(hint)
                check_cancelled(cancel)
                return rows
            except Exception:
                check_cancelled(cancel)
                raise
            finally:
                session._signin_cancel = None
                self.events.put(("signin_finished", None))
        def done(rows):
            if cancel.is_set():
                self.discard_cancelled_signin(); return
            self.display_subscriptions(rows)
        done._signin_cancel = cancel
        self.work("Waiting for Microsoft sign-in…", task, done)

    # ------------------------------------------------------------- discovery
    @staticmethod
    def subscription_label(row):
        state = row.get("state", "Unknown")
        return "  ·  ".join(filter(None, (row.get("name") or "Unnamed subscription", row["id"],
                                          None if state == "Enabled" else state, "Tenant " + row["tenantId"])))

    def display_subscriptions(self, rows, discover=True, select=None):
        rows = sorted(rows, key=lambda r: (r.get("name", "").casefold(), r["id"]))
        self.subscriptions = rows
        self.sync_connection()
        self.subbox.configure(values=[self.subscription_label(r) for r in rows])
        self.render_session()
        if rows:
            chosen = next((i for i, r in enumerate(rows) if r["id"] == select), None) if select else None
            if chosen is None: chosen = next((i for i, r in enumerate(rows) if r.get("isDefault")), 0)
            self.subbox.current(chosen)
            if discover: self.subscription_changed()
        else:
            self.vars["subscription"].set("")
            self.status.set("Signed in, but no subscriptions are visible yet. Check your access, then select Refresh.")

    def refresh_subscriptions(self):
        if not self.session or not (self.subscriptions or getattr(self.session, "signed_in", False)):
            messagebox.showinfo("Refresh subscriptions", "Sign in with Microsoft first.", parent=self.root); return
        index = self.subbox.current()
        current = self.subscriptions[index]["id"] if 0 <= index < len(self.subscriptions) else ""
        def done(rows):
            self.clear_selection()
            self.display_subscriptions(rows, select=current)
            if not rows: self.status.set("No subscriptions are visible for this account.")
        self.work("Refreshing subscriptions from Azure…", self.session.refresh_subscriptions, done)

    def check_subscription(self):
        if not self.session or not (self.subscriptions or getattr(self.session, "signed_in", False)):
            messagebox.showinfo("Check a subscription", "Sign in with Microsoft first.", parent=self.root); return
        values = kit.FormDialog(self.root, "Check a subscription",
            "A read-only check of a subscription you expect to see but don't.",
            [("id", "Subscription ID", "", "From the Azure portal, for example 22222222-2222-4222-8222-222222222222.")],
            submit="Check", validate=lambda v: {"id": guid(v["id"].strip(), "subscription ID")}).show()
        if not values: return
        value = values["id"]
        def done(report):
            window = tk.Toplevel(self.root); window.title("Subscription check"); window.configure(bg=COLORS["card"])
            body = ttk.Frame(window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
            body.pack(fill="both", expand=True)
            ttk.Label(body, text="Subscription check", style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(body, text="Read-only check of subscription " + value + ".", style="CardSubtitle.TLabel").pack(
                anchor="w", pady=(px(2), px(14)))
            frame, box = kit.text_area(body, 18, readonly=True)
            frame.pack(fill="both", expand=True, pady=(0, px(14)))
            kit.set_text(box, report)
            def copy():
                self.root.clipboard_clear(); self.root.clipboard_append(report); self.flash("Copied.")
            actions, buttons = kit.action_row(body, secondary=(("Copy results", copy),), close=("Close", window.destroy))
            actions.pack(fill="x")
            kit.place_dialog(window, self.root, 860, 600, 600, 400)
            kit.modal(window, self.root, window.destroy, focus=buttons[-1])
            self.flash("Subscription check complete.", "info")
        self.work("Checking subscription access and workspaces…", lambda: self.session.check_subscription(value), done)

    @staticmethod
    def workspace_rows(sub, workspace=None, note=None):
        """The identity text shown in the Workspace card, one 'Label: value' line each."""
        lines = ["Tenant ID: " + sub["tenantId"], "Subscription ID: " + sub["id"]]
        if workspace is not None:
            lines += [label + ": " + workspace[key] for key, label in
                      (("resource_group", "Resource group"), ("workspace_name", "Workspace name"), ("workspace_id", "Workspace ID"))]
        elif note:
            lines += ["Workspace name: " + note]
        return "\n".join(lines)

    def reset_workspaces(self):
        self.workspace = None; self.workspaces = []
        self.rules_page.reset()
        self.wsbox.configure(values=[]); self.vars["workspace"].set("")

    def subscription_changed(self, event=None):
        self.reset_workspaces()
        index = self.subbox.current()
        if index < 0: return
        sub = self.subscriptions[index]
        self.auth_hint = sub["tenantId"]
        self.vars["tenant"].set(self.auth_hint)
        self.identity.set(self.workspace_rows(sub, note="Loading workspaces…"))
        self.discover()

    def discover(self):
        index = self.subbox.current()
        if not self.session or index < 0:
            messagebox.showinfo("Refresh workspaces", "Sign in and choose a subscription first.", parent=self.root); return
        sub = self.subscriptions[index]
        self.reset_workspaces()
        def done(rows):
            self.workspaces = rows
            self.sync_connection()
            self.wsbox.configure(values=[r["workspace_name"] + "  ·  " + r["resource_group"] for r in rows])
            if rows:
                self.wsbox.current(0); self.workspace_changed()
                count = len(rows)
                self.flash(f"Found {count} workspace{'s' if count != 1 else ''} in this subscription. "
                           f"Selected {rows[0]['workspace_name']}.")
            else:
                self.identity.set(self.workspace_rows(sub, note="No accessible workspaces"))
                self.status.set("No workspaces in this subscription. Choose another subscription, or check your access.")
        self.work("Loading workspaces…", lambda: self.session.discover(sub["id"], sub["tenantId"]), done)

    def workspace_changed(self, event=None):
        index = self.wsbox.current()
        if index < 0: return
        self.workspace = dict(self.workspaces[index])
        self.rules_page.reset()
        selected = dict(self.workspace)
        self.root.after_idle(lambda: self.rules_page.load() if self.workspace == selected and not self.busy else None)
        account = self.session.account or {}
        sub = dict(tenantId=self.workspace["tenant_id"], id=self.workspace["subscription_id"])
        self.identity.set("Signed in: " + account.get("user", {}).get("name", "unknown") + "\n" +
                          self.workspace_rows(sub, self.workspace))

    def render_details(self):
        lines = self.identity.get().splitlines()
        rows = [line.split(": ", 1) for line in lines if ": " in line]
        if lines and len(rows) == len(lines):
            self.details_empty.pack_forget()
            # The account already shows in the note, pill and sidebar; Copy details keeps it.
            self.details.set([row for row in rows if row[0] != "Signed in"]); self.details.pack(fill="x")
        else:
            self.details.pack_forget()
            self.details_empty.configure(text=self.identity.get()); self.details_empty.pack(fill="x")
        self.render_session()

    def copy_workspace(self):
        if not self.workspace:
            messagebox.showinfo("Copy details", "Choose a workspace first.", parent=self.root)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.identity.get())
        self.copy_button.configure(text="Copied")
        self.root.after(2000, lambda: self.copy_button.winfo_exists() and self.copy_button.configure(text="Copy details"))
        self.flash("Workspace details copied.")

    # ----------------------------------------------------------------- github
    def github_login(self):
        def task():
            session = self.session or Session(logger=self.log)
            require(session.gh_exe, "GitHub CLI isn't installed on this PC. Ask your administrator to install it.")
            # Explicit user action. Device authorization text is shown live; no tokens are printed.
            p = subprocess.Popen([session.gh_exe, "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", env=session.env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            p.stdin.write("\n"); p.stdin.flush(); p.stdin.close()
            timer = threading.Timer(GITHUB_LOGIN_TIMEOUT_SECONDS, p.kill); timer.start()
            try:
                for line in p.stdout: self.log(line.rstrip())
                require(p.wait() == 0, "GitHub sign-in didn't finish. Check the one-time code shown in Session activity.")
            finally: timer.cancel()
            return "Signed in to GitHub."
        self.show_page("connect"); self.root.update_idletasks()
        self.page_canvases[PAGES.index("connect")].yview_moveto(1)
        def done(result):
            self.flash(result)
            self.rule_builder.check_github(force=True)
        self.work("Finish signing in to GitHub in the browser; the one-time code appears in Session activity…", task, done)

    def logout(self):
        if not self.session or self.busy: return
        session = self.session
        def done(_):
            self.session = None; self.clear_selection(); self.flash("Signed out.")
        self.work("Signing out…", session.logout, done)

    def close(self):
        if self.busy:
            if not messagebox.askyesno("Close Sentinel Workspace?",
                    "A task is still running. If you close now it will be abandoned; nothing already sent to Azure or GitHub is undone.",
                    parent=self.root):
                return
            self.save_state(); self.root.destroy(); return
        if not self.rule_builder.can_discard():
            return
        self.save_state()
        if self.session:
            session = self.session
            self.work("Signing out before closing…", session.logout, lambda _: self.root.destroy())
            self.closing = True
        else: self.root.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
