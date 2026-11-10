"""Offline checks for the shared desktop kit: theme tokens, tables, search boxes,
dialogs, the stepper and scroll areas."""
import re
import tkinter as tk
import unittest
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import MagicMock, patch

from sentinel_app import ui_kit as kit
from sentinel_app.desktop_theme import (CHECK_INDICATOR, CHECKBOX, COLORS, FONTS, ICONS, checkbox_image,
                                        configure_theme, px)


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def contrast(foreground, background):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return .2126 * r + .7152 * g + .0722 * b
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + .05) / (low + .05)


class KitTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); configure_theme(self.root); self.root.geometry("900x700+40+40")
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()

    def rows(self, tree):
        return [tree.set(item, "name") for item in tree.get_children()]


class ThemeTests(KitTestCase):
    def test_text_greys_meet_aa_contrast(self):
        for background in ("card", "canvas", "raised", "stripe"):
            self.assertGreaterEqual(contrast(COLORS["muted"], COLORS[background]), 4.5, background)

    def test_selection_is_visible_and_quieter_without_focus(self):
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup("Treeview", "background", ["selected", "focus"]), COLORS["selection"])
        self.assertEqual(style.lookup("Treeview", "background", ["selected"]), COLORS["selection_inactive"])
        self.assertNotIn("accent_disabled", COLORS)

    def test_disabled_buttons_are_quiet(self):
        style = ttk.Style(self.root)
        for name in ("Ghost.TButton", "Disclosure.TButton", "Small.Ghost.TButton"):
            for option in ("background", "bordercolor", "lightcolor", "darkcolor"):
                self.assertEqual(style.lookup(name, option, ["disabled"]), COLORS["card"], (name, option))
            self.assertEqual(style.lookup(name, "foreground", ["disabled"]), COLORS["disabled"])
        for name in ("Primary.TButton", "Danger.TButton", "Secondary.TButton"):
            self.assertEqual(style.lookup(name, "background", ["disabled"]), COLORS["raised"], name)
            self.assertEqual(style.lookup(name, "bordercolor", ["disabled"]), COLORS["line"], name)
            self.assertEqual(style.lookup(name, "foreground", ["disabled"]), COLORS["disabled"], name)
        self.assertEqual(style.lookup("Danger.TButton", "background"), COLORS["danger"])

    def test_rows_fonts_and_search_style(self):
        style = ttk.Style(self.root)
        self.assertLessEqual(int(style.lookup("Treeview", "rowheight")), px(40))
        self.assertEqual(ICONS["search"], "")
        self.assertEqual(str(style.lookup("Search.TEntry", "padding")).split()[0], str(px(32)))
        for name in ("strong", "title", "page", "stat"):
            family, size, weight = FONTS[name]
            self.assertEqual(weight, "normal" if "Semib" in family else "bold", name)


class TableTests(KitTestCase):
    def make(self, **options):
        frame, tree, empty = kit.table(self.root, (("name", "Name", 200), ("severity", "Severity", 100)), **options)
        frame.pack(fill="both", expand=True)
        for index, (name, severity) in enumerate((("beta", "Low"), ("Alpha", "High"), ("gamma", ""),
                                                  ("delta", "Informational"), ("epsilon", "Medium"))):
            tree.insert("", "end", iid=str(index), values=(name, severity))
        return frame, tree

    def stripes(self, tree):
        return ["stripe" in tree.item(item, "tags") for item in tree.get_children()]

    def test_rows_are_counted_and_restriped(self):
        _, tree = self.make()
        self.assertEqual(self.stripes(tree), [False, True, False, True, False])
        tree.delete("0", "1")
        self.assertEqual(tree._rows, 3)
        tree.insert("", "end", iid="new", values=("zeta", "Low"), tags=("muted",))
        self.assertEqual(tree.item("new", "tags"), ("muted", "stripe"))
        tree.move("new", "", 0); tree.restripe()
        self.assertEqual(self.stripes(tree), [False, True, False, True])
        self.assertIn("muted", tree.item("new", "tags"))

    def test_scrollbars_show_only_while_the_rows_overflow(self):
        bar = ttk.Scrollbar(self.root); update = kit.autohide(bar, bar.pack)
        update("0.0", "0.5"); self.assertEqual(bar.winfo_manager(), "pack")
        update("0.0", "1.0"); self.assertEqual(bar.winfo_manager(), "")
        # A table squeezed below one row reports 0-0 for a moment: that must not show the bar
        # (doing so narrowed a table, which hid a column, which let the table grow: a loop).
        update("0.0", "0.0"); self.assertEqual(bar.winfo_manager(), "")
        update("0.2", "0.6"); update("0.0", "0.0"); self.assertEqual(bar.winfo_manager(), "pack")

    def test_heading_sorts_naturally_and_shows_an_arrow(self):
        _, tree = self.make(sort=True)
        self.assertIsNone(tree.sort_state)
        command = tree.heading("severity", "command")
        tree.tk.call(command)
        self.assertEqual(self.rows(tree), ["Alpha", "epsilon", "beta", "delta", "gamma"])
        self.assertEqual(tree.sort_state, ("severity", False))
        self.assertEqual(tree.heading("severity", "text"), "Severity ▲")
        self.assertEqual(self.stripes(tree), [False, True, False, True, False])
        tree.tk.call(command)
        # Descending, with the blank severity still last.
        self.assertEqual(self.rows(tree), ["delta", "beta", "epsilon", "Alpha", "gamma"])
        self.assertEqual(tree.heading("severity", "text"), "Severity ▼")
        tree.tk.call(tree.heading("name", "command"))
        self.assertEqual(self.rows(tree), ["Alpha", "beta", "delta", "epsilon", "gamma"])
        self.assertEqual(tree.heading("severity", "text"), "Severity")

    def test_sort_callback_and_headings_keep_the_arrow(self):
        calls = []
        _, tree = self.make(sort=lambda key, descending: calls.append((key, descending)))
        tree.toggle_sort("name"); tree.toggle_sort("name"); tree.toggle_sort("severity")
        self.assertEqual(calls, [("name", False), ("name", True), ("severity", False)])
        kit.set_headings(tree, {"name": "Rule", "severity": "Level"})
        self.assertEqual((tree.heading("name", "text"), tree.heading("severity", "text")), ("Rule", "Level ▲"))
        self.assertEqual(kit.SEVERITY_RANK["High"], 0)
        self.assertLess(kit.sort_value("9"), kit.sort_value("10"))

    def test_sort_items_reapplies_the_current_order(self):
        _, tree = self.make(sort=True)
        tree.toggle_sort("name")
        tree.insert("", "end", iid="late", values=("aardvark", "Low"))
        kit.sort_items(tree)
        self.assertEqual(self.rows(tree)[0], "aardvark")

    def test_focus_gives_a_focus_row_and_a_ring(self):
        frame, tree = self.make()
        tree.event_generate("<FocusIn>")
        self.assertEqual(tree.focus(), "0"); self.assertEqual(tree.selection(), ("0",))
        self.assertEqual(frame.cget("highlightbackground"), COLORS["accent"])
        tree.event_generate("<FocusOut>")
        self.assertEqual(frame.cget("highlightbackground"), COLORS["line"])
        # An existing selection is kept and becomes the focus row.
        tree.focus(""); tree.selection_set("3"); tree.focus("")
        tree.event_generate("<FocusIn>")
        self.assertEqual((tree.focus(), tree.selection()), ("3", ("3",)))

    def test_heading_press_does_not_select_a_row(self):
        frame, tree = self.make(sort=True)
        other = ttk.Entry(self.root); other.pack()
        selected = []
        tree.bind("<<TreeviewSelect>>", lambda event: selected.append(tree.selection()), add="+")
        self.root.deiconify(); other.focus_force(); self.root.update()
        x, y = px(20), px(8)
        self.assertEqual(tree.identify_region(x, y), "heading")
        tree.event_generate("<ButtonPress-1>", x=x, y=y)
        tree.event_generate("<FocusIn>")  # As the class binding's 'focus' queues it.
        self.root.update()
        tree.event_generate("<ButtonRelease-1>", x=x, y=y); self.root.update()
        self.assertEqual((tree.selection(), selected), ((), []))
        self.assertEqual(tree.sort_state, ("name", False))
        self.assertEqual(frame.cget("highlightbackground"), COLORS["accent"])
        # Keyboard focus afterwards still picks a focus row.
        other.focus_force(); self.root.update(); tree.event_generate("<FocusIn>")
        self.assertEqual(tree.selection(), (tree.get_children()[0],))

    def test_copy_and_row_menu(self):
        _, tree = self.make(selectmode="extended",
                            menu=lambda item: [("Open " + item, lambda: None), None, ("Delete", lambda: None, False)])
        tree.selection_set("0", "2")
        kit.copy_rows(tree)
        self.assertEqual(self.root.clipboard_get(), "beta\tLow\ngamma\t")
        menu = tree.build_menu("2")
        self.assertEqual(menu.entrycget(0, "label"), "Copy")
        self.assertEqual(menu.type(1), "separator")
        self.assertEqual(menu.entrycget(2, "label"), "Open 2")
        self.assertEqual(menu.type(3), "separator")
        self.assertEqual((menu.entrycget(4, "label"), str(menu.entrycget(4, "state"))), ("Delete", "disabled"))

    def test_marker_and_check_columns_use_cached_images(self):
        for option in ("marker", "check"):
            frame, tree, _ = kit.table(self.root, (("name", "Name", 200),), **{option: True})
            self.assertIn("tree", [str(part) for part in tree.tk.splitlist(tree.cget("show"))])
            self.assertEqual(int(tree.column("#0", "width")), px(28))
            frame.destroy()
        dot = kit.dot("danger", self.root)
        self.assertIs(dot, kit.dot("danger", self.root))
        self.assertIsNot(dot, kit.dot("warning", self.root))
        self.assertEqual((dot.width(), dot.height()), (px(10), px(10)))
        self.assertTrue(dot.transparency_get(0, 0))
        self.assertFalse(dot.transparency_get(px(5), px(5)))
        on, off = kit.checkbox("on", self.root), kit.checkbox("off", self.root)
        self.assertEqual(on.width(), px(14))
        self.assertIs(off, kit.checkbox("off", self.root))
        middle = px(7)
        self.assertEqual("#%02X%02X%02X" % on.get(1, middle), COLORS["accent"])
        self.assertEqual("#%02X%02X%02X" % off.get(middle, middle), COLORS["card"])
        self.assertIs(on, kit.checkbox("on", self.root))

    def test_default_stretch_and_minimum_widths(self):
        frame, tree, _ = kit.table(self.root, (("name", "Name", 200), ("state", "State", 100), ("batch", "Batch", 60, False),
                                               ("note", "Note", 120, True)))
        stretch = lambda key: tree.tk.getboolean(tree.column(key, "stretch"))
        # The main text column takes the spare width; narrow columns keep theirs unless asked.
        self.assertEqual([stretch(key) for key in ("name", "state", "batch", "note")], [True, False, False, True])
        self.assertEqual(int(tree.column("batch", "minwidth")), px(60))
        self.assertEqual(int(tree.column("name", "minwidth")), px(70))


class EllipsisTests(KitTestCase):
    LONG = "Anomalous Azure resource deployment across every subscription"

    def make(self, width=150, **options):
        frame, tree, _ = kit.table(self.root, (("name", "Name", width), ("state", "State", 90)), **options)
        frame.place(x=0, y=0, width=px(width + 90), height=px(300))
        return tree

    def shown(self, tree, item):
        return tree.tk.splitlist(tree.tk.call(tree._w, "item", item, "-values"))  # What Tk draws.

    def room(self, tree, key):
        return int(tree.column(key, "width")) - px(tree.PAD)

    def test_long_cells_show_an_ellipsis_but_keep_their_values(self):
        tree = self.make()
        tree.insert("", "end", iid="a", values=(self.LONG, "Enabled"))
        tree.insert("", "end", iid="b", values=("Brute force", 3))
        name = self.shown(tree, "a")[0]
        self.assertTrue(name.endswith("…")); self.assertTrue(self.LONG.startswith(name[:-1]))
        self.assertLessEqual(kit.font(tree, "body").measure(name), self.room(tree, "name"))
        self.assertEqual(self.shown(tree, "b"), ("Brute force", "3"))
        # Every reader gets the full text, as Tk would store it.
        self.assertEqual(tree.item("a", "values"), (self.LONG, "Enabled"))
        self.assertEqual(tree.item("a")["values"], (self.LONG, "Enabled"))
        self.assertEqual((tree.set("a", "name"), tree.set("b", "state")), (self.LONG, "3"))
        self.assertEqual(tree.set("a"), {"name": self.LONG, "state": "Enabled"})
        self.assertEqual(tree.full_values("a"), (self.LONG, "Enabled"))
        self.assertEqual((tree.shortened("a", "name"), tree.shortened("b", "name"), tree.shortened("a", "state")),
                         (self.LONG, "", ""))
        # Writes through set() and item() are shortened and kept in full too.
        tree.set("b", "name", self.LONG + " (copy)")
        self.assertEqual(tree.set("b", "name"), self.LONG + " (copy)"); self.assertIn("…", self.shown(tree, "b")[0])
        tree.item("a", values=("Short", "Disabled"))
        self.assertEqual((self.shown(tree, "a"), tree.shortened("a", "name")), (("Short", "Disabled"), ""))
        tree.insert("", "end", iid="c")
        self.assertEqual((tree.item("c", "values"), tree.set("c", "name"), tree.full_values("c")), ("", "", ()))

    def test_copy_and_sort_use_the_full_values(self):
        tree = self.make(sort=True, selectmode="extended")
        # Both show as the same shortened text; only the end tells them apart.
        tree.insert("", "end", iid="zeta", values=(self.LONG + " zeta", "Enabled"))
        tree.insert("", "end", iid="alpha", values=(self.LONG + " alpha", "Enabled"))
        self.assertEqual(self.shown(tree, "zeta"), self.shown(tree, "alpha"))
        tree.toggle_sort("name")
        self.assertEqual(tree.get_children(), ("alpha", "zeta"))
        tree.selection_set("zeta"); kit.copy_rows(tree)
        self.assertEqual(self.root.clipboard_get(), self.LONG + " zeta\tEnabled")

    def test_a_width_change_shortens_the_cells_again(self):
        tree = self.make()
        tree.insert("", "end", iid="a", values=(self.LONG, "Enabled"))
        self.root.update()
        tree.column("name", width=px(700)); tree.refit_soon.flush()
        self.assertEqual((self.shown(tree, "a")[0], tree.shortened("a", "name")), (self.LONG, ""))
        tree.column("name", width=px(110)); tree.refit_soon.flush()
        narrow = self.shown(tree, "a")[0]
        self.assertTrue(narrow.endswith("…"))
        self.assertLessEqual(kit.font(tree, "body").measure(narrow), self.room(tree, "name"))
        # A column edge dragged with the mouse: Tk sets the width itself, the release re-fits.
        tree.tk.call(tree._w, "column", "name", "-width", px(260))
        tree.event_generate("<ButtonRelease-1>", x=1, y=1)
        self.assertTrue(self.root.tk.call("after", "info"))  # Debounced on the root, like every kit timer.
        tree.refit_soon.flush()
        wider = self.shown(tree, "a")[0]
        self.assertGreater(len(wider), len(narrow)); self.assertTrue(self.LONG.startswith(wider.rstrip("…")))

    def test_two_thousand_rows_stay_quick(self):
        import time
        tree = self.make()
        start = time.perf_counter()
        for index in range(2000):
            tree.insert("", "end", iid=str(index), values=(self.LONG + " " + str(index), "Enabled"))
        tree.column("name", width=px(400)); tree.refit_soon.flush()
        kit.sort_items(tree, "name", True)
        self.assertLess(time.perf_counter() - start, 1.5)  # About 0.05s on a laptop.
        self.assertEqual(tree.set(tree.get_children()[0], "name"), self.LONG + " 999")

    def test_hovering_a_shortened_cell_shows_it_in_full(self):
        tree = self.make()
        tree.insert("", "end", iid="a", values=(self.LONG, "Enabled"))
        self.root.deiconify(); self.root.update()
        x, y, width, height = tree.bbox("a", "name")
        tree.event_generate("<Motion>", x=x + 5, y=y + height // 2)
        deadline = self.root.tk.call("clock", "milliseconds") + 3000
        tips = lambda: [w for w in tree.winfo_children() if isinstance(w, tk.Toplevel)]
        while not tips() and self.root.tk.call("clock", "milliseconds") < deadline: self.root.update()
        self.assertEqual([w.winfo_children()[0].cget("text") for w in tips()], [self.LONG])
        tree.event_generate("<Leave>"); self.root.update()
        self.assertEqual(tips(), [])

    def test_a_tip_never_outlives_its_row(self):
        tree = self.make()
        errors = []
        self.root.report_callback_exception = lambda *exc: errors.append(exc[1])
        tips = lambda: [w for w in tree.winfo_children() if isinstance(w, tk.Toplevel)]
        fill = lambda count: [tree.insert("", "end", iid=str(i), values=(self.LONG + " %d" % i, "On"))
                              for i in range(count)]
        fill(10); self.root.deiconify(); self.root.update()
        def hover(item):
            before = set(self.root.tk.call("after", "info"))
            x, y, width, height = tree.bbox(item, "name")
            tree.event_generate("<Motion>", x=x + 5, y=y + height // 2)
            return set(self.root.tk.call("after", "info")) - before  # The tip's timer, on the root.
        def flush(timers):
            for timer in timers & set(self.root.tk.call("after", "info")):  # Run a pending tip now.
                script = self.root.tk.splitlist(self.root.tk.call("after", "info", timer))[0]
                self.root.tk.call("after", "cancel", timer); self.root.tk.eval(script)
            self.root.update()
        # The pointer rests on row 5 while a search redraws the list with three rows.
        timers = hover("5")
        tree.delete(*tree.get_children()); fill(3)
        flush(timers)
        self.assertEqual((errors, tips()), ([], []))
        # Even rows taken away behind the kit's back don't break the tip when its time comes.
        timers = hover("2")
        ttk.Treeview.delete(tree, "2"); flush(timers)
        self.assertEqual((errors, tips()), ([], []))
        tree.delete(*tree.get_children()); fill(3)
        # A tip already open closes when the rows are redrawn, or when its row's text changes.
        timers = hover("1"); flush(timers)
        self.assertEqual(len(tips()), 1)
        tree.delete(*tree.get_children()); self.root.update()
        self.assertEqual(tips(), [])
        fill(3); timers = hover("2"); flush(timers)
        tree.item("0", values=("Other row", "Off")); self.root.update()
        self.assertEqual(len(tips()), 1)
        tree.set("2", "name", self.LONG + " renamed"); self.root.update()
        self.assertEqual((errors, tips()), ([], []))


class KeyValueTests(KitTestCase):
    PATH = "rules/sentinel/identity-and-access/brute-force-against-entra-id-accounts.yml"

    def test_ellipsize_middle_keeps_both_ends(self):
        mono = kit.font(self.root, "mono")
        self.assertEqual(kit.ellipsize_middle("short", mono, px(300)), "short")
        text = kit.ellipsize_middle(self.PATH, mono, px(200))
        self.assertLessEqual(mono.measure(text), px(200))
        head, tail = text.split("…")
        self.assertTrue(self.PATH.startswith(head) and self.PATH.endswith(tail) and head and tail)
        # A path whose file name fits keeps it whole, after as much of the start as fits.
        path = "rules/sentinel/identity-and-access/brute-force.yml"
        text = kit.ellipsize_middle(path, mono, mono.measure(path) - 1)
        self.assertTrue(text.endswith("…/brute-force.yml") and path.startswith(text.split("…")[0]))
        self.assertLessEqual(mono.measure(text), mono.measure(path) - 1)
        self.assertGreater(len(text), len("rules/…/brute-force.yml"))

    def test_mono_values_shorten_in_the_middle_instead_of_wrapping(self):
        facts = kit.KeyValues(self.root, key_width=12)
        facts.place(x=0, y=0, width=px(360))
        prose = "Detects anomalous deployments across sign-in and audit telemetry for the analyst."
        facts.set([("File", self.PATH), ("Description", prose), ("Used by", "3: acme-primary, bluestone-primary")],
                  mono={"File", "Used by"})
        self.root.update()
        path, description, used = facts.values
        text = path.cget("text")
        self.assertIn("…", text); self.assertTrue(text.startswith("rules/") and text.endswith(".yml"))
        self.assertLessEqual(kit.font(facts, "mono").measure(text), path.winfo_width())
        self.assertEqual(int(str(path.cget("wraplength")) or 0), 0)
        self.assertEqual((path.full_text, path.tooltip_text), (self.PATH, self.PATH))
        # Prose, and a mono list of names, still wrap whole; every value keeps its full text.
        for label, value in ((description, prose), (used, "3: acme-primary, bluestone-primary")):
            self.assertEqual((label.cget("text"), label.full_text), (value, value))
            self.assertGreater(int(str(label.cget("wraplength"))), 0)
        facts.place_configure(width=px(900)); self.root.update()
        self.assertEqual((path.cget("text"), path.tooltip_text), (self.PATH, ""))

    def test_stacked_values_use_the_whole_width(self):
        facts = kit.KeyValues(self.root, surface="Raised", stacked=True)
        facts.place(x=0, y=0, width=px(200))
        guid = "11111111-1111-4111-8111-111111111111"
        facts.set([("Tenant ID", guid)]); self.root.update()
        label = facts.values[0]
        self.assertIn("…", label.cget("text")); self.assertEqual(label.full_text, guid)
        self.assertLessEqual(kit.font(facts, "mono").measure(label.cget("text")), px(200))


class CheckboxTests(KitTestCase):
    STATES = {(): ("off", False), ("selected",): ("on", False), ("active",): ("hover", False),
              ("active", "selected"): ("on_hover", False), ("focus",): ("off", True), ("focus", "selected"): ("on", True),
              ("active", "focus"): ("hover", True), ("pressed",): ("pressed", False),
              ("pressed", "selected"): ("on_pressed", False), ("focus", "pressed", "selected"): ("on_pressed", True),
              ("disabled",): ("disabled", False), ("disabled", "selected"): ("on_disabled", False),
              ("disabled", "focus", "selected"): ("on_disabled", False)}

    def test_every_checkbox_uses_the_drawn_indicator(self):
        style = ttk.Style(self.root)
        self.assertIn("Kit.Checkbutton.indicator", style.element_names())
        def names(layout):
            for name, options in layout:
                yield name; yield from names(options.get("children", ()))
        parts = list(names(style.layout("TCheckbutton")))
        self.assertIn("Kit.Checkbutton.indicator", parts)
        self.assertNotIn("Checkbutton.indicator", parts); self.assertNotIn("Checkbutton.focus", parts)
        box = ttk.Checkbutton(self.root, text="Allow missing MITRE details"); box.pack(); self.root.update()
        self.assertGreaterEqual(box.winfo_reqheight(), px(16) + 2 * px(2))
        configure_theme(self.root)  # Theming a root again keeps the element it made.

    def test_each_state_has_its_own_image(self):
        # Tk picks the first entry whose states all hold; CHECK_INDICATOR is that list.
        pick = lambda states: next(((state, ring) for spec, state, ring in CHECK_INDICATOR if set(spec) <= set(states)),
                                   ("off", False))
        for states, expected in self.STATES.items():
            self.assertEqual(pick(states), expected, states)
        size, ring = px(16), px(2)
        image = lambda state, focus=False: checkbox_image(self.root, state, size, ring, focus)
        color = lambda img, x, y: "#%02X%02X%02X" % img.get(x, y)
        inside, middle = ring + px(3), ring + size // 2
        ticked = lambda img: any(color(img, x, y) == "#FFFFFF" for x in range(img.width()) for y in range(img.height())
                                 if not img.transparency_get(x, y))
        # An empty box's edge is a control boundary: 3:1 against every surface a checkbox sits on.
        for surface in ("card", "stripe", "raised", "canvas", "hover"):
            self.assertGreaterEqual(contrast(COLORS[CHECKBOX["off"][1]], COLORS[surface]), 3, surface)
        for state, (fill, border, tick) in CHECKBOX.items():
            self.assertEqual(color(image(state), inside, inside), COLORS[fill], state)
            self.assertEqual(color(image(state), ring, middle), COLORS[border], state)
            if tick: self.assertTrue(ticked(image(state)), state)  # Their fills aren't white; only the tick is.
        self.assertIs(image("on"), image("on"))
        for state in ("off", "on"):
            plain, focused = image(state), image(state, True)
            self.assertIsNot(plain, focused)
            self.assertTrue(plain.transparency_get(0, middle))
            self.assertFalse(focused.transparency_get(0, middle))
            self.assertEqual(color(focused, 0, middle), COLORS["accent"])
            self.assertTrue(focused.transparency_get(0, 0))


class SearchTests(KitTestCase):
    def test_placeholder_stays_while_focused(self):
        var = tk.StringVar(); entry = ttk.Entry(self.root, textvariable=var); entry.pack()
        hint = kit.placeholder(entry, var, "Search rules")
        self.root.update()
        entry.event_generate("<FocusIn>"); self.root.update()
        self.assertEqual(hint.winfo_manager(), "place")
        var.set("x"); self.assertEqual(hint.winfo_manager(), "")
        var.set(""); self.assertEqual(hint.winfo_manager(), "place")
        self.assertIsNone(entry.search_glyph)

    def test_search_variant_has_a_glyph(self):
        var = tk.StringVar(); entry = ttk.Entry(self.root, textvariable=var); entry.pack()
        hint = kit.placeholder(entry, var, "Search", search=True)
        if not FONTS.get("icon_small"): self.skipTest("No icon font")
        self.assertEqual(str(entry.cget("style")), "Search.TEntry")
        self.assertEqual(entry.search_glyph.cget("text"), ICONS["search"])
        self.assertEqual(int(hint.place_info()["x"]), px(36))

    def test_link_search_keys(self):
        var = tk.StringVar(); entry = ttk.Entry(self.root, textvariable=var); entry.pack()
        frame, tree, _ = kit.table(self.root, (("name", "Name", 200),)); frame.pack()
        closed = []
        self.root.bind("<Escape>", lambda event: closed.append(True))
        kit.link_search(entry, tree)
        for name in ("a", "b"): tree.insert("", "end", iid=name, values=(name,))
        var.set("abc")
        self.root.deiconify(); entry.focus_force(); self.root.update()
        entry.event_generate("<Escape>"); self.root.update()
        self.assertEqual(var.get(), ""); self.assertFalse(closed)
        entry.event_generate("<Escape>"); self.root.update()
        self.assertTrue(closed)  # An empty box lets Esc through to the window.
        entry.event_generate("<Down>"); self.root.update()
        self.assertEqual((tree.focus(), tree.selection()), ("a", ("a",)))
        self.assertIs(self.root.focus_get(), tree)

    def test_debounce_runs_once_after_a_pause(self):
        calls = []
        later = kit.debounce(self.root, 150, lambda: calls.append(1))
        later(); later("trace", "args", "write"); later()
        self.assertEqual(len(self.root.tk.call("after", "info")), 1)
        later.flush(); later.flush()
        self.assertEqual(calls, [1])
        later(); later.cancel(); self.root.after(200, lambda: calls.append("tick"))
        while "tick" not in calls: self.root.update()
        self.assertEqual(calls, [1, "tick"])

    def test_segmented_set_label(self):
        var = tk.StringVar(value="all")
        control = kit.Segmented(self.root, var, (("all", "All"), ("off", "Disabled")))
        control.set_label("off", "Disabled (3)")
        self.assertEqual(control.items["off"].cget("text"), "Disabled (3)")


class EditingTests(KitTestCase):
    def test_text_area_tab_moves_focus_or_indents(self):
        frame, prose = kit.text_area(self.root, 3, font="body"); frame.pack()
        frame, code = kit.text_area(self.root, 3); frame.pack()
        after = ttk.Entry(self.root); after.pack()
        self.root.deiconify(); prose.focus_force(); self.root.update()
        prose.event_generate("<Tab>"); self.root.update()
        self.assertIs(self.root.focus_get(), code)
        self.assertEqual(prose.get("1.0", "end-1c"), "")
        code.event_generate("<Tab>"); self.root.update()
        self.assertEqual(code.get("1.0", "end-1c"), "    ")
        self.assertIs(self.root.focus_get(), code)
        code.event_generate("<Control-Tab>"); self.root.update()
        self.assertIs(self.root.focus_get(), after)

    def test_edit_menu_matches_the_field(self):
        menu = kit.install_edit_menu(self.root)
        self.assertTrue(self.root.bind_class("TEntry", "<Button-3>"))
        states = lambda: [str(menu.entrycget(index, "state")) for index in (0, 1, 2)]
        entry = ttk.Entry(self.root); entry.insert(0, "tenant"); entry.pack()
        kit._prepare_edit_menu(menu, entry)
        self.assertEqual(states(), ["disabled", "disabled", "normal"])
        entry.selection_range(0, "end"); kit._prepare_edit_menu(menu, entry)
        self.assertEqual(states(), ["normal", "normal", "normal"])
        combo = ttk.Combobox(self.root, values=("a",), state="readonly"); combo.pack()
        kit._prepare_edit_menu(menu, combo)
        self.assertEqual(states(), ["disabled", "disabled", "disabled"])
        frame, text = kit.text_area(self.root, 3, readonly=True); frame.pack()
        kit.set_text(text, "KQL"); text.tag_add("sel", "1.0", "end")
        kit._prepare_edit_menu(menu, text)
        self.assertEqual(states(), ["disabled", "normal", "disabled"])

    def test_right_click_on_a_hint_opens_the_fields_menu(self):
        menu = kit.install_edit_menu(self.root)
        shown = []
        original = kit._show_menu
        kit._show_menu = lambda popup, event: shown.append((popup.target, event.widget))
        self.addCleanup(setattr, kit, "_show_menu", original)
        var = tk.StringVar(); entry = ttk.Entry(self.root, textvariable=var, width=40); entry.pack()
        hint = kit.placeholder(entry, var, "Paste a tenant ID", search=True)
        frame, text = kit.text_area(self.root, 3, placeholder="Why is this changing?"); frame.pack()
        self.root.update()
        labels = [(hint, entry), (text.placeholder_label, text)]
        if entry.search_glyph is not None: labels.append((entry.search_glyph, entry))
        for label, target in labels:
            shown.clear()
            label.event_generate("<Button-3>", x=2, y=2, rootx=label.winfo_rootx() + 2, rooty=label.winfo_rooty() + 2)
            self.root.update()
            self.assertEqual(shown, [(target, target)], label)
            self.assertIs(menu.target, target)


class DialogTests(KitTestCase):
    def press_later(self, text=None, close=False):
        def press():
            window = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            if close:
                window.tk.call(window.protocol("WM_DELETE_WINDOW")); return
            next(w for w in descendants(window) if isinstance(w, ttk.Button) and w.cget("text") == text).invoke()
        self.root.after(150, press)

    def test_confirm_and_alert_results(self):
        self.root.update()
        self.press_later("Remove"); self.assertTrue(kit.confirm(self.root, "Remove client", "Remove it?", ok="Remove"))
        self.press_later("Cancel"); self.assertFalse(kit.confirm(self.root, "Remove client", "Remove it?"))
        self.press_later(close=True); self.assertFalse(kit.confirm(self.root, "Remove client", "Remove it?"))
        self.press_later("OK"); self.assertIsNone(kit.alert(self.root, "Couldn't save", "Disk full.", details="E1"))

    def test_message_dialog_parts(self):
        dialog = kit.MessageDialog(self.root, "Remove client", "Remove it?", details="trace", ok="Remove",
                                   cancel="Cancel", destructive=True)
        self.addCleanup(lambda: dialog.window.winfo_exists() and dialog.window.destroy())
        self.assertEqual(str(dialog.ok_button.cget("style")), "Danger.TButton")
        self.assertEqual(dialog.text.get("1.0", "end-1c"), "trace")
        dialog.copy()
        self.assertEqual(self.root.clipboard_get(), "trace")

    def test_promote_moves_the_enter_target(self):
        window = tk.Toplevel(self.root)
        pressed = []
        entry = ttk.Entry(window); entry.pack()
        _, (check, submit, close) = kit.action_row(window, primary=("Check", lambda: pressed.append("check")),
                                                   secondary=(("Submit", lambda: pressed.append("submit")),),
                                                   close=("Close", window.destroy))
        _.pack()
        kit.modal(window, self.root, window.destroy, primary=check)
        kit.promote(window, submit, check)
        self.assertEqual((str(submit.cget("style")), str(check.cget("style"))), ("Primary.TButton", "Secondary.TButton"))
        window.update(); entry.focus_force(); window.update()
        entry.event_generate("<Return>"); window.update()
        self.assertEqual(pressed, ["submit"])
        kit.promote(window, None, submit, check)
        self.assertEqual(str(submit.cget("style")), "Secondary.TButton")
        entry.event_generate("<Return>"); window.update()
        self.assertEqual(pressed, ["submit"])  # Nothing leads, so Enter presses nothing.
        window.destroy()

    def test_modal_without_a_primary_leaves_enter_alone(self):
        window = tk.Toplevel(self.root)
        pressed = []
        entry = ttk.Entry(window); entry.pack()
        _, (apply, close) = kit.action_row(window, secondary=(("Apply", lambda: pressed.append("apply")),),
                                           close=("Close", lambda: pressed.append("close")))
        _.pack()
        kit.modal(window, self.root, window.destroy, focus=apply)
        self.assertEqual(window.bind("<Return>"), "")
        window.update(); apply.focus_force(); window.update()
        apply.event_generate("<Return>"); window.update()
        self.assertEqual(pressed, [])  # As before: Enter on a focused button does nothing here.
        kit.promote(window, apply, close)
        entry.focus_force(); window.update()
        entry.event_generate("<Return>"); window.update()
        close.focus_force(); window.update()
        close.event_generate("<Return>"); window.update()
        self.assertEqual(pressed, ["apply", "close"])
        window.destroy()

    def test_action_row_aux_buttons_sit_left(self):
        frame, buttons = kit.action_row(self.root, primary=("Submit", None), close=("Close", None),
                                        aux=(("Open in GitHub", None), ("Reload", None)))
        self.assertEqual([b.cget("text") for b in buttons], ["Submit", "Close", "Open in GitHub", "Reload"])
        self.assertEqual(str(buttons[2].cget("style")), "Ghost.TButton")
        self.assertEqual(buttons[2].pack_info()["side"], "left")

    def test_form_dialog_field_error_and_banner(self):
        errors = [kit.FieldError("name", "Enter a client name."),
                  ValueError("The tenant must be a Directory (tenant) ID or a verified domain such as "
                             "contoso.onmicrosoft.com, not a portal address. Check it and try again.")]
        def validate(values):
            raise errors.pop(0)
        self.root.update()
        dialog = kit.FormDialog(self.root, "New client", "Saved on this PC only.",
                                [("name", "Client name", "", "Shown in the client list."),
                                 ("tenant", "Tenant", "portal.azure.com", None)], validate=validate)
        self.addCleanup(lambda: dialog.window.winfo_exists() and dialog.window.destroy())
        self.root.update()
        dialog.submit()
        self.assertIsNone(dialog.result)
        self.assertEqual(str(dialog.entries["name"].cget("style")), "Error.TEntry")
        self.assertEqual((dialog.hints["name"].cget("text"), str(dialog.hints["name"].cget("style"))),
                         ("Enter a client name.", "ErrorHint.TLabel"))
        self.assertFalse(dialog.error_banner.winfo_manager())
        dialog.vars["name"].set("Fabrikam")
        self.assertEqual(str(dialog.entries["name"].cget("style")), "TEntry")
        self.assertEqual(dialog.hints["name"].cget("text"), "Shown in the client list.")
        before = dialog.window.winfo_height()
        dialog.submit(); dialog.window.update()
        self.assertIn("portal address", dialog.error.get())
        self.assertGreater(dialog.window.winfo_height(), before)
        for button in (dialog.submit_button, dialog.cancel_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                 dialog.window.winfo_rooty() + dialog.window.winfo_height())


class StepperTests(KitTestCase):
    def test_status_marks_and_notes(self):
        notebook = ttk.Notebook(self.root)
        for _ in range(3): notebook.add(ttk.Frame(notebook))
        stepper = kit.Stepper(self.root, notebook, [("Basics", "Name"), ("Query", "KQL"), ("Review", "File")])
        stepper.pack(); self.root.update()
        size = [stepper.rows[1][1].winfo_reqwidth(), stepper.rows[1][1].winfo_reqheight()]
        stepper.set_status(1, "done"); stepper.set_status(2, "error", "2 issues"); stepper.set_status(0, "done")
        _, marker, _, detail = stepper.rows[1]
        self.assertEqual(marker.cget("bg"), COLORS["success_soft"])
        self.assertEqual(marker.cget("text"), ICONS["check"] if FONTS.get("icon_small") else "✓")
        self.assertEqual([marker.winfo_reqwidth(), marker.winfo_reqheight()], size)
        _, marker, _, detail = stepper.rows[2]
        self.assertEqual((marker.cget("text"), marker.cget("bg")), ("!", COLORS["danger_soft"]))
        self.assertEqual((detail.cget("text"), detail.cget("fg")), ("2 issues", COLORS["danger"]))
        # The current step keeps its accent number.
        self.assertEqual((stepper.rows[0][1].cget("text"), stepper.rows[0][1].cget("bg")), ("1", COLORS["accent"]))
        stepper.clear_status()
        self.assertEqual([row[1].cget("text") for row in stepper.rows], ["1", "2", "3"])
        self.assertEqual(stepper.rows[2][3].cget("text"), "File")


class ScrollAreaTests(KitTestCase):
    def test_fits_width_scrolls_and_passes_the_wheel_on(self):
        area = kit.ScrollArea(self.root)
        area.frame.place(x=0, y=0, width=300, height=200)
        labels = [ttk.Label(area.inner, text="Line %d" % index) for index in range(40)]
        for label in labels: label.pack(anchor="w")
        self.root.update()
        self.assertEqual(area.inner.winfo_width(), area.canvas.winfo_width())
        self.assertEqual(area.scrollbar.winfo_manager(), "pack")
        self.assertIn(area.tag, labels[5].bindtags())
        wheel = lambda delta: area.wheel(SimpleNamespace(widget=labels[1], delta=delta))
        self.assertIsNone(wheel(120))  # Already at the top: the page may scroll instead.
        self.assertEqual(wheel(-120), "break")
        self.assertGreater(area.canvas.yview()[0], 0)
        for label in labels[3:]: label.destroy()
        self.root.update()
        self.assertEqual(area.scrollbar.winfo_manager(), "")
        area.canvas.yview_moveto(0)
        self.assertIsNone(wheel(-120))


class ReviewFixTests(KitTestCase):
    """Wrapping, overlays, table gutters, keyboard order and dialog results."""

    def show(self):
        self.root.deiconify(); self.root.update()

    def test_placeholder_wraps_to_its_area(self):
        hint = "Sign-in steps, GitHub one-time codes and any errors appear here, one line after another."
        host = tk.Frame(self.root, width=px(436), height=px(200)); host.pack_propagate(False); host.pack(anchor="nw")
        frame, text = kit.text_area(host, 7, readonly=True, placeholder=hint); frame.pack(fill="both", expand=True)
        self.show()
        label = text.placeholder_label
        self.assertLessEqual(label.winfo_x() + label.winfo_reqwidth(), text.winfo_width())  # Not cut at the edge.
        narrow = int(str(label.cget("wraplength")))
        host.configure(width=px(800)); self.root.update()
        self.assertGreater(int(str(label.cget("wraplength"))), narrow)  # A wide box doesn't wrap early.
        self.assertLessEqual(label.winfo_x() + label.winfo_reqwidth(), text.winfo_width())

    def test_empty_overlay_keeps_clear_of_the_headings_and_the_border(self):
        host = tk.Frame(self.root, width=px(500), height=px(124)); host.pack_propagate(False); host.pack(anchor="nw")
        frame, tree, empty = kit.table(host, (("name", "Name", 200),), height=2); frame.pack(fill="both", expand=True)
        if empty.glyph is None: self.skipTest("The icon font isn't available")
        self.show()
        empty.show("The rule library couldn't be read.\nCheck the source above, then select Refresh library.")
        self.root.update()
        box = empty.frame
        self.assertGreaterEqual(box.winfo_y(), px(36) + px(2))  # Under the headings,
        self.assertLessEqual(box.winfo_y() + box.winfo_height(), frame.winfo_height() - 1)  # above the bottom border.
        self.assertFalse(empty.glyph.winfo_ismapped())  # No room for the glyph.
        host.configure(height=px(400)); self.root.update()
        self.assertTrue(empty.glyph.winfo_ismapped())

    def test_plain_tables_have_a_gutter_and_lead_tables_keep_theirs(self):
        style = ttk.Style(self.root)
        left = lambda name: int(str(style.lookup(name, "padding")).split()[0])
        self.assertGreaterEqual(left("Treeview.Cell"), px(8)); self.assertGreaterEqual(left("Treeview.Heading"), px(8))
        self.assertLess(left("Lead.Treeview.Heading"), px(8))  # Its dot or checkbox column is the gutter.
        frame, plain, _ = kit.table(self.root, (("name", "Name", 150),)); frame.pack()
        _, lead, _ = kit.table(self.root, (("name", "Name", 150),), marker=True)
        self.assertEqual((plain.PAD, lead.PAD), (23, 9))
        room = int(plain.column("name", "width")) - px(plain.PAD)
        body, text = kit.font(self.root, "body"), "W"
        while body.measure(text + "W") <= room: text += "W"
        plain.insert("", "end", iid="a", values=(text,))
        self.assertEqual(plain.shortened("a", "name"), "")  # Exactly fits: whole.
        plain.item("a", values=(text + "WW",))
        self.assertEqual(plain.shortened("a", "name"), text + "WW")  # Too long: shortened.

    def test_enter_in_a_search_box_never_presses_the_dialog_button(self):
        window = tk.Toplevel(self.root); pressed = []
        window.bind("<Return>", lambda event: pressed.append(event.widget))
        var = tk.StringVar(); entry = ttk.Entry(window, textvariable=var); entry.pack()
        frame, tree, _ = kit.table(window, (("name", "Name", 200),)); frame.pack()
        kit.link_search(entry, tree)
        window.update(); entry.focus_force(); window.update()
        entry.event_generate("<Return>"); window.update()
        self.assertEqual(pressed, [])  # Nothing matched: Enter stays in the box.
        tree.insert("", "end", iid="a", values=("a",))
        entry.event_generate("<Return>"); window.update()
        self.assertEqual(pressed, []); self.assertIs(window.focus_get(), tree)  # It moves into the results.
        window.destroy()

    def test_a_tooltip_never_makes_a_label_a_tab_stop(self):
        label = ttk.Label(self.root, text="Ready"); label.pack(); kit.tooltip(label, "")
        values = kit.KeyValues(self.root); values.pack(fill="x"); values.set([("Rule ID", "a" * 60)], mono={"Rule ID"})
        button = ttk.Button(self.root, text="Go"); button.pack(); kit.tooltip(button, "Go now")
        self.show()
        focusable = lambda widget: str(self.root.tk.eval("tk::FocusOK " + str(widget)))
        self.assertEqual((focusable(label), focusable(values.values[0]), focusable(button)), ("0", "0", "1"))

    def test_read_only_text_is_a_tab_stop_that_scrolls_and_lets_go(self):
        before = ttk.Button(self.root, text="Before"); before.pack()
        frame, text = kit.text_area(self.root, 4, readonly=True); frame.pack()
        after = ttk.Button(self.root, text="After"); after.pack()
        kit.set_text(text, "\n".join("Line %d" % number for number in range(40)))
        self.show()
        self.assertIs(before.tk_focusNext(), text)
        text.focus_force(); self.root.update()
        text.event_generate("<Next>"); self.root.update()
        self.assertGreater(text.yview()[0], 0)  # Page Down scrolls it.
        text.event_generate("<Shift-Tab>"); self.root.update()
        self.assertIs(self.root.focus_get(), before)  # Shift+Tab isn't trapped.
        text.focus_force(); self.root.update()
        text.event_generate("<Tab>"); self.root.update()
        self.assertIs(self.root.focus_get(), after)

    def test_action_row_tab_follows_the_visual_order(self):
        window = tk.Toplevel(self.root)
        entry = ttk.Entry(window); entry.pack()
        row, buttons = kit.action_row(window, primary=("Submit", None), secondary=(("Check", None),),
                                      close=("Close", None), aux=(("Open in GitHub", None),))
        row.pack(fill="x"); window.update()
        order, widget = [], entry
        for _ in buttons:
            widget = widget.tk_focusNext(); order.append(widget.cget("text"))
        self.assertEqual(order, ["Open in GitHub", "Submit", "Check", "Close"])  # Left to right, as they read.
        self.assertEqual([b.cget("text") for b in buttons], ["Submit", "Check", "Close", "Open in GitHub"])
        window.destroy()

    def test_message_dialog_keeps_its_dismiss_button_rightmost(self):
        dialog = kit.MessageDialog(self.root, "Couldn't load workspaces", "Try again.", details="trace")
        self.addCleanup(lambda: dialog.window.winfo_exists() and dialog.window.destroy())
        self.assertEqual(str(dialog.copy_button.cget("style")), "Ghost.TButton")
        self.assertEqual(dialog.copy_button.pack_info()["side"], "left")
        right = [button for button in dialog.actions.pack_slaves() if button.pack_info()["side"] == "right"]
        self.assertEqual(right, [dialog.ok_button])
        dialog.window.update()
        self.assertGreater(dialog.ok_button.winfo_x(), dialog.copy_button.winfo_x())
        asked = kit.MessageDialog(self.root, "Restore unsaved rule?", "Restore it?", ok="Restore", cancel="Discard")
        self.addCleanup(lambda: asked.window.winfo_exists() and asked.window.destroy())
        self.assertEqual(asked.actions.pack_slaves()[0], asked.cancel_button)  # Packed first from the right.

    def test_confirm_tells_not_now_apart_from_cancel(self):
        self.show()
        def later(action):
            def run():
                window = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
                if action == "escape":
                    window.focus_force(); window.update(); (window.focus_get() or window).event_generate("<Escape>")
                elif action == "close": window.tk.call(window.protocol("WM_DELETE_WINDOW"))
                else: next(w for w in descendants(window) if isinstance(w, ttk.Button) and w.cget("text") == action).invoke()
            self.root.after(150, run)
        ask = lambda: kit.confirm(self.root, "Restore unsaved rule?", "Restore it?", ok="Restore", cancel="Discard",
                                  closed=None)
        later("escape"); self.assertIsNone(ask())
        later("close"); self.assertIsNone(ask())
        later("Discard"); self.assertIs(ask(), False)
        later("Restore"); self.assertIs(ask(), True)
        later("close"); self.assertIs(kit.confirm(self.root, "Remove client", "Remove it?"), False)  # The default.

    def test_dialogs_open_on_the_parents_monitor(self):
        class Top:
            def __init__(self, x, y): self.x, self.y = x, y
            def winfo_toplevel(self): return self
            def winfo_viewable(self): return 1
            def winfo_width(self): return 1500
            def winfo_height(self): return 900
            def winfo_rootx(self): return self.x
            def winfo_rooty(self): return self.y
        for parent, area in ((Top(2509, 131), (2400, 0, 3200, 1740)), (Top(-1800, 60), (-1920, 0, 1920, 1040))):
            window = MagicMock()
            with patch.object(kit, "work_area", return_value=area) as lookup, \
                 patch.object(kit, "window_frame", return_value=(16, 39)):
                kit.place_dialog(window, parent, 520, 300)
            lookup.assert_called_once_with(window, parent.x + 750, parent.y + 450)  # The parent's monitor.
            w, h, x, y = map(int, re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", window.geometry.call_args.args[0]).groups())
            left, top, width, height = area
            self.assertGreaterEqual(x, left); self.assertLessEqual(x + w + 16, left + width)
            self.assertGreaterEqual(y, top); self.assertLessEqual(y + h + 39, top + height)
        self.assertLess(x, 0)  # Left of the primary monitor, and kept there.


if __name__ == "__main__":
    unittest.main()
