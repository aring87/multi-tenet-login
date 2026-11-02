"""Composite widgets shared by the desktop pages and dialogs: cards, pills,
segmented controls, key/value lists, banners, empty states, tables, text
areas, and the helpers that give every window the same keyboard behaviour.

They are drawn with plain Tk/ttk widgets so they need no extra dependencies.
"""
import math
import re
import weakref
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .desktop_theme import COLORS, FONTS, TONES, ICONS, SPACE, px

# Widgets whose look depends on the app being idle; App.set_busy calls refresh_all().
_REFRESHABLE = weakref.WeakSet()


def space(name):
    """A spacing-scale step (xs, sm, md, lg, xl, page) scaled for the display."""
    return px(SPACE[name])


def refresh_all():
    for widget in list(_REFRESHABLE):
        try:
            if widget.winfo_exists(): widget.refresh()
        except tk.TclError:
            pass


def wrap_to_parent(label, margin=0, minimum=None):
    """Wrap a label to the width it is given. Pack or grid it to fill horizontally."""
    minimum = px(180) if minimum is None else minimum
    def rewrap(event):
        width = max(minimum, event.width - margin)
        if int(str(label.cget("wraplength")) or 0) != width:
            label.configure(wraplength=width)
    label.bind("<Configure>", rewrap, add="+")
    return label


def autohide(scrollbar, show):
    """Return a scroll command that shows the scrollbar only while content overflows."""
    def update(first, last):
        scrollbar.set(first, last)
        overflowing = float(first) > 0 or float(last) < 1
        if overflowing and not scrollbar.winfo_manager(): show()
        elif not overflowing and scrollbar.winfo_manager():
            scrollbar.grid_remove() if scrollbar.winfo_manager() == "grid" else scrollbar.pack_forget()
    return update


def placeholder(entry, variable, text):
    """Muted hint drawn inside an empty entry until it has focus or a value."""
    hint = tk.Label(entry, text=text, font=FONTS["body"], fg=COLORS["muted"], bg=COLORS["card"], bd=0, cursor="xterm")
    def refresh(*args):
        if variable.get() or entry.focus_get() is entry: hint.place_forget()
        else: hint.place(x=px(11), rely=.5, anchor="w")
    hint.bind("<Button-1>", lambda event: entry.focus_set())
    entry.bind("<FocusIn>", refresh, add="+"); entry.bind("<FocusOut>", refresh, add="+")
    variable.trace_add("write", refresh); refresh()
    return hint


def icon_label(parent, name, background, foreground=None, font="icon"):
    """A glyph from the Windows icon font, or nothing when it is unavailable."""
    if not FONTS.get(font) or name not in ICONS:
        return None
    return tk.Label(parent, text=ICONS[name], font=FONTS[font], bg=background,
                    fg=foreground or COLORS["muted"], bd=0)


def icon_tile(parent, name, tone="info", size=34, background=None):
    """A soft square holding an icon, used at the start of card headers."""
    fg, soft = TONES[tone]
    tile = tk.Frame(parent, width=px(size), height=px(size), bg=soft, bd=0, highlightthickness=0)
    tile.pack_propagate(False)
    glyph = icon_label(tile, name, soft, fg)
    if glyph is None:
        tile.configure(width=px(6), bg=fg)
    else:
        glyph.pack(expand=True)
    return tile


def ellipsize(text, font, width):
    """Shorten ``text`` with a trailing ellipsis so it fits ``width`` pixels in ``font``
    (a tkfont.Font). Text that already fits is returned unchanged."""
    if width <= 0 or font.measure(text) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle].rstrip() + "…") <= width: low = middle
        else: high = middle - 1
    return text[:low].rstrip() + "…" if low else "…"


def tooltip(widget, text):
    """Show ``text`` under the widget after a short hover, and on keyboard focus.
    The text is read from ``widget.tooltip_text`` when shown, so it can be updated;
    an empty text shows nothing."""
    state = {"window": None, "timer": None}
    def show():
        state["timer"] = None
        if state["window"] is not None or not widget.winfo_exists(): return
        current = getattr(widget, "tooltip_text", text)
        if not current: return
        window = tk.Toplevel(widget); window.overrideredirect(True)
        try: window.attributes("-topmost", True)
        except tk.TclError: pass
        window.configure(bg=COLORS["line_strong"])
        tk.Label(window, text=current, font=FONTS["small"], bg=COLORS["card"], fg=COLORS["ink"],
                 padx=px(8), pady=px(4), bd=0, justify="left", wraplength=px(420)).pack(padx=1, pady=1)
        window.update_idletasks()
        x = widget.winfo_rootx() + (widget.winfo_width() - window.winfo_reqwidth()) // 2
        y = widget.winfo_rooty() + widget.winfo_height() + px(6)
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
        state["window"] = window
    def cancel(event=None):
        if state["timer"] is not None:
            try: widget._root().after_cancel(state["timer"])
            except tk.TclError: pass
            state["timer"] = None
        if state["window"] is not None:
            try: state["window"].destroy()
            except tk.TclError: pass
            state["window"] = None
    def schedule(event=None):
        cancel(); state["timer"] = widget._root().after(600, show)
    widget.bind("<Enter>", schedule, add="+"); widget.bind("<FocusIn>", schedule, add="+")
    for sequence in ("<Leave>", "<ButtonPress>", "<FocusOut>", "<Destroy>"):
        widget.bind(sequence, cancel, add="+")
    widget.tooltip_text = text
    return widget


def link(parent, text, command, fg, hover_fg, bg, font=None, focus_color=None):
    """A text link that is reachable from the keyboard and shows a focus ring."""
    label = tk.Label(parent, text=text, font=font or FONTS["small"], bg=bg, fg=fg, cursor="hand2", bd=0,
                     anchor="w", takefocus=1, highlightthickness=1, highlightbackground=bg,
                     highlightcolor=focus_color or COLORS["accent"])
    label.bind("<Button-1>", lambda event: command())
    label.bind("<Return>", lambda event: command()); label.bind("<space>", lambda event: command())
    label.bind("<Enter>", lambda event: label.configure(fg=hover_fg))
    label.bind("<Leave>", lambda event: label.configure(fg=fg))
    return label


class Card:
    """White surface with a hairline border, a soft shadow and an optional header row.

    ``frame`` is the outer widget to place; ``surface`` is the white panel,
    ``body`` receives content and ``actions`` sits at the right of the header
    for buttons or pills. The title wraps beside the actions and the subtitle
    runs under both."""

    def __init__(self, parent, title=None, subtitle=None, icon=None, tone="info", padding=20, shadow=True):
        # The outer frame shows through as a 2px strip under the surface: the shadow.
        self.frame = tk.Frame(parent, bg=COLORS["shadow"] if shadow else COLORS["canvas"], bd=0, highlightthickness=0)
        self.surface = tk.Frame(self.frame, bg=COLORS["card"], bd=0, highlightthickness=1,
                                highlightbackground=COLORS["line"], highlightcolor=COLORS["line"])
        self.surface.pack(fill="both", expand=True, pady=(0, px(2) if shadow else 0))
        self.frame.card = self  # Keep the title variables alive as long as the widget.
        self.title_var = tk.StringVar(value=title or "")
        self.subtitle_var = tk.StringVar(value=subtitle or "")
        self.actions = None
        if title is not None:
            head = ttk.Frame(self.surface, style="Card.TFrame", padding=(px(padding), px(padding - 4), px(padding), 0))
            head.pack(fill="x")
            head.columnconfigure(1, weight=1)
            if icon:
                icon_tile(head, icon, tone).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, px(12)), pady=(px(1), 0))
            # width=1: the grid column, not the text, decides how wide these labels are;
            # otherwise the wrapped width feeds back into the column and the layout loops.
            self.title_label = ttk.Label(head, textvariable=self.title_var, style="CardTitle.TLabel", anchor="w",
                                         justify="left", wraplength=px(480), width=1)
            self.title_label.grid(row=0, column=1, sticky="ew")
            wrap_to_parent(self.title_label, minimum=px(80))
            self.actions = ttk.Frame(head, style="Card.TFrame")
            self.actions.grid(row=0, column=2, sticky="ne", padx=(px(16), 0))
            if subtitle is not None:
                self.subtitle_label = ttk.Label(head, textvariable=self.subtitle_var, style="CardSubtitle.TLabel",
                                                justify="left", wraplength=px(480), width=1)
                self.subtitle_label.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(px(2), 0))
                wrap_to_parent(self.subtitle_label, minimum=px(120))
        self.body = ttk.Frame(self.surface, style="Card.TFrame",
                              padding=(px(padding), px(16 if title is not None else padding), px(padding), px(padding)))
        self.body.pack(fill="both", expand=True)


class Pill(tk.Label):
    """Compact status label tinted by tone: neutral, info, success, warning or danger."""

    def __init__(self, parent, text="", tone="neutral", dot=True):
        super().__init__(parent, font=FONTS["strong_small"], bd=0, padx=px(9), pady=px(2))
        self.dot = dot
        self.set(text, tone)

    def set(self, text, tone="neutral"):
        fg, bg = TONES.get(tone, TONES["neutral"])
        self.tone = tone
        self.configure(text=("●  " if self.dot else "") + text, fg=fg, bg=bg)


class Segmented(tk.Frame):
    """Exclusive choice bound to a StringVar: one Tab stop, arrow keys move the
    selection. Input is ignored while ``enabled`` is false and the options dim."""

    def __init__(self, parent, variable, options, enabled=lambda: True, background=None, compact=False):
        super().__init__(parent, bg=COLORS["hover"], bd=0, highlightthickness=1, takefocus=1,
                         highlightbackground=COLORS["line"], highlightcolor=COLORS["accent"])
        self.variable, self.enabled, self.items = variable, enabled, {}
        self.order = [value for value, _ in options]
        for value, label in options:
            item = tk.Label(self, text=label, font=FONTS["strong_small"], bd=0, cursor="hand2", takefocus=0,
                            padx=px(10 if compact else 14), pady=px(4 if compact else 6))
            item.pack(side="left", padx=px(2), pady=px(2), fill="y")
            item.bind("<Button-1>", lambda event, v=value: (self.focus_set(), self.choose(v)))
            item.bind("<Enter>", lambda event, v=value: self.hover(v, True))
            item.bind("<Leave>", lambda event, v=value: self.hover(v, False))
            self.items[value] = item
        for sequence, step in (("<Left>", -1), ("<Up>", -1), ("<Right>", 1), ("<Down>", 1)):
            self.bind(sequence, lambda event, s=step: self.move(s))
        self.bind("<Home>", lambda event: self.move(None, self.order[0]))
        self.bind("<End>", lambda event: self.move(None, self.order[-1]))
        variable.trace_add("write", lambda *args: self.render())
        _REFRESHABLE.add(self)
        self.render()

    def choose(self, value):
        if self.enabled() and self.variable.get() != value:
            self.variable.set(value)

    def move(self, step, value=None):
        if value is None:
            current = self.variable.get()
            index = self.order.index(current) if current in self.order else 0
            value = self.order[(index + step) % len(self.order)]
        self.choose(value)
        return "break"

    def hover(self, value, inside):
        if value != self.variable.get():
            self.items[value].configure(bg=COLORS["line"] if inside and self.enabled() else COLORS["hover"])

    def refresh(self):
        self.render()

    def render(self):
        active = self.enabled()
        for value, item in self.items.items():
            selected = value == self.variable.get()
            item.configure(bg=COLORS["card"] if selected else COLORS["hover"],
                           fg=(COLORS["ink"] if selected else COLORS["muted"]) if active else COLORS["disabled"],
                           cursor="hand2" if active else "arrow")


class KeyValues(ttk.Frame):
    """Two-column list of labelled values. Values named in ``mono`` use the
    monospace font (identifiers); the rest read as prose."""

    def __init__(self, parent, key_width=16, surface="", stacked=False):
        super().__init__(parent, style=(surface or "Card") + ".TFrame")
        self.key_width, self.surface, self.stacked = key_width, surface, stacked
        self.values = []
        self.columnconfigure(1, weight=1)
        self.bind("<Configure>", self.rewrap, add="+")

    def rewrap(self, event=None):
        width = max(px(160), self.winfo_width() - (0 if self.stacked else px(self.key_width * 8)))
        for label in self.values:
            label.configure(wraplength=width)

    def set(self, rows, mono=None):
        for child in self.winfo_children():
            child.destroy()
        self.values = []
        for index, (key, value) in enumerate(rows):
            pad = (0 if index == 0 else px(10 if self.stacked else 7), 0)
            style = self.surface + ("Value" if mono is None or key in mono else "Prose") + ".TLabel"
            key_label = ttk.Label(self, text=key, style=self.surface + "Key.TLabel", anchor="w",
                                  width=0 if self.stacked else self.key_width)
            value_label = ttk.Label(self, text=value, style=style, justify="left", wraplength=px(320))
            if self.stacked:
                key_label.grid(row=2 * index, column=0, columnspan=2, sticky="w", pady=pad)
                value_label.grid(row=2 * index + 1, column=0, columnspan=2, sticky="ew", pady=(px(1), 0))
            else:
                key_label.grid(row=index, column=0, sticky="nw", pady=pad)
                value_label.grid(row=index, column=1, sticky="ew", pady=pad)
            self.values.append(value_label)
        self.rewrap()


class Banner(tk.Frame):
    """Inline message with a tinted background and an optional action button
    on the right; ``tone`` changes colour and icon, ``set_action`` the button."""

    def __init__(self, parent, textvariable=None, tone="info", icon="info", action=None):
        super().__init__(parent, bd=0, highlightthickness=0)
        self.icon = icon_label(self, icon, COLORS["card"], font="icon_small")
        if self.icon is not None:
            self.icon.pack(side="left", anchor="n", padx=(px(12), 0), pady=px(10))
        self.actions = tk.Frame(self, bd=0, highlightthickness=0)
        self.actions.pack(side="right", anchor="n", padx=(px(8), px(10)), pady=px(5))
        self.action_button = None
        self.label = tk.Label(self, textvariable=textvariable, font=FONTS["body"], justify="left",
                              anchor="w", bd=0, padx=px(10), pady=px(8), wraplength=px(600))
        self.label.pack(side="left", fill="x", expand=True)
        self.bind("<Configure>", self.rewrap, add="+")
        self.tone(tone)
        if action: self.set_action(*action)

    def rewrap(self, event=None):
        reserved = px(56) + (self.actions.winfo_reqwidth() if self.action_button is not None else 0)
        self.label.configure(wraplength=max(px(200), self.winfo_width() - reserved))

    def set_action(self, text=None, command=None):
        if self.action_button is not None:
            self.action_button.destroy(); self.action_button = None
        if text:
            self.action_button = ttk.Button(self.actions, text=text, command=command, style="Small.Secondary.TButton")
            self.action_button.pack()
        self.rewrap()

    def tone(self, tone, icon=None):
        fg, bg = TONES.get(tone, TONES["info"])
        self.configure(bg=bg)
        self.actions.configure(bg=bg)
        self.label.configure(bg=bg, fg=COLORS["ink_2"])
        if self.icon is not None:
            self.icon.configure(bg=bg, fg=fg, text=ICONS[icon or {"danger": "warning", "warning": "warning",
                                                                 "success": "check"}.get(tone, "info")])


class ProgressLine(tk.Canvas):
    """Thin indeterminate activity bar: a short accent segment glides along a quiet
    track while work runs. It replaces ttk.Progressbar, whose indeterminate mode is
    invisible at this thickness, and keeps its ``start``/``stop`` names."""

    LENGTH, INTERVAL_MS, STEP = 0.28, 24, 0.011

    def __init__(self, parent, background=None, thickness=3):
        super().__init__(parent, height=px(thickness), bg=background or COLORS["card"], bd=0, highlightthickness=0)
        self.track = self.create_rectangle(0, 0, 0, 0, fill=COLORS["accent_line"], width=0, state="hidden")
        self.bar = self.create_rectangle(0, 0, 0, 0, fill=COLORS["accent"], width=0, state="hidden")
        self.timer, self.phase = None, 0.0
        self.bind("<Configure>", lambda event: self.draw(), add="+")

    def running(self):
        return self.timer is not None

    def start(self, interval=None):
        """Begin animating. ``interval`` is accepted for ttk.Progressbar compatibility."""
        if self.timer is None:
            self.itemconfigure(self.track, state="normal"); self.itemconfigure(self.bar, state="normal")
            self.tick()

    def stop(self):
        if self.timer is not None:
            try: self._root().after_cancel(self.timer)
            except tk.TclError: pass
            self.timer = None
        self.phase = 0.0
        try:
            self.itemconfigure(self.track, state="hidden"); self.itemconfigure(self.bar, state="hidden")
        except tk.TclError:
            pass

    def tick(self):
        self.timer = None
        if not self.winfo_exists(): return
        self.phase = (self.phase + self.STEP) % 1.0
        self.draw()
        # Scheduled on the root, like every timer in the kit, so a pending callback is owned
        # by one widget only and cancelling it elsewhere never leaves a dangling command here.
        self.timer = self._root().after(self.INTERVAL_MS, self.tick)

    def draw(self):
        width, height = self.winfo_width(), self.winfo_height()
        self.coords(self.track, 0, 0, width, height)
        if self.timer is None: return
        length = max(px(72), width * self.LENGTH)
        eased = 0.5 - math.cos(math.pi * self.phase) / 2  # Slow at both ends, quick through the middle.
        x = -length + (width + length) * eased
        self.coords(self.bar, x, 0, x + length, height)


class StatTile:
    """A metric card: a coloured accent along the top, a small caption and a large number."""

    def __init__(self, parent, caption, tone="neutral"):
        self.card = Card(parent, padding=16)
        self.frame = self.card.frame
        fg, _ = TONES[tone]
        tk.Frame(self.card.surface, bg=fg if tone != "neutral" else COLORS["line_strong"], height=px(3), bd=0).pack(
            fill="x", side="top", before=self.card.body)
        ttk.Label(self.card.body, text=caption.upper(), style="Eyebrow.TLabel").pack(anchor="w")
        self.value = tk.StringVar(value="—")
        ttk.Label(self.card.body, textvariable=self.value, style="Stat.TLabel").pack(anchor="w", pady=(px(2), 0))


class EmptyOverlay:
    """Message drawn over a table while it has no rows, with an optional action, a
    large muted glyph above the text when the icon font is available, and an
    activity line while ``busy`` (loading) is set.

    The message is centred in the space under the column headings, so it never
    overlaps them in a short table and sits comfortably high in a tall one."""

    HEADING = 36  # Treeview heading height, 96-DPI pixels.

    def __init__(self, host, text="", icon="rules"):
        self.host = host
        self.frame = tk.Frame(host, bg=COLORS["card"], bd=0, highlightthickness=0)
        self.glyph = icon_label(self.frame, icon, COLORS["card"], COLORS["line_strong"], font="icon_hero")
        if self.glyph is not None: self.glyph.pack(pady=(0, px(10)))
        self.text = tk.StringVar(value=text)
        self.label = tk.Label(self.frame, textvariable=self.text, font=FONTS["body"], fg=COLORS["muted"],
                              bg=COLORS["card"], justify="center", wraplength=px(420))
        self.label.pack()
        self.line = ProgressLine(self.frame, thickness=3)
        self.button = ttk.Button(self.frame, style="Small.Secondary.TButton")
        host.bind("<Configure>", lambda event: self.visible() and self.position(), add="+")

    def position(self):
        # place() remembers earlier options, so rely and y are always both given.
        height = self.host.winfo_height()
        if height <= 1:
            self.frame.place(relx=.5, rely=.4, y=0, anchor="center"); return
        head = px(self.HEADING)
        room = max(0, height - head)
        # A tall table reads better with the message in its upper third; a short one is simply centred.
        share = .5 if room < px(260) else .36
        self.frame.place(relx=.5, rely=0, y=head + int(room * share), anchor="center")

    def show(self, text=None, action=None, busy=False):
        if text is not None:
            self.text.set(text)
        if busy:
            if not self.line.winfo_manager(): self.line.pack(fill="x", padx=px(40), pady=(px(12), 0))
            self.line.start()
            if self.glyph is not None: self.glyph.pack_forget()
        else:
            self.line.stop(); self.line.pack_forget()
            if self.glyph is not None and not self.glyph.winfo_manager(): self.glyph.pack(before=self.label, pady=(0, px(10)))
        if action:
            self.button.configure(text=action[0], command=action[1])
            if not self.button.winfo_manager(): self.button.pack(pady=(px(10), 0))
        else:
            self.button.pack_forget()
        self.position()

    def hide(self):
        self.line.stop()
        self.frame.place_forget()

    def visible(self):
        return bool(self.frame.winfo_manager())


class StripedTreeview(ttk.Treeview):
    """A Treeview whose top-level rows are striped as they are inserted, so every
    list in the app alternates row tints without each page having to count."""

    def insert(self, parent, index, iid=None, **kw):
        if parent == "" and index == "end" and len(self.get_children("")) % 2:
            kw["tags"] = tuple(kw.get("tags", ())) + ("stripe",)
        return super().insert(parent, index, iid, **kw)


def table(parent, columns, height=10, selectmode="browse", icon="rules"):
    """A bordered Treeview with striped rows, slim scrollbars and an empty-state overlay.

    ``columns`` holds (key, heading, width[, stretch]) tuples."""
    frame = tk.Frame(parent, bg=COLORS["card"], bd=0, highlightthickness=1,
                     highlightbackground=COLORS["line"], highlightcolor=COLORS["line"])
    tree = StripedTreeview(frame, columns=[key for key, *_ in columns], show="headings", height=height,
                           selectmode=selectmode)
    for key, title, width, *stretch in columns:
        tree.heading(key, text=title, anchor="w")
        tree.column(key, width=px(width), minwidth=px(70), anchor="w", stretch=bool(stretch[0]) if stretch else True)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.grid(row=0, column=0, sticky="nsew"); vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=autohide(vertical, vertical.grid), xscrollcommand=autohide(horizontal, horizontal.grid))
    # Re-report the scroll range when the width changes, so the horizontal bar
    # hides once everything fits. Only on a width change: the bar appearing
    # changes the height, and reacting to that would loop.
    seen = {"width": None}
    def reflow(event):
        if seen["width"] == event.width: return
        seen["width"] = event.width
        # Scheduled on the root so a pending callback is owned by one widget only.
        tree._root().after_idle(lambda: tree.winfo_exists() and tree.xview_moveto(tree.xview()[0]))
    tree.bind("<Configure>", reflow, add="+")
    frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
    tree.tag_configure("stripe", background=COLORS["stripe"])
    tree.tag_configure("muted", foreground=COLORS["muted"])
    tree.tag_configure("danger", foreground=COLORS["danger"])
    return frame, tree, EmptyOverlay(frame, icon=icon)


def text_area(parent, height, readonly=False, wrap="word", undo=False, font="mono", placeholder=None):
    """A Text widget with a slim scrollbar inside a bordered frame.

    ``placeholder`` is muted text shown while the area is empty; write through
    ``set_text`` (or call ``refresh_placeholder``) so it comes and goes."""
    frame = tk.Frame(parent, bg=COLORS["card"], bd=0, highlightthickness=1,
                     highlightbackground=COLORS["line"], highlightcolor=COLORS["line"])
    text = tk.Text(frame, height=height, width=30, wrap=wrap, undo=undo, font=FONTS[font],
                   highlightthickness=0, bg=COLORS["card"])
    scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.pack(side="left", fill="both", expand=True)
    text.configure(yscrollcommand=autohide(scroll, lambda: scroll.pack(side="right", fill="y", before=text)))
    if wrap == "none":
        across = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(xscrollcommand=autohide(across, lambda: across.pack(side="bottom", fill="x", before=text)))
    # A flagged frame (frame.error) keeps its danger border through focus changes.
    text.bind("<FocusIn>", lambda event: frame.configure(
        highlightbackground=COLORS["danger"] if getattr(frame, "error", False) else COLORS["accent"]), add="+")
    text.bind("<FocusOut>", lambda event: frame.configure(
        highlightbackground=COLORS["danger"] if getattr(frame, "error", False) else COLORS["line"]), add="+")
    if readonly:
        text.configure(state="disabled", bg=COLORS["raised"])
        frame.configure(bg=COLORS["raised"])
    if placeholder:
        text.placeholder_label = tk.Label(text, text=placeholder, font=FONTS["body"], fg=COLORS["subtle"],
                                          bg=COLORS["raised"] if readonly else COLORS["card"], bd=0,
                                          justify="left", anchor="nw", wraplength=px(440))
        text.placeholder_label.bind("<Button-1>", lambda event: text.focus_set())
        if not readonly:
            # An editable area clears its hint while it has the keyboard, so the caret is never hidden.
            text.bind("<<Modified>>", lambda event: refresh_placeholder(text), add="+")
            text.bind("<FocusIn>", lambda event: text.placeholder_label.place_forget(), add="+")
            text.bind("<FocusOut>", lambda event: refresh_placeholder(text), add="+")
        refresh_placeholder(text)
    return frame, text


def refresh_placeholder(text):
    """Show a text area's placeholder while it is empty, hide it otherwise."""
    label = getattr(text, "placeholder_label", None)
    if label is None: return
    if text.get("1.0", "end-1c").strip(): label.place_forget()
    elif not label.winfo_manager(): label.place(x=px(12), y=px(10))


def set_text(widget, value):
    """Replace the contents of a Text widget, preserving its state."""
    state = widget.cget("state")
    widget.configure(state="normal"); widget.delete("1.0", "end"); widget.insert("1.0", value)
    widget.configure(state=state)
    refresh_placeholder(widget)


def font(widget, name):
    """A measuring tkfont.Font for the named FONTS entry, cached per Tk instance."""
    root = widget._root()
    fonts = getattr(root, "_kit_fonts", None)
    if fonts is None:
        fonts = root._kit_fonts = {}
    if name not in fonts:
        fonts[name] = tkfont.Font(root, font=FONTS[name])
    return fonts[name]


def field_label(parent, text, hint=None):
    """Label above a form control, with an optional muted hint on the right.

    The hint gives way to the label: in a narrow column it is shortened with an
    ellipsis rather than clipped mid-word."""
    row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x", pady=(0, px(5)))
    row.label = ttk.Label(row, text=text, style="Field.TLabel"); row.label.pack(side="left")
    row.hint = None
    if hint:
        row.hint = ttk.Label(row, text=hint, style="Hint.TLabel", anchor="e")
        row.hint.pack(side="right", padx=(px(8), 0))
        small = font(row, "small")
        def fit(event=None):
            room = row.winfo_width() - row.label.winfo_reqwidth() - px(12)
            row.hint.configure(text=ellipsize(hint, small, room) if room > px(30) else "")
        row.bind("<Configure>", fit, add="+")
    return row


def section_label(parent, text, style="Section.TLabel", action=None):
    """A small heading inside a card. ``action`` is an optional (text, command)
    pair shown as a ghost button at the right. Returns the row; ``row.label``
    and ``row.button`` hold its parts."""
    row = ttk.Frame(parent, style="Card.TFrame")
    row.pack(fill="x", pady=(0, px(8)))
    row.label = ttk.Label(row, text=text, style=style)
    row.label.pack(side="left")
    row.button = None
    if action:
        row.button = ttk.Button(row, text=action[0], command=action[1], style="Small.Ghost.TButton")
        row.button.pack(side="right")
    return row


def divider(parent, pady=16):
    line = tk.Frame(parent, bg=COLORS["line"], height=1, bd=0)
    line.pack(fill="x", pady=px(pady))
    return line


_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def duration_text(value):
    """'PT1H' -> '1 hour'; unknown text is returned unchanged."""
    match = _DURATION.match(str(value or "").strip())
    if not match or not any(match.groups()):
        return str(value or "")
    words = []
    for amount, unit in zip(match.groups(), ("day", "hour", "minute", "second")):
        if amount and int(amount):
            amount = int(amount); words.append(f"{amount} {unit}{'s' if amount != 1 else ''}")
    return " ".join(words)


class Stepper(ttk.Frame):
    """Vertical list of numbered steps that drives a hidden-tab Notebook.
    Rows are Tab stops with a focus ring; Up/Down move between them."""

    def __init__(self, parent, notebook, steps, enabled=lambda: True):
        super().__init__(parent, style="Card.TFrame")
        self.notebook, self.enabled, self.rows = notebook, enabled, []
        for index, (title, description) in enumerate(steps):
            row = tk.Frame(self, bg=COLORS["card"], cursor="hand2", bd=0, takefocus=1, highlightthickness=1,
                           highlightbackground=COLORS["card"], highlightcolor=COLORS["accent"])
            row.pack(fill="x", pady=(0, px(2)))
            marker = tk.Label(row, text=str(index + 1), font=FONTS["strong_small"], width=3, bd=0, pady=px(3))
            marker.grid(row=0, column=0, rowspan=2, sticky="n", padx=(px(10), px(10)), pady=px(10))
            name = tk.Label(row, text=title, font=FONTS["strong"], anchor="w", bd=0)
            name.grid(row=0, column=1, sticky="ew", pady=(px(9), 0), padx=(0, px(10)))
            detail = tk.Label(row, text=description, font=FONTS["small"], anchor="w", justify="left", bd=0)
            detail.grid(row=1, column=1, sticky="ew", pady=(0, px(9)), padx=(0, px(10)))
            row.columnconfigure(1, weight=1)
            for part in (row, marker, name, detail):
                part.bind("<Button-1>", lambda event, i=index: (self.rows[i][0].focus_set(), self.select(i)))
                part.bind("<Enter>", lambda event, i=index: self.hover(i, True))
                part.bind("<Leave>", lambda event, i=index: self.hover(i, False))
            row.bind("<Return>", lambda event, i=index: self.select(i))
            row.bind("<space>", lambda event, i=index: self.select(i))
            row.bind("<Up>", lambda event, i=index: self.focus_row(i - 1))
            row.bind("<Down>", lambda event, i=index: self.focus_row(i + 1))
            self.rows.append((row, marker, name, detail))
        notebook.bind("<<NotebookTabChanged>>", lambda event: self.render(), add="+")
        _REFRESHABLE.add(self)
        self.render()

    def focus_row(self, index):
        if 0 <= index < len(self.rows):
            self.rows[index][0].focus_set()
        return "break"

    def select(self, index):
        if self.enabled():
            self.notebook.select(index)

    def hover(self, index, inside):
        if index != self.notebook.index("current"):
            self.paint(index, COLORS["hover"] if inside else COLORS["card"], False)

    def paint(self, index, background, selected):
        row, marker, name, detail = self.rows[index]
        active = self.enabled()
        for part in (row, name, detail):
            part.configure(bg=background)
        row.configure(highlightbackground=background, cursor="hand2" if active else "arrow")
        name.configure(fg=(COLORS["ink"] if selected else COLORS["ink_2"]) if active else COLORS["disabled"])
        detail.configure(fg=COLORS["muted"] if active else COLORS["disabled"])
        marker.configure(bg=COLORS["accent"] if selected else COLORS["hover"],
                         fg="#FFFFFF" if selected else (COLORS["muted"] if active else COLORS["disabled"]))

    def refresh(self):
        self.render()

    def render(self):
        current = self.notebook.index("current") if self.notebook.tabs() else 0
        for index in range(len(self.rows)):
            self.paint(index, COLORS["accent_soft"] if index == current else COLORS["card"], index == current)


# ------------------------------------------------------------------ dialogs
def place_dialog(window, parent, width=None, height=None, min_width=None, min_height=None):
    """Size a Toplevel in scaled pixels, clamp it to the parent and screen, and centre it."""
    if width is None or height is None:
        # Only measure when needed: idle tasks on a not-yet-shown Toplevel whose master is
        # withdrawn leave it withdrawn for good on Windows, even after deiconify.
        window.update_idletasks()
    top = parent.winfo_toplevel()
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    anchored = bool(top.winfo_viewable()) and top.winfo_width() > 1
    if anchored:
        ax, ay, aw, ah = top.winfo_rootx(), top.winfo_rooty(), top.winfo_width(), top.winfo_height()
    else:
        ax, ay, aw, ah = 0, 0, screen_w, screen_h - px(120)
    w = px(width) if width else window.winfo_reqwidth()
    h = px(height) if height else window.winfo_reqheight()
    w = max(px(320), min(w, aw - px(40) if anchored else aw - px(80), screen_w - px(40)))
    h = max(px(200), min(h, ah - px(24) if anchored else ah, screen_h - px(80)))
    if min_width or min_height:
        window.minsize(min(px(min_width or 0), w), min(px(min_height or 0), h))
    x = max(0, min(ax + max(0, (aw - w) // 2), screen_w - w))
    y = max(0, min(ay + max(0, (ah - h) // 3), screen_h - h))
    window.geometry(f"{w}x{h}+{x}+{y}")


def modal(window, parent, on_close, primary=None, focus=None):
    """Give a Toplevel the standard dialog behaviour: stays over its parent, Esc
    closes, Enter triggers ``primary`` (outside text areas), input is grabbed and
    ``focus`` receives the keyboard."""
    window.transient(parent.winfo_toplevel())
    window.protocol("WM_DELETE_WINDOW", on_close)
    window.bind("<Escape>", lambda event: on_close())
    if primary is not None:
        def submit(event):
            widget = event.widget
            if isinstance(widget, tk.Text): return
            if isinstance(widget, ttk.Button):
                if str(widget.cget("state")) != "disabled": widget.invoke()
            elif primary.winfo_exists() and str(primary.cget("state")) != "disabled":
                primary.invoke()
        window.bind("<Return>", submit)
    try: window.grab_set()
    except tk.TclError: pass
    if focus is not None:
        try: focus.focus_set()
        except tk.TclError: pass


def action_row(parent, primary=None, secondary=(), close=None):
    """Right-aligned dialog buttons in one order: [Primary] [secondary…] [Close].

    Each argument is a (text, command) pair. Returns (frame, buttons) where
    ``buttons`` lists the created buttons in that same visual order."""
    frame = ttk.Frame(parent, style="Card.TFrame")
    buttons = []
    specs = ([("Primary.TButton", primary)] if primary else []) + \
            [("Secondary.TButton", item) for item in secondary] + \
            ([("Secondary.TButton", close)] if close else [])
    for index, (style, (text, command)) in enumerate(reversed(specs)):
        button = ttk.Button(frame, text=text, command=command, style=style)
        button.pack(side="right", padx=(0 if index == len(specs) - 1 else px(8), 0))
        buttons.insert(0, button)
    return frame, buttons


def run_in_dialog(app, window, title, task, done, controls=(), say=None, page="builder",
                  on_error=None, progress=None, after=None, log_as=None):
    """Run ``task`` on the app worker on behalf of a dialog: disable ``controls``,
    announce ``title`` through ``say``, animate ``progress`` and, when the window
    still exists, hand the result to ``done`` or the error text to ``on_error``.
    ``after`` always runs last (use it to re-enable controls). Errors are logged
    under ``log_as`` (a stable prefix engineers can search for) or ``title``."""
    if app.busy: return False
    for widget in controls:
        try: widget.configure(state="disabled")
        except tk.TclError: pass
    if say: say(title)
    if progress is not None:
        progress.pack(fill="x", pady=(0, px(12))); progress.start(12)
    def wrapped():
        try: return task(), None
        except Exception as error: return None, str(error)
    def finished(result):
        if progress is not None and progress.winfo_exists():
            progress.stop(); progress.pack_forget()
        if not window.winfo_exists(): return
        value, error = result
        try:
            if error:
                app.log((log_as or title) + " " + error)
                if on_error: on_error(error)
                elif say: say(error, "danger")
            else: done(value)
        finally:
            if after: after()
    app.work(title, wrapped, finished, page=page)
    return True


class FormDialog:
    """Small modal form. ``fields`` are (key, label, initial, hint) tuples.

    ``validate`` receives the values and returns them (possibly normalised)
    or raises an exception whose text is shown inline."""

    def __init__(self, root, title, subtitle, fields, submit="Save", validate=lambda values: values):
        self.result = None
        self.validate = validate
        self.window = window = tk.Toplevel(root)
        window.title(title); window.resizable(False, False)
        window.configure(bg=COLORS["card"])
        body = ttk.Frame(window, style="Card.TFrame", padding=(space("xl"), px(22), space("xl"), px(20)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text=subtitle, style="CardSubtitle.TLabel", wraplength=px(400), justify="left").pack(
            anchor="w", pady=(px(4), px(18)))
        self.vars = {}
        first = None
        for key, label, initial, hint in fields:
            field_label(body, label)
            self.vars[key] = tk.StringVar(value=initial)
            entry = ttk.Entry(body, textvariable=self.vars[key], width=48)
            entry.pack(fill="x")
            first = first or entry
            ttk.Label(body, text=hint or "", style="Hint.TLabel", wraplength=px(400), justify="left").pack(
                anchor="w", pady=(px(4), px(12)))
        self.error = tk.StringVar()
        self.error_banner = Banner(body, self.error, "danger")
        self.actions, buttons = action_row(body, primary=(submit, self.submit), close=("Cancel", window.destroy))
        self.actions.pack(fill="x", side="bottom", pady=(px(8), 0))
        self.submit_button, self.cancel_button = buttons
        place_dialog(window, root)
        modal(window, root, window.destroy, primary=self.submit_button, focus=first)

    def submit(self):
        values = {key: var.get().strip() for key, var in self.vars.items()}
        try:
            self.result = self.validate(values)
        except Exception as error:
            self.error.set(str(error))
            if not self.error_banner.winfo_manager():
                self.error_banner.pack(fill="x", before=self.actions, pady=(0, px(4)))
            return
        self.window.destroy()

    def show(self):
        self.window.wait_window()
        return self.result
