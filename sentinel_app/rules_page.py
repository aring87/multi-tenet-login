"""Workspace-scoped analytics rule inventory view."""
import json
from copy import deepcopy
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from . import ui_kit as kit
from .desktop_theme import px
from .rule_export import export_rules
from .workspace_tools import list_rules, rule_values

STATES = (("All states", "All"), ("Enabled", "Enabled"), ("Disabled", "Disabled"), ("Not specified", "Not specified"))
SEVERITY_TONES = dict(High="danger", Medium="warning", Low="info", Informational="neutral")
MONO_KEYS = {"Rule ID", "Template"}
ALERT_WORDS = {"GreaterThan": "results are more than", "LessThan": "results are fewer than",
               "NotEqual": "results are not", "Equal": "results equal"}


def friendly_stamp(value):
    """'2026-10-12T09:41:00Z' -> '12 Oct 2026, 09:41' in local time; anything else unchanged."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.astimezone().strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(value or "")


def rule_overview(row):
    """Readable summary rows for one rule; absent fields are skipped."""
    props = row.get("properties", {})
    join = lambda value: ", ".join(value) if isinstance(value, list) else value
    alert = None
    if "triggerOperator" in props or "triggerThreshold" in props:
        alert = ALERT_WORDS.get(props.get("triggerOperator", "GreaterThan"), str(props.get("triggerOperator"))) + \
            " " + str(props.get("triggerThreshold", 0))
    rows = [("Rule ID", row.get("name")), ("Type", row.get("kind")),
            ("Tactics", join(props.get("tactics"))), ("Techniques", join(props.get("techniques"))),
            ("Runs every", kit.duration_text(props["queryFrequency"]) if props.get("queryFrequency") else None),
            ("Looks back", kit.duration_text(props["queryPeriod"]) if props.get("queryPeriod") else None),
            ("Alert when", alert),
            ("Template", props.get("alertRuleTemplateName")),
            ("Last modified", friendly_stamp(props["lastModifiedUtc"]) if props.get("lastModifiedUtc") else None)]
    return [(key, str(value)) for key, value in rows if value not in (None, "", [])]


class RulesPage:
    def __init__(self, app, parent):
        self.app = app
        self.rows = []
        self.cache = []  # (values, haystack) per row, so filtering never re-derives them.
        self.collection = None
        self.loaded_session = None
        self.loaded_workspace = None
        tiles = ttk.Frame(parent); tiles.pack(fill="x", pady=(0, kit.space("lg")))
        self.tiles = {}
        for index, (key, caption, tone) in enumerate((("total", "Total rules", "info"), ("enabled", "Enabled", "success"),
                                                     ("disabled", "Disabled", "neutral"), ("unspecified", "Not specified", "warning"))):
            tiles.columnconfigure(index, weight=1, uniform="tiles")
            tile = kit.StatTile(tiles, caption, tone)
            tile.frame.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else px(12), 0))
            self.tiles[key] = tile.value

        card = kit.Card(parent, "Analytics rules", "Every rule configured in the selected workspace, including disabled rules.",
                        icon="rules")
        card.frame.pack(fill="both", expand=True, pady=(0, kit.space("lg")))
        # Not registered in app.controls: sync() decides when these are usable.
        self.refresh_button = ttk.Button(card.actions, text="Refresh", command=self.load, style="Primary.TButton")
        self.refresh_button.pack(side="right"); kit.tooltip(self.refresh_button, "Read the rules from Sentinel again (F5)")
        self.export_button = ttk.Button(card.actions, text="Export all…", command=self.export, style="Secondary.TButton")
        self.export_button.pack(side="right", padx=(0, px(8)))
        kit.tooltip(self.export_button, "Save every loaded rule as a ZIP of JSON and CSV files")
        body = card.body
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=(0, px(12)))
        self.search = tk.StringVar()
        self.filter = tk.StringVar(value="All states")
        kit.Segmented(bar, self.filter, STATES, compact=True).pack(side="right", padx=(px(12), 0))
        self.search_entry = ttk.Entry(bar, textvariable=self.search)
        self.search_entry.pack(side="left", fill="x", expand=True)
        app.controls.append((self.search_entry, "normal"))
        kit.placeholder(self.search_entry, self.search, "Search rules  (Ctrl+F)")
        self.notice = tk.StringVar()
        self.banner = kit.Banner(body, self.notice, "danger")
        self.summary = tk.StringVar(value="Select a workspace to load its rules.")
        self.summary_label = kit.wrap_to_parent(ttk.Label(body, textvariable=self.summary, style="Hint.TLabel",
                                                          wraplength=px(600)))
        self.summary_label.pack(fill="x", pady=(0, px(10)))
        frame, self.tree, self.empty = kit.table(body, (("name", "Rule name", 360, True), ("state", "State", 110, False),
                                                       ("severity", "Severity", 110, False), ("kind", "Type", 140, False)),
                                                 height=8)
        frame.pack(fill="both", expand=True)

        detail = kit.Card(parent, "Rule details", "Select a rule above to see its details here.", icon="document",
                          tone="neutral")
        detail.frame.pack(fill="both", expand=True)
        self.detail_card = detail
        self.pills = ttk.Frame(detail.actions, style="Card.TFrame"); self.pills.pack()
        self.description = tk.StringVar()
        kit.wrap_to_parent(ttk.Label(detail.body, textvariable=self.description, style="Body.TLabel",
                                     justify="left", wraplength=px(600))).pack(fill="x")
        self.overview = kit.KeyValues(detail.body, key_width=14); self.overview.pack(fill="x", pady=(px(4), 0))
        self.query_label = kit.section_label(detail.body, "Query")
        self.query_frame, self.query = kit.text_area(detail.body, 8, readonly=True, wrap="none")
        self.raw_label = kit.section_label(detail.body, "Full configuration")
        self.raw_label.pack_configure(pady=(kit.space("lg"), px(8)))
        frame, self.details = kit.text_area(detail.body, 10, readonly=True)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.tree.bind("<Double-1>", self.open_row)
        self.tree.bind("<Return>", self.open_row)
        self.search.trace_add("write", lambda *args: self.render())
        self.filter.trace_add("write", lambda *args: self.render())
        self.render()

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

    def open_row(self, event=None):
        """Double-click or Enter brings the details card into view."""
        if event is not None and event.type == tk.EventType.ButtonPress and self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.selection():
            self.app.scroll_into_view(self.detail_card.frame)

    def detail(self, row=None):
        for child in self.pills.winfo_children(): child.destroy()
        if row is None:
            self.detail_card.title_var.set("Rule details")
            self.detail_card.subtitle_var.set("Select a rule above to see its details here.")
            self.description.set(""); self.overview.set([])
            self.query_label.pack_forget(); self.query_frame.pack_forget()
            kit.set_text(self.query, ""); kit.set_text(self.details, "")
            return
        name, state, severity, kind = rule_values(row)
        props = row.get("properties", {})
        self.detail_card.title_var.set(name)
        self.detail_card.subtitle_var.set(kind + " rule")
        kit.Pill(self.pills, state, dict(Enabled="success", Disabled="neutral").get(state, "warning")).pack(side="right")
        if severity in SEVERITY_TONES:
            kit.Pill(self.pills, severity, SEVERITY_TONES[severity], dot=False).pack(side="right", padx=(0, px(6)))
        self.description.set(props.get("description", "").strip())
        self.overview.set(rule_overview(row), mono=MONO_KEYS)
        query = props.get("query")
        if query:
            self.query_label.pack(anchor="w", pady=(kit.space("lg"), px(8)), before=self.raw_label)
            self.query_frame.pack(fill="both", expand=True, after=self.query_label)
        else:
            self.query_label.pack_forget(); self.query_frame.pack_forget()
        kit.set_text(self.query, query or "")
        kit.set_text(self.details, json.dumps(row, indent=2, ensure_ascii=False))

    def reset(self):
        self.collection = None; self.loaded_session = None; self.loaded_workspace = None
        self.rows = []; self.cache = []; self.banner.pack_forget(); self.render()
        self.summary.set("Select a workspace to load its rules.")
        for value in self.tiles.values(): value.set("—")
        self.sync()

    def build_cache(self):
        self.cache = []
        for row in self.rows:
            values = rule_values(row)
            self.cache.append((values, (" ".join(map(str, values)) + " " + row.get("name", "")).casefold()))

    def render(self):
        if len(self.cache) != len(self.rows): self.build_cache()
        keep = self.tree.selection()[0] if self.tree.selection() else None
        self.tree.delete(*self.tree.get_children())
        needle = self.search.get().strip().casefold()
        state = self.filter.get()
        for index, (values, haystack) in enumerate(self.cache):
            if state not in ("All states", values[1]): continue
            if needle and needle not in haystack: continue
            self.tree.insert("", "end", iid=str(index), values=values, tags=("muted",) if values[1] != "Enabled" else ())
        if keep is not None and self.tree.exists(keep):
            self.tree.selection_set(keep)  # Keep the open details while the list is filtered.
        else:
            self.detail()
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
        if selection: self.detail(self.rows[int(selection[0])])

    def load(self):
        app = self.app
        if app.busy or not self.ready(): return
        session, workspace = app.session, dict(app.workspace)
        self.reset(); self.summary.set("Loading rules from " + workspace["workspace_name"] + "…")
        self.empty.show("Loading rules from " + workspace["workspace_name"] + "…")
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
            enabled = sum(values[1] == "Enabled" for values, _ in self.cache)
            disabled = sum(values[1] == "Disabled" for values, _ in self.cache)
            for key, value in (("total", len(self.rows)), ("enabled", enabled), ("disabled", disabled),
                               ("unspecified", len(self.rows) - enabled - disabled)):
                self.tiles[key].set(str(value))
            count = len(self.rows)
            text = f"{count} rule{'s' if count != 1 else ''} from {workspace['workspace_name']}"
            if result["status"] not in ("collected", "no_records"):
                reason = result.get("error", result["status"]).replace("_", " ")
                text = "Some rules couldn't be read. " + text + "."
                self.notice.set("Couldn't read all the analytics rules: " + reason)
                self.banner.pack(fill="x", pady=(0, px(12)), before=self.summary_label)
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
            messagebox.showinfo("Export all", "Load the rules for the current workspace before exporting.", parent=app.root)
            return
        complete = self.collection.get("status") in ("collected", "no_records")
        if not complete:
            if not self.rows:
                messagebox.showinfo("Export all", "The rules couldn't be read. Select Refresh before exporting.", parent=app.root)
                return
            if not messagebox.askyesno("Export all",
                    "Some rules couldn't be read. Export the available rules and mark the export as incomplete?",
                    parent=app.root): return
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
