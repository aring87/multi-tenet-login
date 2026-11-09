"""Sentinel Workspace: local Windows desktop interface."""
import json
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from . import __version__
from .desktop_theme import configure_theme, COLORS, FONTS, TONES, px
from . import advice
from . import brand
from . import ui_kit as kit
from .ui_kit import work_area, window_frame, on_a_monitor  # Window placement, shared with dialogs.
from .auth_recovery import AuthenticationRequired, SignInCancelled
from .signin_process import check_cancelled
from .rules_page import CopyValues, RulesPage
from .catalog_page import CatalogPage
from .rule_builder_page import RuleBuilderPage
from .signin_window import create_handoff
from .desktop_backend import (BASE, DATA, Session, Stop, require, guid, validate_tenant_hint, CYBERQP_PORTALS)
from .gov_onboarding import GOV_CLOUD
from .gov_onboarding_dialog import GovOnboardingDialog

# Only the GitHub repository is edited here. Any other keys found in an existing
# settings.json (the command-line onboarding tools keep theirs there) are preserved verbatim.
SETTINGS_KEYS = ("github_owner", "github_repo")

# Page keys in notebook order, with navigation and header copy.
PAGES = ("connect", "rules", "builder")
NAVIGATION = dict(
    connect=("Connect", "Choose a client and sign in", "connect",
             "Connect", "Sign in with your Microsoft account, then choose the client's workspace."),
    rules=("Rules", "Live rules and the library", "rules",
           "Rules", "Browse the live rules in the selected workspace, or the rule library."),
    builder=("Rule builder", "Create and edit rules", "builder",
             "Rule builder", "Create or edit a rule and submit it for a teammate's approval."))

EVENT_POLL_MS = 100
SIGNIN_FOCUS_SECONDS = 300  # The whole sign-in, MFA included (Azure CLI times out at five minutes).
SIGNIN_FOCUS_POLL_MS = 750
GITHUB_LOGIN_TIMEOUT_SECONDS = 900
STATUS_PREVIEW_CHARS = 180
CONTENT_MAX_WIDTH = 1320
SCROLL_TARGET_OFFSET = 140
PROGRESS_REPORT_EVERY = 5
FLASH_SECONDS = 8
BUSY_DELAY_MS = 250  # Busy visuals wait this long, so a quick local read doesn't flash every control.
TYPEAHEAD_MS = 800  # Typing in the client box picks the first client starting with what was typed this soon.
LISTING_SUBSCRIPTIONS = "Refreshing subscriptions from Azure…"
DEFAULT_SIZE, MINIMUM_SIZE = (1360, 900), (1120, 740)  # Window client area, 96-DPI px.
ERROR_LOG_LIMIT = 1024 * 1024  # errors.log keeps its newest half once it reaches this size.
TITLE = "Sentinel Workspace"
CLIENT_CHARS = 40  # A longer client name is shortened on the sign-in button.


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


def append_error_log(path, text, limit=ERROR_LOG_LIMIT):
    """Add ``text`` to the error log; once the log reaches ``limit`` bytes, keep only its newest half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(text if text.endswith("\n") else text + "\n")
    if path.stat().st_size > limit:
        data = path.read_bytes()[-(limit // 2):]
        newline = data.find(b"\n")
        path.write_bytes(data[newline + 1:] if 0 <= newline < len(data) - 1 else data)  # Start on a whole line.


# ------------------------------------------------------------------ help viewer
INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))")


def markdown_blocks(source):
    """The guide as (kind, text) blocks: h1-h3, item, code and para. Hard-wrapped lines join up,
    so the viewer wraps them to its own width."""
    blocks, code, joinable = [], None, False
    for line in source.splitlines():
        if line.strip().startswith("```"):
            if code is None: code = []
            else: blocks.append(("code", "\n".join(code))); code = None
            joinable = False; continue
        if code is not None: code.append(line); continue
        heading = re.match(r"(#{1,3})\s+(.*)", line)
        item = re.match(r"\s*([-*]|\d+\.)\s+(.*)", line)
        if not line.strip(): joinable = False
        elif heading: blocks.append(("h" + str(len(heading[1])), heading[2].strip())); joinable = False
        elif item: blocks.append(("item", ("•" if item[1] in "-*" else item[1]) + "  " + item[2].strip())); joinable = True
        elif joinable: blocks[-1] = (blocks[-1][0], blocks[-1][1] + " " + line.strip())
        else: blocks.append(("para", line.strip())); joinable = True
    if code is not None: blocks.append(("code", "\n".join(code)))
    return blocks


def render_markdown(text, source):
    """Show the guide's Markdown in a Text widget: headings, bullets, **bold**, `code`, code blocks and
    links (web links open in the browser)."""
    c, f = COLORS, FONTS
    text.tag_configure("h1", font=f.get("page", f["title"]), spacing1=px(4), spacing3=px(10))
    text.tag_configure("h2", font=f["title"], spacing1=px(18), spacing3=px(6))
    text.tag_configure("h3", font=f["strong"], spacing1=px(12), spacing3=px(4))
    text.tag_configure("para", spacing3=px(8))
    text.tag_configure("item", lmargin1=px(8), lmargin2=px(28), spacing3=px(4))
    text.tag_configure("bold", font=f["strong"])
    text.tag_configure("mono", font=f["mono"], background=c["hover"])
    text.tag_configure("block", font=f["mono"], background=c["hover"], lmargin1=px(12), lmargin2=px(12), spacing3=px(8))
    text.tag_configure("link", foreground=c["accent"], underline=True)
    text.tag_bind("link", "<Enter>", lambda event: text.configure(cursor="hand2"))
    text.tag_bind("link", "<Leave>", lambda event: text.configure(cursor=""))
    text.configure(state="normal"); text.delete("1.0", "end")
    links = {}
    for kind, value in markdown_blocks(source):
        if kind == "code":
            text.insert("end", value + "\n", ("block",)); continue
        for part in INLINE.split(value):
            if not part: continue
            if part.startswith("**") and part.endswith("**"): text.insert("end", part[2:-2], (kind, "bold"))
            elif part.startswith("`") and part.endswith("`"): text.insert("end", part[1:-1], (kind, "mono"))
            elif (link := re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", part)) is not None:
                label, url = link.groups()
                if url.startswith("https://"):
                    tag = "link%d" % len(links); links[tag] = url
                    text.insert("end", label, (kind, "link", tag))
                    text.tag_bind(tag, "<Button-1>", lambda event, u=url: webbrowser.open(u, new=2))
                else: text.insert("end", label, (kind,))
            else: text.insert("end", part, (kind,))
        text.insert("end", "\n", (kind,))
    text.configure(state="disabled")
    return links


# ------------------------------------------------------------------ window placement
GEOMETRY = re.compile(r"(\d+)x(\d+)\+?(-?\d+)\+?(-?\d+)")


def plan_geometry(remembered, area, frame, on_monitor, zoomed=False):
    """Where the main window opens. ``area`` is the work area (left, top, width, height) of the monitor
    it opens on, ``frame`` the title bar and borders (width, height) around the client area, and
    ``on_monitor(x, y)`` whether a point is on any monitor. Sizes are clamped so the whole window,
    frame included, fits the work area at any scaling; a saved position is kept when it is on a
    monitor. Returns (width, height, x, y, min_width, min_height, zoom)."""
    left, top, area_w, area_h = area
    room_w, room_h = max(1, area_w - frame[0]), max(1, area_h - frame[1])
    min_w, min_h = min(px(MINIMUM_SIZE[0]), room_w), min(px(MINIMUM_SIZE[1]), room_h)
    width, height = px(DEFAULT_SIZE[0]), px(DEFAULT_SIZE[1])
    x = y = None
    match = GEOMETRY.fullmatch(str(remembered or ""))
    if match:
        width, height, x, y = (int(value) for value in match.groups())
    # The default size doesn't fit (150% on a 1080p laptop, say): open maximised instead.
    zoom = bool(zoomed) or (match is None and (width > room_w or height > room_h))
    width, height = max(min_w, min(width, room_w)), max(min_h, min(height, room_h))
    if x is None or not on_monitor(x + px(50), y + px(20)):
        x, y = left + max(0, (room_w - width) // 2), top + max(0, (room_h - height) // 2)
    else:  # On its monitor, but never under the taskbar or past an edge.
        x, y = max(left, min(x, left + room_w - width)), max(top, min(y, top + room_h - height))
    return width, height, x, y, min_w, min_h, zoom


class App:
    def __init__(self, root):
        self.root = root
        root.title(TITLE)
        configure_theme(root)
        # Errors in Tk callbacks are logged instead of vanishing (the packaged exe has no console).
        root.report_callback_exception = self.callback_failed
        self.state_file = DATA / "ui-state.json"
        self.error_log = DATA / "logs" / "errors.log"
        DATA.mkdir(parents=True, exist_ok=True)
        self.ui_state = self.read_state()
        self.normal_geometry, self.was_zoomed, self.closing = None, False, False
        self.apply_geometry(self.ui_state.get("geometry"), self.ui_state.get("zoomed") is True)
        root.bind("<Configure>", self.track_geometry, add="+")
        self.events = queue.Queue()
        self.busy = False
        self.busy_visual, self.busy_shown = None, False
        self.busy_ended = float("-inf")  # When the busy visuals last went away (time.monotonic()).
        self.session = None
        self.session_client = None  # The client the current Microsoft session was started for.
        self.auth_hint = None
        self.subscriptions = []
        self.subscriptions_listed = False  # The list on show came from Azure (empty means none are visible).
        self.relist = False  # Back on the session's client: list its subscriptions again once idle.
        self.subscription_index = -1  # The subscription chosen last, restored when a choice must wait.
        self.workspaces = []
        self.workspace = None
        self.clientfile = DATA / "clients.json"
        self.settingsfile = DATA / "settings.json"
        self.startup_notes = []
        self.clients = self.read_clients()
        self.settings = self.read_settings()
        self.vars = {}
        self.controls = []  # (widget, state) disabled while a task runs: buttons that start work, sign-in choices.
        self.inputs = []  # Search boxes and form fields: left usable while a task runs (set_busy leaves them).
        self.typed, self.typed_timer = "", None
        self.wheel = 0
        self.operation_page = "connect"
        self.operation_title = ""
        self.operation_failed = None  # The heading of the alert shown if the running task fails.
        self.flash_timer = None
        self.signin_handoff = None
        self.signin_timer = None
        self.signin_active = False
        self.signin_cancel = None
        self.signin_window = "your browser"  # Where Microsoft asks the analyst to finish signing in.
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
        self.root.after(600, self.rule_builder.offer_recovery)  # Unsaved builder work from a crash, once.
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # Windows signing out or restarting: Tk turns WM_QUERYENDSESSION into WM_SAVE_YOURSELF, not a close.
        self.root.protocol("WM_SAVE_YOURSELF", self.end_session)

    # ---------------------------------------------------------------- storage
    def read_state(self):
        """ui-state.json as a dict. A value of the wrong type (a hand-edited or damaged file) is dropped,
        so the window always opens with defaults for it."""
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
        except (OSError, ValueError):
            return {}
        if not isinstance(value, dict): return {}
        def usable(key, item):
            if key in ("rule_view", "library_view", "geometry", "client"): return isinstance(item, str)
            if key in ("rules_split", "library_split"):
                return isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
            if key == "last_workspace": return isinstance(item, dict)
            return True
        return {key: item for key, item in value.items() if usable(key, item)}

    def save_state(self):
        state = dict(self.ui_state)
        try:
            # A maximised window keeps the size it had before, so restoring it later returns there. A
            # minimised one (closed from the taskbar) keeps the state and place it had before minimising.
            current = self.root.state()
            if current == "normal": state["geometry"] = self.root.geometry()
            elif self.normal_geometry: state["geometry"] = self.normal_geometry
            state["zoomed"] = current == "zoomed" or (current == "iconic" and self.was_zoomed)
            state["client"] = self.vars["client_name"].get()
            state["rule_view"] = self.rule_view.get()
            state["rules_sort"] = list(self.rules_page.sort)
            state["library_view"] = self.catalog_page.view.get()
            # A split never shown this session keeps the share saved last time.
            for key, split in (("rules_split", self.rules_page.split), ("library_split", self.catalog_page.split)):
                if split.fraction(): state[key] = split.fraction()
            write_json(self.state_file, state)
        except (OSError, tk.TclError):
            pass

    def apply_geometry(self, remembered, zoomed=False):
        """Open at the saved size and place, clamped to the work area of the monitor it was on (title
        bar, status bar and the builder's footer stay above the taskbar at any scaling). main() then
        maximises the window when ``start_zoomed`` says so."""
        match = GEOMETRY.fullmatch(str(remembered or ""))
        point = (int(match.group(3)) + px(50), int(match.group(4)) + px(20)) if match else (None, None)
        width, height, x, y, min_w, min_h, self.start_zoomed = plan_geometry(
            remembered, work_area(self.root, *point), window_frame(), on_a_monitor, zoomed)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min_w, min_h)
        self.normal_geometry = f"{width}x{height}+{x}+{y}"
        self.was_zoomed = self.start_zoomed

    def track_geometry(self, event):
        """Remember the last size and place the window had while not maximised, and whether it was
        maximised before it was minimised (a minimised window reports neither)."""
        if event.widget is self.root:
            try:
                state = self.root.state()
                if state == "normal": self.normal_geometry = self.root.geometry()
                if state in ("normal", "zoomed"): self.was_zoomed = state == "zoomed"
            except tk.TclError:
                pass

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
            kit.alert(self.root, "Save rule library", "The settings couldn't be saved.", details=str(error))
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
        self.icons = brand.window_icons(self.root)
        self.root.iconphoto(True, *self.icons)
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
            if name == "rules":
                # The rule lists fill the window instead of scrolling it: each table and detail
                # pane scrolls by itself, so the page has no canvas (None in page_canvases).
                content = ttk.Frame(page, padding=(kit.space("page"), kit.space("xl"), kit.space("page"), kit.space("lg")))
                content.pack(fill="both", expand=True)
                self.page_canvases.append(None); self.page_contents.append(content)
                continue
            # One wheel notch scrolls three 20 px increments; see scroll_page.
            canvas = tk.Canvas(page, bg=COLORS["canvas"], highlightthickness=0, bd=0, yscrollincrement=px(20))
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
        # Packed first so they keep their room; Tab reaches them after the page, as they read.
        self.footer_slot.lift(); self.statusbar.lift()
        self.last_page = None
        self.tabs.bind("<<NotebookTabChanged>>", self.page_changed)
        self.page_changed()
        self.root.bind("<MouseWheel>", self.scroll_page)
        self.bind_shortcuts()
        kit.install_edit_menu(self.root)

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
        self.root.bind("<F1>", lambda event: self.open_help())
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
        header = tk.Frame(side, bg=c["sidebar"]); header.pack(fill="x", padx=px(16), pady=(px(22), px(22)))
        self.brand_mark = brand.mark(self.root, px(36))
        tk.Label(header, image=self.brand_mark, bg=c["sidebar"], bd=0).pack(side="left")
        words = tk.Frame(header, bg=c["sidebar"]); words.pack(side="left", padx=(px(12), 0))
        tk.Label(words, text="Sentinel Workspace", font=FONTS["brand"], bg=c["sidebar"], fg="#FFFFFF", bd=0).pack(anchor="w")
        tk.Label(words, text="FOR MICROSOFT SENTINEL", font=FONTS["eyebrow"], bg=c["sidebar"], fg=c["sidebar_muted"],
                 bd=0).pack(anchor="w", pady=(px(1), 0))
        tk.Frame(side, bg=c["sidebar_line"], height=1).pack(fill="x", padx=px(16), pady=(0, px(12)))

        footer = tk.Frame(side, bg=c["sidebar"]); footer.pack(side="bottom", fill="x", padx=px(20), pady=(0, px(18)))
        for text, command, tip in (("Help & prerequisites", self.open_help, "The desktop guide and keyboard shortcuts (F1)"),
                                   ("Open app data folder", self.open_data_folder, "Clients, drafts and submissions saved on this PC"),
                                   ("About Sentinel Workspace", self.open_about, "Version, components and license")):
            item = kit.link(footer, text, command, c["sidebar_muted"], c["sidebar_ink"], c["sidebar"],
                            focus_color=c["sidebar_accent"])
            item.pack(fill="x", pady=(px(6), 0))
            kit.tooltip(item, tip)
        session = tk.Frame(side, bg=c["sidebar_hover"], bd=0, highlightthickness=1,
                           highlightbackground=c["sidebar_line"], highlightcolor=c["sidebar_line"])
        session.pack(side="bottom", fill="x", padx=px(14), pady=(0, px(14)))
        top = self.session_top = tk.Frame(session, bg=c["sidebar_hover"])
        top.pack(fill="x", padx=px(12), pady=(px(10), px(10)))
        self.session_dot = tk.Label(top, text="●", font=FONTS["small"], bg=c["sidebar_hover"], fg=c["subtle"], bd=0)
        self.session_dot.pack(side="left")
        self.session_title = tk.Label(top, text="Not signed in", font=FONTS["strong_small"], bg=c["sidebar_hover"],
                                      fg=c["sidebar_ink"], bd=0)
        self.session_title.pack(side="left", padx=(px(6), 0))
        # The Microsoft account only: the header chip names the client and workspace. Packed when there is one.
        self.session_detail = tk.Label(session, text="", font=FONTS["small"], bg=c["sidebar_hover"],
                                       fg=c["sidebar_muted"], bd=0, justify="left", anchor="w", wraplength=px(196))
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
        # The account panel and links were packed first to keep the bottom of the sidebar; Tab reaches them last.
        session.lift(); footer.lift()

    def open_help(self):
        """The desktop guide, in a window of its own (F1). It works in the packaged exe, where a .md file
        has no program to open it."""
        return self.show_document("Help & prerequisites", BASE / "docs" / "DESKTOP-START-HERE.md")

    def open_data_folder(self):
        self.open_path(DATA, "Open app data folder")

    def open_path(self, path, title):
        try:
            os.startfile(str(path))
        except (OSError, AttributeError) as error:
            kit.alert(self.root, title, "Windows couldn't open it. It is here:", details=str(path) + "\n\n" + str(error),
                      tone="warning")

    def show_document(self, title, path, markdown=True, parent=None):
        """A read-only viewer for one of the app's own documents, with Close and Esc. Over another
        dialog (``parent``), that dialog gets the keyboard back when the viewer closes."""
        parent = parent or self.root
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as error:
            kit.alert(self.root, title, "The document isn't available in this copy of the app.",
                      details=str(path) + "\n\n" + str(error), tone="warning")
            return None
        window = tk.Toplevel(self.root); window.title(title); window.configure(bg=COLORS["card"])
        def close():
            window.destroy()
            if parent is not self.root and parent.winfo_exists():
                try: parent.grab_set()
                except tk.TclError: pass
        body = ttk.Frame(window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        actions, buttons = kit.action_row(body, close=("Close", close))
        actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        frame, text = kit.text_area(body, 24, readonly=True, font="body")
        frame.pack(fill="both", expand=True)
        actions.lift()  # Tab: the document, then Close.
        text.configure(padx=px(18), pady=px(14), spacing1=px(1), spacing3=px(2), bg=COLORS["card"])
        frame.configure(bg=COLORS["card"])
        try:
            render_markdown(text, source) if markdown else kit.set_text(text, source)
        except Exception as error:  # A guide edit the viewer can't show must not leave an empty window behind.
            window.destroy()
            kit.alert(self.root, title, "The document isn't available in this copy of the app.",
                      details=str(path) + "\n\n" + str(error), tone="warning")
            return None
        window.document = text
        kit.place_dialog(window, self.root, 820, 760, 560, 420)
        # Enter on Close closes; Enter in the document does nothing (text areas ignore it).
        kit.modal(window, parent, close, primary=buttons[0], focus=text)
        return window

    def open_about(self):
        """Version, what the app talks to, and where its data lives."""
        c = COLORS
        window = tk.Toplevel(self.root); window.title("About Sentinel Workspace")
        window.resizable(False, False); window.configure(bg=c["card"])
        body = ttk.Frame(window, style="Card.TFrame", padding=(kit.space("xl"), px(24), kit.space("xl"), px(20)))
        body.pack(fill="both", expand=True)
        head = ttk.Frame(body, style="Card.TFrame"); head.pack(fill="x")
        self.about_mark = brand.mark(self.root, px(56))
        tk.Label(head, image=self.about_mark, bg=c["card"], bd=0).pack(side="left", anchor="n")
        words = ttk.Frame(head, style="Card.TFrame"); words.pack(side="left", fill="x", expand=True, padx=(px(16), 0))
        ttk.Label(words, text=TITLE, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(words, text="Version " + __version__, style="CardSubtitle.TLabel").pack(anchor="w", pady=(px(2), 0))
        ttk.Label(words, text="Connect to client tenants, review Microsoft Sentinel analytics rules and submit "
                              "detection rules for approval.", style="Body.TLabel", justify="left",
                  wraplength=px(390)).pack(anchor="w", fill="x", pady=(px(10), 0))
        kit.divider(body, 16)
        facts = CopyValues(body, key_width=14); facts.pack(fill="x")
        facts.set([("Python", sys.version.split()[0]), ("Tk", str(self.root.tk.call("info", "patchlevel"))),
                   ("App data", str(DATA)), ("License", "MIT")], mono={"App data"})
        actions, buttons = kit.action_row(
            body, secondary=(("Open license", lambda: self.show_document("License", BASE / "LICENSE", False, window)),),
            close=("Close", window.destroy))
        actions.pack(fill="x", pady=(px(20), 0))
        kit.place_dialog(window, self.root, width=520)
        kit.modal(window, self.root, window.destroy, primary=buttons[-1], focus=buttons[-1])

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
        # The client and workspace, on every page; pointing at it shows the tenant and subscription IDs.
        self.workspace_context = tk.StringVar(value="No client selected")
        self.context_label = tk.Label(chip, textvariable=self.workspace_context, font=FONTS["small"], bg=c["raised"],
                                      fg=c["ink_2"], bd=0, wraplength=px(300), justify="left")
        self.context_label.pack(side="left", padx=(0, px(12)), pady=px(7))
        kit.tooltip(self.context_label, "")
        tk.Frame(main, bg=c["line"], height=1).pack(fill="x")
        # A thin activity line runs under the header while the app is busy; see set_busy.
        holder = tk.Frame(main, bg=c["canvas"], height=px(3)); holder.pack(fill="x"); holder.pack_propagate(False)
        self.progress = kit.ProgressLine(holder, background=c["canvas"])

    def build_statusbar(self, main):
        c = COLORS
        bar = self.statusbar = tk.Frame(main, bg=c["card"]); bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=c["line"], height=1).pack(fill="x")
        row = ttk.Frame(bar, style="Card.TFrame", padding=(kit.space("xl"), kit.space("sm"))); row.pack(fill="x")
        self.activity_badge = kit.Pill(row, "Ready", "success")
        self.activity_badge.pack(side="left", anchor="n")
        ttk.Label(row, text="Sentinel Workspace " + __version__, style="Hint.TLabel").pack(side="right", anchor="n", padx=(px(12), 0))
        self.status = tk.StringVar(value="Ready.")
        self.status_preview = tk.StringVar()
        status = ttk.Label(row, textvariable=self.status_preview, style="Muted.TLabel", anchor="w")
        status.pack(side="left", fill="x", expand=True, padx=(px(12), 0))
        kit.tooltip(status, "")
        body_font = tkfont.Font(self.root, font=FONTS["body"])
        def show_status(*args):
            # The footer stays one line tall: a long diagnostic is shortened with an ellipsis and
            # shown in full in the label's tooltip and in Session activity. The pill already says
            # Ready, so the bare ready message is not repeated beside it.
            text = " ".join(self.status.get().split())
            text = "" if text == "Ready." else text
            text = text if len(text) <= STATUS_PREVIEW_CHARS else text[:STATUS_PREVIEW_CHARS - 3] + "…"
            width = status.winfo_width() - px(4)
            shown = kit.ellipsize(text, body_font, width) if width > px(40) else text
            self.status_preview.set(shown)
            status.tooltip_text = text if shown != text else ""
        self.status.trace_add("write", show_status); show_status()
        status.bind("<Configure>", show_status, add="+")

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
        self.clientbox.bind("<KeyPress>", self.type_client)  # Type the first letters of a client to pick it.
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
        # Why the last sign-in failed, where the analyst is looking; hidden again when a sign-in starts.
        self.signin_error_text, self.signin_error_details = tk.StringVar(), ""
        self.signin_error = kit.Banner(body, self.signin_error_text, "danger",
                                       action=("Copy details", self.copy_signin_error))
        self.signin_panel = tk.Frame(body, bg=c["accent_soft"], bd=0, highlightthickness=1,
                                     highlightbackground=c["accent_line"], highlightcolor=c["accent_line"])
        panel = tk.Frame(self.signin_panel, bg=c["accent_soft"]); panel.pack(fill="x", padx=px(14), pady=px(12))
        tk.Label(panel, text="Waiting for Microsoft sign-in", font=FONTS["strong"], bg=c["accent_soft"],
                 fg=c["ink"], bd=0, anchor="w").pack(fill="x")
        # Names the window the chosen sign-in method opens; see login().
        self.signin_waiting = tk.StringVar(value=self.waiting_text())
        waiting = tk.Label(panel, textvariable=self.signin_waiting,
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
        # The next step sits right under the pickers; the IDs wait behind a disclosure (remembered).
        actions = self.workspace_row = ttk.Frame(body, style="Card.TFrame"); actions.pack(fill="x")
        # Not in self.controls: sync_connection enables these only once a workspace is selected.
        self.copy_button = ttk.Button(actions, text="Copy details", command=self.copy_workspace, style="Secondary.TButton")
        self.workspace_actions = [
            self.copy_button,
            ttk.Button(actions, text="View analytics rules", command=lambda: self.show_page("rules"), style="Primary.TButton")]
        self.workspace_actions[0].pack(side="left"); self.workspace_actions[1].pack(side="right")
        # Shown only for a workspace in an Azure Government (GCC High) tenant, on a row of its own so the
        # actions above keep their room at the smallest window size; see sync_connection.
        self.gov_row = ttk.Frame(body, style="Card.TFrame")
        self.gov_button = ttk.Button(self.gov_row, text="Onboard GCC High…", command=self.open_gov_onboarding,
                                     style="Secondary.TButton")
        kit.tooltip(self.gov_button, "Create this client's pipeline identities and open its target pull request")
        self.ids_button = ttk.Button(body, text="+  Show workspace IDs", style="Disclosure.TButton",
                                     command=lambda: self.toggle_ids())
        self.details_panel = details = tk.Frame(body, bg=c["raised"], bd=0, highlightthickness=1,
                                                highlightbackground=c["line"], highlightcolor=c["line"])
        inner = ttk.Frame(details, style="Raised.TFrame", padding=(px(16), px(14))); inner.pack(fill="x")
        self.details = CopyValues(inner, surface="Raised", stacked=True, copied=lambda key: self.flash(key + " copied."))
        self.details_empty = ttk.Label(inner, style="RaisedMuted.TLabel", justify="left", wraplength=px(320))
        kit.wrap_to_parent(self.details_empty)
        self.show_ids = self.ui_state.get("show_ids") is True
        self.identity = tk.StringVar(value="No workspace selected yet. Sign in to discover subscriptions and workspaces.")
        self.identity.trace_add("write", lambda *args: (self.render_details(), self.sync_connection()))

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
        frame, self.logbox = kit.text_area(log.body, 7, readonly=True,
                                           placeholder="Sign-in steps, GitHub one-time codes and any errors appear here.")
        frame.pack(fill="both", expand=True)
        self.logbox.tag_configure("stamp", foreground=c["muted"])
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

    def toggle_ids(self, show=None):
        """'+ Show workspace IDs': the tenant, subscription, resource group and workspace IDs, below the
        actions. ``show`` sets it instead of toggling. The choice is remembered (ui-state show_ids)."""
        self.show_ids = (not self.show_ids) if show is None else bool(show)
        self.ui_state["show_ids"] = self.show_ids
        self.ids_button.configure(text=("−  Hide" if self.show_ids else "+  Show") + " workspace IDs")
        self.render_details()

    def waiting_text(self):
        """The waiting panel's instruction, naming where the chosen sign-in method asks for the account."""
        return "Complete sign-in in " + self.signin_window + ". If you closed it, select Cancel sign-in."

    def copy_signin_error(self):
        self.root.clipboard_clear(); self.root.clipboard_append(self.signin_error_details)
        self.flash("Sign-in error details copied.")

    def show_signin_error(self, details, advice_text=None):
        """Explain a failed sign-in on the Sign in card itself, with its full text one Copy away."""
        self.signin_error_details = details
        first = next((line.strip() for line in details.splitlines() if line.strip()), "The sign-in didn't finish.")
        self.signin_error_text.set(advice_text or "Sign-in didn't finish: " + (first if len(first) <= 240 else first[:239] + "…"))
        if not self.signin_error.winfo_manager():
            self.signin_error.pack(fill="x", pady=(px(12), 0), after=self.signin_main)

    def hide_signin_error(self):
        self.signin_error_details = ""; self.signin_error.pack_forget()

    def type_client(self, event):
        """Type-ahead in the read-only client box: the first client whose name starts with what was typed
        in the last moment is chosen, as in a Windows list."""
        if self.busy or not event.char or not event.char.isprintable() or event.state & 0x20004:  # Ctrl or Alt held.
            return None
        if event.char == " " and not self.typed: return None
        if self.typed_timer is not None: self.root.after_cancel(self.typed_timer)
        self.typed += event.char
        self.typed_timer = self.root.after(TYPEAHEAD_MS, self.reset_typed)
        typed = self.typed.casefold()
        match = next((c["name"] for c in self.clients if c["name"].casefold().startswith(typed)), None)
        if match and match != self.vars["client_name"].get():
            self.vars["client_name"].set(match); self.client_changed(event, focus=False)
        return "break"

    def reset_typed(self):
        self.typed, self.typed_timer = "", None
        self.relist_subscriptions()

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
            # Packed before the box goes: 'before' needs a widget that is still packed.
            self.tenant_row.pack(fill="x", pady=(0, px(14)), before=self.tenant_box); self.tenant_box.pack_forget()

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
        canvas = self.page_canvases[PAGES.index("rules")]
        if canvas is not None: canvas.yview_moveto(0)
        self.update_workspace_context()
        if view == "repository": self.catalog_page.auto_load()

    # ------------------------------------------------------------ navigation
    def scroll_into_view(self, widget, only_if_hidden=False):
        """Scroll the current page so ``widget`` sits near the top of the view.

        With ``only_if_hidden`` the page stays put while the widget is already fully
        visible, so pointing at a field never scrolls the message about it away."""
        index = self.tabs.index("current")
        canvas, content = self.page_canvases[index], self.page_contents[index]
        if canvas is None: return  # A page that fills the window has nothing to scroll.
        self.root.update_idletasks()
        height = content.winfo_height()
        if height <= canvas.winfo_height(): return
        if only_if_hidden:
            top, bottom = widget.winfo_rooty(), widget.winfo_rooty() + widget.winfo_height()
            if canvas.winfo_rooty() <= top and bottom <= canvas.winfo_rooty() + canvas.winfo_height(): return
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
        if canvas is None or canvas.yview() == (0.0, 1.0): return
        # Precision touchpads send many small deltas: add them up and scroll whole 20 px increments,
        # three per 120-unit notch. A change of direction starts again from zero.
        if self.wheel and (self.wheel > 0) != (event.delta > 0): self.wheel = 0
        self.wheel += event.delta
        steps = int(self.wheel * 3 / 120)
        if steps:
            self.wheel -= steps * 40
            canvas.yview_scroll(-steps, "units")

    def page_changed(self, event=None):
        name = self.current_page()
        self.page_title.set(NAVIGATION[name][3]); self.page_subtitle.set(NAVIGATION[name][4])
        self.update_workspace_context()
        for index in range(len(self.nav)):
            self.paint_nav(index)
        if hasattr(self, "rule_builder"):
            self.rule_builder.set_footer_visible(name == "builder")
            # An emptied frame keeps its last height in Tk, which would leave a band under the
            # window-filling Rules page; give the empty slot its height back.
            if not self.footer_slot.pack_slaves(): self.footer_slot.configure(height=1)
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

    def client_name(self):
        return self.vars["client_name"].get().strip() if "client_name" in self.vars else ""

    def signed_in(self):
        """A Microsoft account is signed in (to some client)."""
        return self.session is not None and getattr(self.session, "signed_in", False) is True

    def signed_in_here(self):
        """Signed in for the client chosen now; after switching clients, the session is the old client's."""
        return self.signed_in() and (self.session_client is None or self.session_client == self.client_name())

    def update_workspace_context(self):
        """The header chip names the client on every page, and the window title does once a workspace is
        chosen (Alt+Tab, the taskbar). Pointing at the chip shows the tenant and subscription IDs."""
        if not hasattr(self, "context_label"): return
        client, workspace = self.client_name(), self.workspace
        if workspace:
            text, tone = "  ·  ".join(filter(None, (client, workspace["workspace_name"]))), "success"
        elif self.signed_in_here():
            text, tone = (client or "Signed in") + "  ·  choose a workspace", "info"
        elif client:
            text, tone = client + "  ·  not signed in", "neutral"
        else:
            text, tone = "No client selected", "neutral"
        self.workspace_context.set(text)
        self.context_dot.configure(fg=TONES[tone][0] if tone != "neutral" else COLORS["subtle"])
        tenant = workspace["tenant_id"] if workspace else self.vars["tenant"].get().strip() if "tenant" in self.vars else ""
        ids = [("Tenant ID" if workspace else "Tenant", tenant)] + \
              ([("Subscription ID", workspace["subscription_id"]), ("Workspace ID", workspace["workspace_id"])] if workspace else [])
        self.context_label.tooltip_text = "\n".join(key + ": " + value for key, value in ids if value)
        title = (client or workspace["workspace_name"]) + " — " + TITLE if workspace else TITLE
        if self.root.title() != title: self.root.title(title)

    def render_session(self):
        c = COLORS
        signed_in, here = self.signed_in(), self.signed_in_here()
        account = getattr(self.session, "account", None)
        user = account.get("user", {}).get("name", "") if isinstance(account, dict) else ""
        # The sidebar names the Microsoft account only; the header chip owns the client and workspace.
        if self.signin_active:
            title, detail, color, pill = ("Signing in…", "Complete sign-in in " + self.signin_window + ".", c["warning"],
                                          ("Signing in", "warning"))
        elif signed_in and here and self.workspace:
            title, detail, color, pill = "Signed in", user, c["sidebar_success"], ("Connected", "success")
        elif signed_in:
            title, detail, color = "Signed in", user, c["sidebar_accent"]
            pill = ("Signed in", "info") if here else ("Not connected", "neutral")
        else:
            title, detail, color, pill = "Not signed in", "", c["subtle"], ("Not connected", "neutral")
        self.session_title.configure(text=title); self.session_detail.configure(text=detail)
        if detail and not self.session_detail.winfo_manager():
            self.session_top.pack_configure(pady=(px(10), 0))
            self.session_detail.pack(fill="x", padx=px(12), pady=(px(3), px(10)), after=self.session_top)
        elif not detail and self.session_detail.winfo_manager():
            self.session_detail.pack_forget(); self.session_top.pack_configure(pady=(px(10), px(10)))
        self.session_dot.configure(fg=color)
        self.connection_pill.set(*pill)
        if signed_in and not self.signin_active: self.signout_link.pack(fill="x", padx=px(12), pady=(0, px(10)))
        else: self.signout_link.pack_forget()
        # One blue button on Connect: once connected, View analytics rules leads and sign-in steps back.
        client = self.client_name()
        short = client if len(client) <= CLIENT_CHARS else client[:CLIENT_CHARS - 1].rstrip() + "…"
        self.signin_main.configure(text="Sign in to " + short if client and not here else "Sign in with Microsoft",
                                   style="Secondary.TButton" if here and self.workspace else "Primary.TButton")
        self.update_workspace_context()

    # --------------------------------------------------------------- clients
    def delete_client(self):
        if self.busy:
            return
        name = self.vars["client_name"].get()
        match = next((c for c in self.clients if c["name"] == name), None)
        if match is None:
            kit.alert(self.root, "Remove client", "Choose a client first.", tone="info")
            return
        if not kit.confirm(self.root, "Remove client",
                           f"Remove {name} from the client list? This only removes it from this PC. Nothing in Azure "
                           "or GitHub changes.", ok="Remove", destructive=True):
            return
        remaining = [c for c in self.clients if c["name"] != name]
        try:
            write_json(self.clientfile, remaining)
        except OSError as error:
            kit.alert(self.root, "Remove client", "The client list couldn't be saved.", details=str(error))
            return
        remembered = self.ui_state.get("last_workspace")
        if isinstance(remembered, dict): remembered.pop(name, None)
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

    def client_changed(self, event=None, focus=True):
        """Another client was chosen: its selection starts empty (its last workspace is picked again
        after sign-in) and Session activity notes the switch. Nothing asks first. A choice from the
        list moves the keyboard to the sign-in button (``focus``); type-ahead keeps it in the box."""
        name = self.vars["client_name"].get()
        previous = getattr(self, "last_client", "")
        if event is not None and self.busy and name != previous:
            self.vars["client_name"].set(previous); return  # A task is still running for the previous client.
        self.last_client = name
        if event is not None and previous and name != previous:
            self.log(f"Switched from {previous} to {name}.")
        self.clear_selection()
        self.hide_signin_error()
        for c in self.clients:
            if c["name"] == name:
                self.vars["tenant"].set(c["tenant"])
                break
        self.toggle_tenant(not self.vars["tenant"].get().strip())
        self.render_session()
        if event is not None and focus and str(self.signin_main.cget("state")) != "disabled":
            self.signin_main.focus_set()
        # Back on the client the session was started for: the switch only cleared its lists, so they are
        # listed again (its last workspace returns). Type-ahead waits until the typing pauses.
        self.relist = event is not None and self.session is not None and self.signed_in_here()
        if focus: self.relist_subscriptions()

    def relist_subscriptions(self):
        if self.relist and not self.busy and not self.subscriptions and self.session is not None and self.signed_in_here():
            self.relist = False; self.refresh_subscriptions()

    def clear_selection(self):
        # The old session is not reused for another client's login.
        self.rules_page.reset()
        self.subscriptions = []; self.workspaces = []; self.workspace = None; self.subscriptions_listed = False
        self.subbox.configure(values=[]); self.wsbox.configure(values=[])
        self.vars["subscription"].set(""); self.vars["workspace"].set("")
        self.identity.set("No workspace selected yet. Sign in to discover subscriptions and workspaces.")

    def add_client(self):
        if self.busy:
            return
        def validate(values):
            if not values["name"]: raise kit.FieldError("name", "Enter a client name.")
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
            kit.alert(self.root, "Save client", str(error))
            return
        self.vars["tenant"].set(v["tenant"])
        entry = dict(name=v["client_name"], tenant=v["tenant"])
        clients = [c for c in self.clients if c["name"] != entry["name"]] + [entry]
        clients.sort(key=lambda c: (c["name"].casefold(), c["name"]))
        try:
            write_json(self.clientfile, clients)
        except OSError as error:
            kit.alert(self.root, "Save client", "The client list couldn't be saved.", details=str(error))
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
                    self.callback_failed(type(error), error, error.__traceback__)
        except queue.Empty:
            pass
        finally:
            # A callback may have destroyed the window (closing after sign-out); then there
            # is nothing left to poll and winfo_exists itself raises.
            try:
                if self.root.winfo_exists():
                    self.root.after(EVENT_POLL_MS, self.poll)
            except tk.TclError:
                pass

    def dispatch(self, kind, data):
        if kind == "log":
            if not hasattr(self, "logbox"): return
            self.logbox.configure(state="normal")
            self.logbox.insert("end", datetime.now().strftime("%H:%M:%S") + "  ", ("stamp",))
            self.logbox.insert("end", data + "\n"); self.logbox.see("end")
            self.logbox.configure(state="disabled")
            kit.refresh_placeholder(self.logbox)
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
            failed = self.operation_failed
            self.set_busy(False)
            if error:
                details = str(error)
                if self.closing:  # Signing out before closing failed, so the window stays open.
                    self.closing = False; self.rule_builder.start_autosave()
                self.log(details); self.show_page(self.operation_page)
                signin = isinstance(cancel, threading.Event)  # The sign-in's own callback carries its event.
                if isinstance(error, SignInCancelled):
                    self.discard_cancelled_signin(); return
                tip = advice.lookup(details, workspace=(self.workspace or {}).get("workspace_name"),
                                    repo=self.github_repository() or None)
                self.status.set("Couldn't finish: " + advice.first_sentence(tip or details)[:STATUS_PREVIEW_CHARS])
                if signin: self.show_signin_error(details, tip)
                if isinstance(error, AuthenticationRequired):
                    self.recover_authentication(error)
                else:
                    self.activity_badge.set("Stopped", "danger")
                    if not signin:  # A failed sign-in is explained on the Sign in card instead.
                        # The heading states the outcome; the progress title ('Reading…') would read as still running.
                        kit.alert(self.root, failed or "This didn't finish",
                                  tip or "The full message is below and in Session activity on the Connect page.",
                                  details=details)
            elif callback:
                callback(result)

    def callback_failed(self, kind, error, trace):
        """An unexpected error in a Tk callback: log it in Session activity and DATA/logs/errors.log
        (the packaged exe has no console), and say so in the status bar."""
        text = "".join(traceback.format_exception(kind, error, trace))
        self.log("Unexpected error: " + text.rstrip())
        try:
            append_error_log(self.error_log, datetime.now().isoformat(timespec="seconds") + "  Sentinel Workspace "
                             + __version__ + "\n" + text)
        except OSError:
            pass
        try:
            self.status.set("Something went wrong. Details are in Session activity.")
            self.activity_badge.set("Stopped", "danger")
        except (tk.TclError, AttributeError):
            pass

    def set_busy(self, busy, immediate=False):
        """Mark a task as running (handlers check ``busy`` at once). Its visuals (disabled controls, the
        activity line and the Working badge) wait BUSY_DELAY_MS, so a quick local read finishes without
        greying the window; ``immediate`` shows them at once. Inputs (``self.inputs``) stay usable.

        A task that starts within BUSY_DELAY_MS of one whose visuals showed (sign-in, then the workspaces,
        then the rules) shows them at once too, so a chain of tasks never flashes the window usable."""
        self.busy = busy
        if self.busy_visual is not None:
            self.root.after_cancel(self.busy_visual); self.busy_visual = None
        if busy:
            if self.flash_timer is not None:
                self.root.after_cancel(self.flash_timer); self.flash_timer = None
            immediate = immediate or (time.monotonic() - self.busy_ended) * 1000 < BUSY_DELAY_MS
            if immediate or BUSY_DELAY_MS <= 0: self.show_busy()
            elif not self.busy_shown: self.busy_visual = self.root.after(BUSY_DELAY_MS, self.show_busy)
            return
        self.activity_badge.set("Ready", "success"); self.status.set("Ready.")
        if self.busy_shown:
            self.busy_shown = False; self.busy_ended = time.monotonic()
            self.progress.stop(); self.progress.pack_forget()
            for widget, state in self.controls:
                widget.configure(state=state)
        self.sync_busy()

    def show_busy(self):
        self.busy_visual = None
        if not self.busy: return
        self.busy_shown = True
        self.activity_badge.set("Working", "info")
        if not self.progress.winfo_manager(): self.progress.pack(fill="both", expand=True)
        self.progress.start(12)
        for widget, _ in self.controls:
            widget.configure(state="disabled")
        self.sync_busy()

    def sync_busy(self):
        """Controls whose state follows ``busy`` as well as the selection."""
        kit.refresh_all()
        idle = not self.busy
        self.signout_link.configure(fg=COLORS["sidebar_accent"] if idle else COLORS["sidebar_muted"],
                                    cursor="hand2" if idle else "arrow")
        self.sync_connection()
        if hasattr(self, "catalog_page"): self.catalog_page.sync_actions()
        if hasattr(self, "rules_page"): self.rules_page.sync()

    def sync_connection(self):
        """Enable selectors and workspace actions only when there is something to act on."""
        if not hasattr(self, "workspace_actions"): return
        for button in self.workspace_actions:
            button.configure(state="normal" if self.workspace and not self.busy else "disabled")
        if hasattr(self, "rule_builder"): self.rule_builder.sync_test()  # Run test query needs the workspace too.
        gov = bool(self.workspace) and getattr(self.session, "cloud", None) == GOV_CLOUD
        if gov and not self.gov_button.winfo_manager():
            self.gov_row.pack(fill="x", pady=(px(10), 0), after=self.workspace_row); self.gov_button.pack(side="left")
        elif not gov and self.gov_button.winfo_manager():
            self.gov_button.pack_forget(); self.gov_row.pack_forget()
        self.gov_button.configure(state="disabled" if self.busy else "normal")
        self.subbox.configure(state="readonly" if self.subscriptions and not self.busy else "disabled")
        self.wsbox.configure(state="readonly" if self.workspaces and not self.busy else "disabled")
        signed_in = self.session is not None and (bool(self.subscriptions) or self.signed_in_here())
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
        elif self.subscriptions and "No accessible workspaces" in self.identity.get():
            self.connection_note.set("This subscription has no workspaces you can open. Choose another subscription, "
                                     "or check your access.")
        elif self.subscriptions: self.connection_note.set("Select a subscription and workspace to continue.")
        elif signed_in and self.subscriptions_listed:
            self.connection_note.set("Signed in, but no subscriptions are visible yet. Check your access, then select Refresh.")
        elif signed_in:  # Not listed for this client yet, e.g. after switching back to it.
            client = " for " + self.client_name() if self.client_name() else ""
            self.connection_note.set("Listing the subscriptions" + client + "…"
                                     if self.busy and self.operation_title == LISTING_SUBSCRIPTIONS else
                                     "Signed in. Select Refresh to list the subscriptions" + client + ".")
        else: self.connection_note.set("Sign in to load your subscriptions and workspaces.")

    def work(self, title, task, done=None, page="connect", failed=None):
        """Run ``task`` on a worker thread and hand its result to ``done`` on the UI thread. ``title`` says
        what is happening; ``failed`` is the alert's heading if it fails ("Couldn't load workspaces")."""
        if self.busy:
            self.flash("Wait for the current task to finish.", "warning", 3); return
        self.operation_page, self.operation_title, self.operation_failed = page, title, failed
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
            kit.alert(self.root, "Open CyberQP", str(error), tone="warning")

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
            try:
                for process, title, signin in self.signin_handoff.new_windows():
                    self.log("Sign-in window check: " + process + (" — " + title if title else "")
                             + (" (sign-in window)" if signin else ""))
                self.signin_handoff.bring_forward()
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
            kit.alert(self.root, "Sign-in still required",
                      "Microsoft still requires authentication after the fresh sign-in. Nothing was retried. "
                      "Confirm Azure CLI is current. Check this client's Entra sign-in logs using the correlation ID "
                      "below, including Authentication Details and Conditional Access results, and confirm the account "
                      "and tenant. You can start another sign-in after resolving them.", details=str(error))
            return
        if not kit.confirm(self.root, "Complete Microsoft authentication?",
                           ("The Windows sign-in component couldn't complete verification. Try a fresh browser sign-in? "
                            if error.broker_failure else "Microsoft requires additional verification. Open Microsoft sign-in now? ") +
                           ("Azure supplied an additional authentication challenge; it will be included in this sign-in. "
                            if error.claims else "") +
                           "After signing in, check the workspace and start the task again. The interrupted task is not "
                           "replayed; earlier steps may already have completed. The full message is in Session activity.",
                           ok="Open sign-in", cancel="Not now"):
            return
        if not tenant:
            tenant = self.ask_tenant()
            if not tenant: return
        try: tenant = validate_tenant_hint(tenant)
        except Stop as problem:
            kit.alert(self.root, "Client tenant", str(problem)); return
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
            kit.alert(self.root, "Tenant", str(error))
            return
        self.vars["tenant"].set(hint)
        self.clear_selection()
        self.hide_signin_error()
        self.auth_hint = hint
        old = self.session
        self.session = Session(logger=self.log)
        self.session_client = self.client_name()
        broker = self.vars["login_method"].get() == "Windows account window"
        self.session.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"] = "true" if broker else "false"
        # With the broker, Windows asks in its own account window and no browser opens.
        self.signin_window = "the Windows account window" if broker else "your browser"
        self.signin_waiting.set(self.waiting_text())
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
                session.prepare_cloud(hint)
                check_cancelled(cancel)
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
            self.display_subscriptions(rows, select=self.remembered_workspace().get("subscription_id"))
        done._signin_cancel = cancel
        self.work("Waiting for Microsoft sign-in…", task, done)

    # ------------------------------------------------------------- discovery
    @staticmethod
    def subscription_label(row):
        """'Acme (Warned)  ·  22222222…2222': short enough for the box at the smallest window size, with a
        state other than Enabled beside the name. The full IDs are under Show workspace IDs and on the
        header chip."""
        state = row.get("state", "Unknown")
        name = row.get("name") or "Unnamed subscription"
        sid = row["id"]
        return name + ("" if state == "Enabled" else " (" + state + ")") + "  ·  " + \
            (sid[:8] + "…" + sid[-4:] if len(sid) > 13 else sid)

    def display_subscriptions(self, rows, discover=True, select=None):
        rows = sorted(rows, key=lambda r: (r.get("name", "").casefold(), r["id"]))
        self.subscriptions, self.subscriptions_listed = rows, True
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
        if not self.session or not (self.subscriptions or self.signed_in_here()):
            kit.alert(self.root, "Refresh subscriptions", "Sign in with Microsoft first.", tone="info"); return
        index = self.subbox.current()
        current = self.subscriptions[index]["id"] if 0 <= index < len(self.subscriptions) else ""
        def done(rows):
            self.clear_selection()
            self.display_subscriptions(rows, select=current or self.remembered_workspace().get("subscription_id"))
            if not rows: self.status.set("No subscriptions are visible for this account.")
        self.work(LISTING_SUBSCRIPTIONS, self.session.refresh_subscriptions, done, failed="Couldn't refresh subscriptions")

    def check_subscription(self):
        if self.busy:  # Before the form opens: the check couldn't start anyway.
            self.flash("Wait for the current task to finish.", "warning", 3); return
        if not self.session or not (self.subscriptions or self.signed_in_here()):
            kit.alert(self.root, "Check a subscription", "Sign in with Microsoft first.", tone="info"); return
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
            kit.modal(window, self.root, window.destroy, primary=buttons[-1], focus=buttons[-1])
            self.flash("Subscription check complete.", "info")
        self.work("Checking subscription access and workspaces…", lambda: self.session.check_subscription(value), done,
                  failed="Couldn't check the subscription")

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
        self.update_workspace_context()  # The header chip and title no longer name the cleared workspace.

    def subscription_changed(self, event=None):
        if event is not None and self.busy:  # Chosen in the moment before the busy visuals showed.
            if 0 <= self.subscription_index < len(self.subscriptions): self.subbox.current(self.subscription_index)
            return
        self.reset_workspaces()
        index = self.subscription_index = self.subbox.current()
        if index < 0: return
        sub = self.subscriptions[index]
        self.auth_hint = sub["tenantId"]
        self.vars["tenant"].set(self.auth_hint)
        self.identity.set(self.workspace_rows(sub, note="Loading workspaces…"))
        self.discover()

    def discover(self):
        if self.busy:  # Clicked in the moment before the busy visuals showed: keep the selection.
            self.flash("Wait for the current task to finish.", "warning", 3); return
        index = self.subbox.current()
        if not self.session or index < 0:
            kit.alert(self.root, "Refresh workspaces", "Sign in and choose a subscription first.", tone="info"); return
        sub = self.subscriptions[index]
        self.reset_workspaces()
        self.identity.set(self.workspace_rows(sub, note="Loading workspaces…"))  # Not the old workspace's IDs.
        def done(rows):
            self.workspaces = rows
            self.sync_connection()
            self.wsbox.configure(values=[r["workspace_name"] + "  ·  " + r["resource_group"] for r in rows])
            if rows:
                # The workspace this client used last time, or the first one.
                remembered = self.remembered_workspace().get("workspace_id")
                chosen = next((i for i, r in enumerate(rows) if r["workspace_id"] == remembered), 0)
                self.wsbox.current(chosen); self.workspace_changed()
                count = len(rows)
                self.flash(f"Found {count} workspace{'s' if count != 1 else ''} in this subscription. "
                           f"Selected {rows[chosen]['workspace_name']}.")
            else:
                self.identity.set(self.workspace_rows(sub, note="No accessible workspaces"))
                self.status.set("No workspaces in this subscription. Choose another subscription, or check your access.")
        self.work("Loading workspaces…", lambda: self.session.discover(sub["id"], sub["tenantId"]), done,
                  failed="Couldn't load workspaces")

    def remembered_workspace(self):
        """The subscription and workspace this client used last ({} when none), from ui-state.json."""
        value = self.ui_state.get("last_workspace")
        value = value.get(self.client_name()) if isinstance(value, dict) else None
        return value if isinstance(value, dict) else {}

    def workspace_changed(self, event=None):
        index = self.wsbox.current()
        if event is not None and self.busy:  # Chosen in the moment before the busy visuals showed.
            current = next((i for i, r in enumerate(self.workspaces) if r == self.workspace), -1)
            if current >= 0: self.wsbox.current(current)
            return
        if index < 0: return
        self.workspace = dict(self.workspaces[index])
        client = self.client_name()
        if client:  # Picked again after the next sign-in for this client (ui-state, not clients.json).
            remembered = self.ui_state.get("last_workspace")
            if not isinstance(remembered, dict): remembered = self.ui_state["last_workspace"] = {}
            remembered[client] = dict(subscription_id=self.workspace["subscription_id"],
                                      workspace_id=self.workspace["workspace_id"])
            self.save_state()
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
        # The IDs show only for a chosen workspace, behind their disclosure; until then the note above
        # the pickers is the only instruction.
        if not self.workspace:
            self.ids_button.pack_forget(); self.details_panel.pack_forget()
        else:
            if not self.ids_button.winfo_manager(): self.ids_button.pack(fill="x", pady=(px(14), 0))
            if self.show_ids and not self.details_panel.winfo_manager():
                self.details_panel.pack(fill="x", pady=(px(8), 0), after=self.ids_button)
            elif not self.show_ids: self.details_panel.pack_forget()
        self.ids_button.configure(text=("−  Hide" if self.show_ids else "+  Show") + " workspace IDs")
        self.render_session()

    def open_gov_onboarding(self):
        if self.busy or not self.workspace: return
        if "/" not in self.github_repository():
            kit.alert(self.root, "Onboard GCC High client", "Set and save the rule library (organization and repository) "
                      "first. The target pull request is opened there.", tone="info")
            self.show_github_settings(); return
        GovOnboardingDialog(self)

    def copy_workspace(self):
        if not self.workspace:
            kit.alert(self.root, "Copy details", "Choose a workspace first.", tone="info")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.identity.get())
        self.copy_button.configure(text="Copied")
        self.root.after(2000, lambda: self.copy_button.winfo_exists() and self.copy_button.configure(text="Copy details"))
        self.flash("Workspace details copied.")

    # ----------------------------------------------------------------- github
    def github_login(self):
        if self.busy:  # Before the page scrolls: the sign-in couldn't start anyway.
            self.flash("Wait for the current task to finish.", "warning", 3); return
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
        self.work("Finish signing in to GitHub in the browser; the one-time code appears in Session activity…", task, done,
                  failed="GitHub sign-in didn't finish")

    def logout(self):
        if not self.session or self.busy: return
        session = self.session
        def done(_):
            self.session = self.session_client = None; self.clear_selection(); self.flash("Signed out.")
        self.work("Signing out…", session.logout, done, failed="Couldn't sign out")

    def close(self):
        if self.busy:
            if not kit.confirm(self.root, "Close Sentinel Workspace?",
                               "A task is still running. If you close now it will be abandoned; nothing already sent "
                               "to Azure or GitHub is undone.", ok="Close anyway", cancel="Keep working", destructive=True):
                return
            # Unsaved builder work stays in the recovery draft, offered at the next start (unless it
            # was already discarded by the close that is signing out now).
            self.rule_builder.autosave(schedule=False); self.rule_builder.stop_autosave()
            self.save_state(); self.root.destroy(); return
        if not self.rule_builder.can_discard():
            return
        # A normal close: nothing is left to recover, and the autosave timer must not write the
        # discarded changes again while the sign-out below runs.
        self.closing = True; self.rule_builder.stop_autosave()
        self.rule_builder.discard_recovery()
        self.save_state()
        if self.session:
            session = self.session
            self.work("Signing out before closing…", session.logout, lambda _: self.root.destroy(), failed="Couldn't sign out")
        else: self.root.destroy()

    def end_session(self):
        """Windows is signing out or restarting (Tk turns WM_QUERYENDSESSION into WM_SAVE_YOURSELF): keep the
        layout and any unsaved Rule builder work, which is offered at the next start. Nothing waits on Azure
        (Windows allows only a few seconds), and the window stays open in case the shutdown is cancelled."""
        if not self.closing:
            self.rule_builder.autosave(schedule=False)
        self.save_state()


APP_USER_MODEL_ID = "ISIEnterprises.SentinelWorkspace"
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
_INSTANCE = []  # The single-instance mutex handle, held for the life of the process.
_ELEVATED_OWNER = []  # Set when the running copy was started with Run as administrator.


def claim_instance(kernel32=None, last_error=None):
    """Hold a per-session named mutex. Returns False when another copy of the app already holds it.
    Two copies would race on ui-state.json, clients.json and settings.json and run two Azure CLI
    sessions, so a second launch brings the first window forward instead. A copy running as
    administrator holds the mutex at a higher integrity level, so this one is refused access to it
    (ERROR_ACCESS_DENIED); that is another copy too (``_ELEVATED_OWNER`` is set)."""
    try:
        import ctypes
        from ctypes import wintypes
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
            last_error = ctypes.get_last_error
        handle = kernel32.CreateMutexW(None, False, "Local\\" + APP_USER_MODEL_ID)
        error = (last_error or ctypes.get_last_error)()
    except (AttributeError, OSError, ImportError):
        return True  # Not Windows, or no mutex: never stop the app from starting.
    if not handle and error == ERROR_ACCESS_DENIED:
        _ELEVATED_OWNER.append(True); return False
    if handle: _INSTANCE.append(handle)
    return not (handle and error == ERROR_ALREADY_EXISTS)


def is_app_window(title):
    return title == TITLE or title.endswith(" — " + TITLE)


def bring_existing_forward(user32=None):
    """Restore and focus the running copy's main window. Returns True when one was found."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = user32 or ctypes.windll.user32
        found = []
        def visit(hwnd, _):
            buffer, kind = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, buffer, 512); user32.GetClassNameW(hwnd, kind, 64)
            if kind.value == "TkTopLevel" and is_app_window(buffer.value) and user32.IsWindowVisible(hwnd):
                found.append(hwnd); return False
            return True
        user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
        if not found: return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)  # SW_RESTORE when minimised, else SW_SHOW.
        user32.SetForegroundWindow(hwnd)
        return True
    except (AttributeError, OSError, ImportError, TypeError):
        return False


def set_dpi_awareness():
    """System DPI awareness, as IDLE uses. Tk 8.6 ignores per-monitor DPI changes, so per-monitor
    awareness left the window 1.25-1.5x too big or small on a monitor with another scale; Windows
    now scales it there instead (correct size, slightly soft text on that monitor only). On the
    main monitor, text is drawn at full resolution, exactly as before."""
    import ctypes
    user32 = ctypes.windll.user32
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2)): return  # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(1) == 0: return  # PROCESS_SYSTEM_DPI_AWARE
    except (AttributeError, OSError):
        pass
    try: user32.SetProcessDPIAware()
    except (AttributeError, OSError): pass


def report_startup_failure(error):
    """The window couldn't be built: write the traceback to errors.log (or, when DATA can't be written,
    to the temp folder) and say so in plain words, instead of the packaged exe's raw Python error box."""
    text = (datetime.now().isoformat(timespec="seconds") + "  Sentinel Workspace " + __version__ + " (start-up)\n"
            + "".join(traceback.format_exception(type(error), error, error.__traceback__)))
    log_path = DATA / "logs" / "errors.log"
    try:
        append_error_log(log_path, text)
    except OSError:
        log_path = Path(tempfile.gettempdir()) / "Sentinel Workspace errors.log"
        try: append_error_log(log_path, text)
        except OSError: log_path = None
    message = advice.lookup(str(error)) or "Something went wrong while starting."
    if log_path is not None: message += " Details were saved to " + str(log_path) + "."
    try:
        from tkinter import messagebox
        messagebox.showerror("Sentinel Workspace couldn't start", message)
    except tk.TclError:
        pass
    return log_path


def main():
    if os.name == "nt":
        if not claim_instance():
            bring_existing_forward()
            if _ELEVATED_OWNER:
                # Windows may not let this copy restore a window running as administrator.
                from tkinter import messagebox
                messagebox.showinfo(TITLE, "Sentinel Workspace is already running as administrator. Switch to it "
                                           "from the taskbar or with Alt+Tab.")
            return
        import ctypes
        try:
            set_dpi_awareness()
        except (AttributeError, OSError, ImportError):
            pass
        try:
            # Group the window under its own taskbar button with the app's icon, instead of Python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass
    root = None
    try:
        root = tk.Tk()
        app = App(root)
    except Exception as error:  # SystemExit and KeyboardInterrupt still pass through.
        if root is not None:
            try: root.destroy()
            except tk.TclError: pass
        report_startup_failure(error)
        return
    if app.start_zoomed: root.after_idle(lambda: root.state("zoomed"))
    root.mainloop()


if __name__ == "__main__":
    main()
