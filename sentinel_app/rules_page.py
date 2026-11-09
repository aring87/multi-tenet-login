"""Workspace-scoped analytics rule inventory view."""
import json
from copy import deepcopy
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, filedialog
from . import ui_kit as kit
from .advice import lookup as advice_for
from .desktop_theme import COLORS, px
from .rule_export import export_rules
from .workspace_tools import list_rules, rule_values

STATES = (("All states", "All"), ("Enabled", "Enabled"), ("Disabled", "Disabled"), ("Not specified", "Not specified"))
STATE_ORDER = ("Enabled", "Disabled", "Not specified")
SEVERITY_TONES = dict(High="danger", Medium="warning", Low="info", Informational="neutral")
MONO_KEYS = {"Rule ID", "Template"}
ALERT_WORDS = {"GreaterThan": "results are more than", "LessThan": "results are fewer than",
               "NotEqual": "results are not", "Equal": "results equal"}
# The name column starts narrow and stretches: a Treeview first laid out narrower than its columns
# never shrinks them to fit, so the starting widths add up to less than the narrowest pane.
COLUMNS = (("name", "Rule name", 100, True), ("state", "State", 100, False), ("severity", "Severity", 100, False),
           ("kind", "Type", 110, False), ("modified", "Last modified", 120, False))
# Optional columns join the table once it is this wide (96-DPI px), so names keep their room.
OPTIONAL = dict(modified=560, kind=680)
# Sort keys per column, from a cache entry (values, haystack, lastModifiedUtc); None sorts last.
SORT_KEYS = dict(name=lambda entry: entry[0][0].casefold(),
                 state=lambda entry: STATE_ORDER.index(entry[0][1]),
                 severity=lambda entry: kit.SEVERITY_RANK.get(entry[0][2]),
                 kind=lambda entry: entry[0][3].casefold(),
                 modified=lambda entry: entry[2] or None)


def friendly_stamp(value):
    """'2026-10-12T09:41:00Z' -> '12 Oct 2026, 09:41' in local time; anything else unchanged."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.astimezone().strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(value or "")


def short_stamp(value):
    """'2026-10-28T09:12:00Z' -> '28 Oct 10:12' in local time, or '28 Oct 2025' in another year."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return ""
    return moment.strftime("%d %b %H:%M" if moment.year == datetime.now().year else "%d %b %Y")


def rule_overview(row):
    """Readable summary rows for one rule, schedule first; absent fields are skipped."""
    props = row.get("properties", {})
    join = lambda value: ", ".join(value) if isinstance(value, list) else value
    alert = None
    if "triggerOperator" in props or "triggerThreshold" in props:
        alert = ALERT_WORDS.get(props.get("triggerOperator", "GreaterThan"), str(props.get("triggerOperator"))) + \
            " " + str(props.get("triggerThreshold", 0))
    rows = [("Runs every", kit.duration_text(props["queryFrequency"]) if props.get("queryFrequency") else None),
            ("Looks back", kit.duration_text(props["queryPeriod"]) if props.get("queryPeriod") else None),
            ("Alert when", alert),
            ("Tactics", join(props.get("tactics"))), ("Techniques", join(props.get("techniques"))),
            ("Last modified", friendly_stamp(props["lastModifiedUtc"]) if props.get("lastModifiedUtc") else None),
            ("Rule ID", row.get("name")), ("Template", props.get("alertRuleTemplateName"))]
    return [(key, str(value)) for key, value in rows if value not in (None, "", [])]


def saved_sort(value, keys, default=("name", False)):
    """A (column, descending) pair read back from ui-state, or ``default`` when it is unusable."""
    try:
        key, descending = value
        if key in keys and isinstance(descending, bool): return key, descending
    except (TypeError, ValueError):
        pass
    return default


def flow(bar, item, column, room=220):
    """Keep ``item`` (a filter) at the end of a gridded toolbar row while the row leaves ``room``
    (96-DPI px) for the search box, and move it onto a second row in a narrow pane. Returns the
    placing function, to call again when ``item`` changes width; ``place.hide(True)`` takes
    ``item`` off the bar until ``place.hide(False)`` puts it back."""
    state = {"wide": None, "hidden": False}
    def place(event=None):
        width = bar.winfo_width()
        if width <= 1 or state["hidden"]: return
        taken = sum(widget.winfo_reqwidth() + px(12) for widget in bar.grid_slaves(row=0)
                    if widget is not item and not isinstance(widget, ttk.Entry))
        wide = width >= taken + item.winfo_reqwidth() + px(room)
        if wide == state["wide"]: return
        state["wide"] = wide
        if wide: item.grid(row=0, column=column, columnspan=1, sticky="e", padx=(px(12), 0), pady=0)
        else: item.grid(row=1, column=0, columnspan=column + 1, sticky="w", padx=0, pady=(px(8), 0))
    def hide(hidden=True):
        if hidden == state["hidden"]: return
        state["hidden"], state["wide"] = hidden, None
        if hidden: item.grid_remove()
        else: place()
    bar.bind("<Configure>", place, add="+")
    place.hide = hide
    return place


def fit_columns(tree, needs):
    """Show an optional column only while the table is at least ``needs[key]`` (96-DPI px) wide;
    the other columns always show. Each time the shown columns change, the stretching ones take up
    the difference, so the columns fill the table exactly: a Treeview shares out only later size
    changes, and leaves a column it gains (or a first layout narrower than its columns) as overflow."""
    keys, shown = tuple(tree["columns"]), [None]
    def fit():
        if not tree.winfo_exists(): return
        visible = shown[0] or keys
        stretchy = [key for key in visible if tree.tk.getboolean(tree.column(key, "stretch"))]
        lead = int(tree.column("#0", "width")) if "tree" in str(tree.cget("show")) else 0
        spare = tree.winfo_width() - lead - sum(int(tree.column(key, "width")) for key in visible)
        for index, key in enumerate(stretchy):
            width = int(tree.column(key, "width"))
            new = max(int(tree.column(key, "minwidth")), width + spare // (len(stretchy) - index))
            tree.column(key, width=new); spare -= new - width
    def update(event):
        visible = tuple(key for key in keys if event.width >= px(needs.get(key, 0)))
        if visible != shown[0]:
            shown[0] = visible
            tree.configure(displaycolumns=visible)
            tree._root().after_idle(fit)  # After the Treeview's own layout for this size.
    tree.bind("<Configure>", update, add="+")


def link_search(entry, tree, render_soon):
    """kit.link_search for a debounced search: Down or Enter first runs a filter still waiting
    for its pause, so the row they move to is the first row of the filtered list."""
    for key in ("<Down>", "<Return>"):
        entry.bind(key, lambda event: render_soon.flush(), add="+")  # Bound first, so it runs first.
    kit.link_search(entry, tree)


def keep_in_view(area, widget, margin=40):
    """Scroll a kit.ScrollArea so ``widget`` (inside it) shows, ``margin`` (96-DPI px) below the
    top of the view when it has to move; a widget already wholly in view stays put."""
    canvas, inner = area.canvas, area.inner
    canvas.update_idletasks()
    height, view = inner.winfo_height(), canvas.winfo_height()
    if height <= view: return
    top, first = widget.winfo_rooty() - inner.winfo_rooty(), canvas.canvasy(0)
    if first <= top and top + widget.winfo_height() <= first + view: return
    canvas.yview_moveto(max(0, (top - px(margin)) / height))


def detail_pane(card):
    """A details card body that scrolls by itself, with the scrollbar near the card's edge and
    the empty-state message wrapped to the pane. Keyboard focus moving onto a part scrolled out
    of view brings it into view. Returns (scroll area, empty overlay)."""
    card.body.configure(padding=(px(20), px(16), px(8), px(20)))
    area = kit.ScrollArea(card.body)
    area.canvas.configure(width=px(200), height=px(120))  # The pane, not the content, sets the size.
    area.inner.configure(padding=(0, 0, px(12), 0))
    # Never empty: Tk keeps an emptied frame's last height, which would leave a scroll range behind.
    tk.Frame(area.inner, height=1, bg=COLORS["card"], bd=0, highlightthickness=0).pack(fill="x")
    area.frame.pack(fill="both", expand=True)
    def follow(event):
        widget = event.widget
        if isinstance(widget, str) or str(widget.tk.call("focus")) != str(widget): return  # Not the focus itself.
        try: under = str(widget.winfo_containing(*widget.winfo_pointerxy()))
        except (KeyError, tk.TclError): under = ""
        if under == str(widget) or under.startswith(str(widget) + "."): return  # Clicked, so already in view.
        keep_in_view(area, widget)
    area.frame.bind_class(area.tag, "<FocusIn>", follow, add="+")  # Every widget in the area carries the tag.
    empty = kit.EmptyOverlay(card.body, icon="document")
    card.body.bind("<Configure>", lambda event: empty.label.configure(wraplength=max(px(160), event.width - px(48))),
                   add="+")
    return area, empty


class CopyValues(kit.KeyValues):
    """kit.KeyValues whose values each copy from a right-click menu, so a single ID can be taken
    without copying every row. ``copied(key)`` is called after a copy, to confirm it."""

    def __init__(self, parent, copied=None, **options):
        super().__init__(parent, **options)
        self.copied, self.keys, self.menu = copied, [], None

    def set(self, rows, mono=None):
        rows = [tuple(row) for row in rows]
        super().set(rows, mono)
        self.keys = [key for key, _ in rows]
        for key, label in zip(self.keys, self.values):
            label.bind("<Button-3>", lambda event, k=key, w=label: self.show_menu(event, k, w), add="+")

    def show_menu(self, event, key, label):
        if self.menu is None: self.menu = kit.popup_menu(self.winfo_toplevel())  # Outlives set(), which clears children.
        self.menu.delete(0, "end")
        name = key if key[:2].isupper() else key[:1].lower() + key[1:]  # 'Copy tenant ID', 'Copy ID'.
        self.menu.add_command(label="Copy " + name, command=lambda: self.copy(key, label))
        try: self.menu.tk_popup(event.x_root, event.y_root)
        finally: self.menu.grab_release()
        return "break"

    def copy(self, key, label):
        # The label may show a path or ID shortened with '…'; copy the whole value.
        self.clipboard_clear(); self.clipboard_append(str(getattr(label, "full_text", label.cget("text"))))
        if self.copied: self.copied(key)


class Split(ttk.Panedwindow):
    """A list beside its details. The sash starts at ``fraction`` of the width the first time the
    split is shown, keeps that share as the window is resized, and stays where both panes remain
    usable when dragged. ``fraction()`` is the share to remember, or None before it was shown."""

    LEFT, RIGHT = 380, 300  # Narrowest list and details panes, 96-DPI px.

    def __init__(self, parent, fraction=0.6):
        super().__init__(parent, orient="horizontal")
        try: fraction = float(fraction)
        except (TypeError, ValueError): fraction = 0.6
        self.share, self.seen_width = min(max(fraction, 0.3), 0.8), None
        self.bind("<Configure>", self.resized, add="+")
        self.bind("<ButtonRelease-1>", lambda event: self.dragged(), add="+")

    def place_sash(self):
        width = self.winfo_width()
        if width <= 1 or len(self.panes()) < 2: return
        position = int(width * self.share)
        if width >= px(self.LEFT + self.RIGHT):
            position = max(px(self.LEFT), min(position, width - px(self.RIGHT)))
        self.sashpos(0, position)

    def resized(self, event):
        if event.width > 1 and event.width != self.seen_width:
            self.seen_width = event.width
            self.place_sash()

    def dragged(self):
        width = self.winfo_width()
        if width <= 1 or len(self.panes()) < 2: return
        self.share = self.sashpos(0) / width
        self.place_sash()
        self.share = self.sashpos(0) / width

    def fraction(self):
        return round(self.share, 3) if self.seen_width else None


class RawConfig:
    """The 'Full configuration' disclosure: a toggle row with Copy JSON, and the JSON area it
    opens. The area stays open or closed as the selection changes."""

    def __init__(self, parent, copy, height=14):
        self.row = ttk.Frame(parent, style="Card.TFrame")
        self.button = ttk.Button(self.row, text="+  Full configuration", style="Disclosure.TButton", command=self.toggle)
        self.button.pack(side="left", fill="x", expand=True)
        self.copy_button = ttk.Button(self.row, text="Copy JSON", style="Small.Ghost.TButton", command=copy)
        self.copy_button.pack(side="right", padx=(px(8), 0))
        self.frame, self.text = kit.text_area(parent, height, readonly=True)
        self.open = False

    def toggle(self, show=None):
        self.open = (not self.open) if show is None else show
        self.button.configure(text=("−  " if self.open else "+  ") + "Full configuration")
        self.place()

    def place(self, visible=True):
        """Pack the toggle (and the JSON while open) after what is already packed, or hide both."""
        if not visible:
            self.row.pack_forget(); self.frame.pack_forget(); return
        if not self.row.winfo_manager(): self.row.pack(fill="x", pady=(kit.space("lg"), 0))
        if self.open and not self.frame.winfo_manager(): self.frame.pack(fill="x", pady=(px(8), 0))
        elif not self.open: self.frame.pack_forget()


class RulesPage:
    def __init__(self, app, parent):
        self.app = app
        self.rows = []
        self.cache = []  # (values, haystack, lastModifiedUtc) per row, so filtering never re-derives them.
        self.collection = None
        self.loaded_session = None
        self.loaded_workspace = None
        self.shown = -1  # Index of the rule in the details pane: None for none, -1 until it is drawn.
        self.sort = saved_sort(app.ui_state.get("rules_sort"), SORT_KEYS)
        # The list fills the window and its details sit beside it; each scrolls by itself.
        self.split = Split(parent, app.ui_state.get("rules_split", 0.6))
        self.split.pack(fill="both", expand=True)

        card = kit.Card(self.split, "Analytics rules", "", icon="rules")
        self.split.add(card.frame, weight=3)
        self.table_card = card
        self.summary = card.subtitle_var
        # Not registered in app.controls: sync() decides when these are usable.
        self.refresh_button = ttk.Button(card.actions, text="Refresh", command=self.load, style="Primary.TButton")
        self.refresh_button.pack(side="right"); kit.tooltip(self.refresh_button, "Read the rules from Sentinel again (F5)")
        self.export_button = ttk.Button(card.actions, text="Export all…", command=self.export, style="Secondary.TButton")
        self.export_button.pack(side="right", padx=(0, px(8)))
        kit.tooltip(self.export_button, "Save every loaded rule as a ZIP of JSON and CSV files")
        body = card.body
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=(0, px(12)))
        bar.columnconfigure(0, weight=1)
        self.search = tk.StringVar()
        self.filter = tk.StringVar(value="All states")
        self.search_entry = ttk.Entry(bar, textvariable=self.search)
        self.search_entry.grid(row=0, column=0, sticky="ew")
        app.inputs.append(self.search_entry)  # Searching stays possible while a task runs.
        kit.placeholder(self.search_entry, self.search, "Search rules  (Ctrl+F)", search=True)
        self.filter_bar = kit.Segmented(bar, self.filter, STATES, compact=True)
        self.place_filters = flow(bar, self.filter_bar, 1)
        self.notice = tk.StringVar()
        self.banner = kit.Banner(body, self.notice, "danger")
        self.table_frame, self.tree, self.empty = kit.table(body, COLUMNS, height=6, sort=self.sort_by,
                                                            menu=self.row_menu, marker=True)
        self.table_frame.pack(fill="both", expand=True)
        self.empty.line.configure(width=px(160))  # The loading line's own width stays inside a narrow pane.
        fit_columns(self.tree, OPTIONAL)
        self.tree.sort_state = self.sort; kit.set_headings(self.tree)
        self.build_detail()
        self.render_soon = kit.debounce(self.tree, 150, self.render)
        link_search(self.search_entry, self.tree, self.render_soon)
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.tree.bind("<Return>", self.open_row)
        self.search.trace_add("write", self.render_soon)
        self.filter.trace_add("write", lambda *args: self.render())
        self.summary.set("Select a workspace to load its rules.")
        self.render()

    def build_detail(self):
        detail = kit.Card(self.split, "Rule details", "Select a rule to see its details here.", icon="document",
                          tone="neutral")
        self.split.add(detail.frame, weight=2)
        self.detail_card = detail
        self.detail_area, self.detail_empty = detail_pane(detail)
        inner = self.detail_area.inner
        self.pills = ttk.Frame(inner, style="Card.TFrame")
        self.description = tk.StringVar()
        self.description_label = kit.wrap_to_parent(ttk.Label(inner, textvariable=self.description, style="Body.TLabel",
                                                              justify="left", wraplength=px(300)), minimum=px(120))
        self.overview = CopyValues(inner, key_width=12, copied=lambda key: self.app.flash(key + " copied."))
        self.query_label = kit.section_label(inner, "Query", action=("Copy query", lambda: self.copy(self.query, "Query")))
        self.query_frame, self.query = kit.text_area(inner, 8, readonly=True)
        self.raw = RawConfig(inner, lambda: self.copy(self.details, "Rule configuration"))
        self.details = self.raw.text

    # ------------------------------------------------------------- state
    def ready(self):
        return bool(self.app.session and self.app.workspace)

    def current_collection(self):
        return (self.collection is not None and self.app.session is self.loaded_session
                and bool(self.app.session) and self.app.workspace == self.loaded_workspace)

    def sync(self):
        """Refresh needs a workspace; Export needs rules loaded for that workspace."""
        busy = self.app.busy
        self.refresh_button.configure(state="normal" if self.ready() and not busy else "disabled")
        self.export_button.configure(state="normal" if self.current_collection() and self.rows and not busy else "disabled")

    def copy(self, widget, what):
        """Put a text area's contents on the clipboard and confirm in the status bar."""
        text = widget.get("1.0", "end-1c")
        if not text.strip():
            self.app.flash("Select a rule first.", "info", 3); return
        self.copy_text(text, what)

    def copy_text(self, text, what):
        self.app.root.clipboard_clear(); self.app.root.clipboard_append(text)
        self.app.flash(what + " copied.")

    def row_menu(self, iid):
        row = self.rows[int(iid)]
        query = row.get("properties", {}).get("query")
        return [("Copy name", lambda: self.copy_text(rule_values(row)[0], "Rule name")),
                ("Copy rule ID", lambda: self.copy_text(row.get("name", ""), "Rule ID"), bool(row.get("name"))),
                ("Copy query", lambda: self.copy_text(query, "Query"), bool(query))]

    def open_row(self, event=None):
        """Enter on a rule moves into its details: to Copy query, or to the configuration toggle."""
        if not self.tree.selection(): return None
        target = self.query_label.button if self.query_frame.winfo_manager() else self.raw.button
        target.focus_set(); keep_in_view(self.detail_area, target)
        return "break"

    def show_notice(self, text):
        """Warn above the list that not every rule could be read."""
        self.notice.set(text)
        if not self.banner.winfo_manager(): self.banner.pack(fill="x", pady=(0, px(12)), before=self.table_frame)

    def detail(self, row=None):
        for child in self.pills.winfo_children(): child.destroy()
        for part in (self.pills, self.description_label, self.overview, self.query_label, self.query_frame):
            part.pack_forget()
        self.raw.place(False)  # Repacked last, so the parts keep their order.
        if row is None:
            self.detail_card.title_var.set("Rule details")
            self.detail_card.subtitle_var.set("Select a rule to see its details here.")
            self.description.set(""); self.overview.set([])
            kit.set_text(self.query, ""); kit.set_text(self.details, "")
            self.detail_empty.show("Its schedule, MITRE mapping, query and full configuration appear here.")
            return
        self.detail_empty.hide()
        name, state, severity, kind = rule_values(row)
        props = row.get("properties", {})
        self.detail_card.title_var.set(name)
        self.detail_card.subtitle_var.set(kind + " rule")
        kit.Pill(self.pills, state, dict(Enabled="success", Disabled="neutral").get(state, "warning")).pack(side="left")
        if severity in SEVERITY_TONES:
            kit.Pill(self.pills, severity, SEVERITY_TONES[severity], dot=False).pack(side="left", padx=(px(6), 0))
        self.pills.pack(fill="x", pady=(0, px(12)))
        self.description.set(props.get("description", "").strip())
        if self.description.get(): self.description_label.pack(fill="x", pady=(0, px(12)))
        self.overview.set(rule_overview(row), mono=MONO_KEYS)
        self.overview.pack(fill="x")
        query = props.get("query")
        kit.set_text(self.query, query or "")
        if query:
            self.query.configure(height=min(14, max(4, query.count("\n") + 2)))
            self.query_label.pack(fill="x", pady=(kit.space("lg"), px(8)))
            self.query_frame.pack(fill="x")
        kit.set_text(self.details, json.dumps(row, indent=2, ensure_ascii=False))
        self.raw.place()
        self.detail_area.canvas.yview_moveto(0)

    def show(self, index):
        """Show rule ``index`` (or nothing) in the details pane, unless it is already there."""
        if index == self.shown: return
        self.shown = index
        self.detail(self.rows[index] if index is not None else None)

    def reset(self):
        self.collection = None; self.loaded_session = None; self.loaded_workspace = None
        self.rows = []; self.cache = []; self.banner.pack_forget(); self.render()
        self.summary.set("Select Refresh to read the rules from Sentinel." if self.ready()
                         else "Select a workspace to load its rules.")
        self.label_filters(counted=False)
        self.sync()

    def label_filters(self, counted=True):
        """Show how many loaded rules each state filter holds ('Enabled 7'), or just the names."""
        states = [values[1] for values, *_ in self.cache]
        for value, label in STATES:
            count = len(states) if value == "All states" else states.count(value)
            self.filter_bar.set_label(value, f"{label} {count}" if counted else label)
        self.place_filters()

    def build_cache(self):
        self.cache, self.shown = [], -1
        for row in self.rows:
            modified = row.get("properties", {}).get("lastModifiedUtc")
            values = rule_values(row) + (short_stamp(modified) if modified else "",)
            self.cache.append((values, (" ".join(map(str, values)) + " " + row.get("name", "")).casefold(), modified))

    def sort_by(self, key, descending):
        """A heading was clicked: redraw in that order (kept for next time by App.save_state)."""
        self.sort = (key, descending)
        self.render()

    def order(self):
        """Cache indexes in the chosen order; ties and rules without a value stay by name."""
        key, descending = self.sort
        value = SORT_KEYS.get(key, SORT_KEYS["name"])
        indexes = sorted(range(len(self.cache)), key=lambda index: self.cache[index][0][0].casefold())
        filled = [index for index in indexes if value(self.cache[index]) is not None]
        filled.sort(key=lambda index: value(self.cache[index]), reverse=descending)
        return filled + [index for index in indexes if value(self.cache[index]) is None]

    def render(self):
        self.render_soon.cancel()
        if len(self.cache) != len(self.rows): self.build_cache()
        keep = self.tree.selection()[0] if self.tree.selection() else None
        self.tree.delete(*self.tree.get_children())
        needle = self.search.get().strip().casefold()
        state = self.filter.get()
        for index in self.order():
            values, haystack, _ = self.cache[index]
            if state not in ("All states", values[1]): continue
            if needle and needle not in haystack: continue
            self.tree.insert("", "end", iid=str(index), values=values,
                             image=kit.dot(SEVERITY_TONES.get(values[2], "neutral"), self.tree),
                             tags=("muted",) if values[1] != "Enabled" else ())
        if keep is not None and self.tree.exists(keep):
            self.tree.selection_set(keep); self.tree.see(keep)  # Keep the open details while the list is filtered.
        else:
            self.show(None)
        if self.tree.get_children(): self.empty.hide()
        elif self.rows:
            term = self.search.get().strip()
            text = (f"No rules match “{term}”" if term else "No rules") + ("" if state == "All states" else f" with the {state} filter") + "."
            self.empty.show(text, ("Clear search and filter", self.clear_filters))
        elif not self.ready():
            self.empty.show("Sign in and choose a workspace to see its analytics rules.",
                            ("Go to Connect", lambda: self.app.show_page("connect")))
        else:
            self.empty.show("No rules loaded yet. Select Refresh to read them from Sentinel.")
        self.sync()

    def clear_filters(self):
        self.search.set(""); self.filter.set("All states")

    def selected(self, event=None):
        selection = self.tree.selection()
        self.show(int(selection[0]) if selection else None)

    def load(self):
        app = self.app
        if app.busy or not self.ready(): return
        session, workspace = app.session, dict(app.workspace)
        self.reset(); self.summary.set("Loading rules from " + workspace["workspace_name"] + "…")
        self.empty.show("Loading rules from " + workspace["workspace_name"] + "…", busy=True)
        def task():
            try: return list_rules(session, workspace)
            except Exception as error: return dict(records=[], status="error", error=str(error))
        def done(result):
            if app.session is not session or app.workspace != workspace: return
            self.collection = deepcopy(result)
            self.collection.setdefault("collected_at_utc", datetime.now(timezone.utc).isoformat())
            self.loaded_session = session; self.loaded_workspace = deepcopy(workspace)
            self.rows = self.collection["records"]
            self.build_cache()
            self.render()
            self.label_filters()
            count = len(self.rows)
            text = f"{count} rule{'s' if count != 1 else ''} from {workspace['workspace_name']}"
            if result["status"] not in ("collected", "no_records"):
                raw = result.get("error", result["status"])
                reason = raw.replace("_", " ")
                text = "Some rules couldn't be read. " + text + "."
                advice = advice_for(raw, workspace=workspace["workspace_name"])
                self.show_notice(advice or "Couldn't read all the analytics rules: " + reason)
                app.log("Rules read for " + workspace["workspace_name"] + ": " + reason)
                if not self.rows: self.empty.show("The rules couldn't be loaded. Select Refresh to try again.")
                app.status.set("Couldn't read the rules for " + workspace["workspace_name"] + ".")
            elif not self.rows:
                text = "This workspace has no analytics rules."
                self.empty.show("This workspace has no analytics rules.")
                app.flash("No analytics rules in " + workspace["workspace_name"] + ".", "info")
            else:
                app.flash("Rules loaded for " + workspace["workspace_name"] + ".")
            self.summary.set(text); self.sync()
        app.work("Reading analytics rules…", task, done, page="rules")

    def export(self):
        app = self.app
        if app.busy: return
        if not self.current_collection():
            kit.alert(app.root, "Export all", "Load the rules for the current workspace before exporting.", tone="info")
            return
        complete = self.collection.get("status") in ("collected", "no_records")
        if not complete:
            if not self.rows:
                kit.alert(app.root, "Export all", "The rules couldn't be read. Select Refresh before exporting.", tone="info")
                return
            if not kit.confirm(app.root, "Export all",
                               "Some rules couldn't be read. Export the available rules and mark the export as incomplete?",
                               ok="Export anyway"): return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = "analytics-rules-" + self.loaded_workspace["workspace_id"] + "-" + stamp + ".zip"
        path = filedialog.asksaveasfilename(parent=app.root, title="Export all analytics rules",
                initialfile=filename, defaultextension=".zip", filetypes=[("Rule export (ZIP)", "*.zip")])
        if not path: return
        workspace, collection = deepcopy(self.loaded_workspace), deepcopy(self.collection)
        def done(manifest):
            status = "complete collection" if manifest["complete"] else "INCOMPLETE collection"
            app.flash(f"Exported {manifest['record_count']} rules ({status}) to {path}")
        app.work("Exporting the rules…", lambda: export_rules(path, workspace, collection), done, page="rules")
