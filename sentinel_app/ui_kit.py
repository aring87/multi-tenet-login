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


def placeholder(entry, variable, text, search=False):
    """Muted hint drawn inside an entry whenever it is empty, focused or not. The hint
    starts just right of the caret, so the caret stays visible while the box is empty.

    ``search=True`` styles the entry as a search box (Search.TEntry) with a muted
    magnifier glyph before the hint; without the icon font it stays a plain entry."""
    glyph = icon_label(entry, "search", COLORS["card"], COLORS["muted"], font="icon_small") if search else None
    if glyph is not None:
        entry.configure(style="Search.TEntry")
        glyph.configure(cursor="xterm"); glyph.place(x=px(11), rely=.5, anchor="w")
        glyph.bind("<Button-1>", lambda event: entry.focus_set())
    entry.search_glyph = glyph
    hint = tk.Label(entry, text=text, font=FONTS["body"], fg=COLORS["muted"], bg=COLORS["card"], bd=0, cursor="xterm")
    x = px(36 if glyph is not None else 14)
    def refresh(*args):
        try:
            if variable.get(): hint.place_forget()
            elif not hint.winfo_manager(): hint.place(x=x, rely=.5, anchor="w")
        except tk.TclError:
            pass  # The entry is gone; the variable outlived it.
    hint.bind("<Button-1>", lambda event: entry.focus_set())
    for label in (hint, glyph):
        if label is not None: _forward_right_click(label, entry)
    variable.trace_add("write", refresh); refresh()
    return hint


def _forward_right_click(label, target):
    """Pass a right-click on a label drawn over ``target`` (a hint or glyph) on to it,
    so the edit menu opens there as it would on the field's blank part."""
    label.bind("<Button-3>", lambda event: target.event_generate(
        "<Button-3>", x=label.winfo_x() + event.x, y=label.winfo_y() + event.y,
        rootx=event.x_root, rooty=event.y_root))


def link_search(entry, tree):
    """Join a search box to the table it filters: Down or Enter moves to the table's
    first row, and Esc clears the search. Esc in an empty box does nothing here, so a
    dialog's own Esc still closes it."""
    def into(event):
        rows = tree.get_children("")
        if not rows: return None
        tree.focus_set(); tree.focus(rows[0]); tree.selection_set(rows[0]); tree.see(rows[0])
        return "break"
    def clear(event):
        if not entry.get(): return None
        entry.delete(0, "end")
        return "break"
    entry.bind("<Down>", into, add="+"); entry.bind("<Return>", into, add="+")
    entry.bind("<Escape>", clear, add="+")


def debounce(widget, ms, fn):
    """Return a callable that runs ``fn()`` once, ``ms`` milliseconds after it was last
    called (any arguments, such as a variable trace's, are ignored). Typing in a search
    box then filters once the analyst pauses rather than on every key.

    The timer is kept on the root, like every kit timer. The callable also has
    ``cancel()`` (drop a pending run) and ``flush()`` (run a pending call now)."""
    root, pending = widget._root(), {"timer": None}
    def cancel():
        if pending["timer"] is not None:
            try: root.after_cancel(pending["timer"])
            except tk.TclError: pass
            pending["timer"] = None
    def fire():
        pending["timer"] = None
        try: alive = widget.winfo_exists()
        except tk.TclError: alive = False
        if alive: fn()
    def trigger(*args):
        cancel(); pending["timer"] = root.after(ms, fire)
    def flush():
        if pending["timer"] is not None:
            cancel(); fire()
    trigger.cancel, trigger.flush = cancel, flush
    return trigger


def popup_menu(master):
    """A right-click menu in the app's colours, the same look as the client menu on Connect."""
    c = COLORS
    return tk.Menu(master, tearoff=0, font=FONTS["body"], bg=c["card"], fg=c["ink"], bd=0,
                   activebackground=c["accent_soft"], activeforeground=c["ink"], disabledforeground=c["disabled"])


def _show_menu(menu, event):
    try: menu.tk_popup(event.x_root, event.y_root)
    finally: menu.grab_release()


def _editable(widget):
    if isinstance(widget, tk.Text): return str(widget.cget("state")) == "normal"
    return not widget.instate(["disabled"]) and not widget.instate(["readonly"])


def _has_selection(widget):
    if isinstance(widget, tk.Text): return bool(widget.tag_ranges("sel"))
    return bool(widget.selection_present())


def _prepare_edit_menu(menu, widget):
    """Point the shared edit menu at ``widget`` and enable only what applies to it."""
    menu.target = widget
    editable, selected = _editable(widget), _has_selection(widget)
    for index, enabled in ((0, editable and selected), (1, selected), (2, editable)):
        menu.entryconfigure(index, state="normal" if enabled else "disabled")
    return menu


def install_edit_menu(root):
    """Give every Entry, Combobox and Text a right-click menu with Cut, Copy, Paste and
    Select all (Tk has none). Cut and Paste are disabled where the field is read-only or
    disabled, Cut and Copy while nothing is selected. Call once per Tk root."""
    menu = popup_menu(root); menu.target = None
    def send(name):
        widget = menu.target
        if widget is not None and widget.winfo_exists(): widget.event_generate(name)
    for label, name in (("Cut", "<<Cut>>"), ("Copy", "<<Copy>>"), ("Paste", "<<Paste>>")):
        menu.add_command(label=label, command=lambda n=name: send(n))
    menu.add_separator()
    menu.add_command(label="Select all", command=lambda: send("<<SelectAll>>"))
    def show(event):
        try: event.widget.focus_set()
        except tk.TclError: pass
        _show_menu(_prepare_edit_menu(menu, event.widget), event)
    for name in ("TEntry", "TCombobox", "Text"):
        root.bind_class(name, "<Button-3>", show)
    root._kit_edit_menu = menu
    return menu


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

    def set_label(self, value, text):
        """Change one option's caption, for example to add a count: 'Disabled (3)'."""
        self.items[value].configure(text=text)

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
        try:
            if not self.winfo_exists(): return
            self.phase = (self.phase + self.STEP) % 1.0
            self.draw()
            # Scheduled on the root, like every timer in the kit, so a pending callback is owned
            # by one widget only and cancelling it elsewhere never leaves a dangling command here.
            self.timer = self._root().after(self.INTERVAL_MS, self.tick)
        except tk.TclError:
            pass  # The window went away mid-animation.

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
    list in the app alternates row tints without each page having to count. It keeps
    its own row count, so filling a long list costs the same per row however long it
    gets; ``restripe()`` re-tints the rows after they are reordered."""

    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._rows = 0

    def insert(self, parent, index, iid=None, **kw):
        if parent == "" and index == "end" and self._rows % 2:
            tags = kw.get("tags", ())
            kw["tags"] = ((tags,) if isinstance(tags, str) else tuple(tags)) + ("stripe",)
        item = super().insert(parent, index, iid, **kw)
        if parent == "": self._rows += 1
        return item

    def delete(self, *items):
        super().delete(*items)
        self._rows = len(self.get_children(""))

    def restripe(self):
        """Stripe every other top-level row by its current position."""
        rows = self.get_children("")
        for index, item in enumerate(rows):
            tags = tuple(tag for tag in self.tk.splitlist(self.item(item, "tags")) if tag != "stripe")
            self.item(item, tags=tags + (("stripe",) if index % 2 else ()))
        self._rows = len(rows)


# Severity order for sorting: most severe first.
SEVERITY_RANK = {"High": 0, "Medium": 1, "Low": 2, "Informational": 3}
_BLANK = ("", "—")


def sort_value(value):
    """A sort key that orders table cells the way an analyst expects: severities by
    SEVERITY_RANK, numbers by size, anything else as text without regard to case."""
    text = str(value).strip()
    if text in SEVERITY_RANK: return (0, SEVERITY_RANK[text], "")
    try: return (1, float(text.replace(",", "")), "")
    except ValueError: return (2, 0, text.casefold())


def sort_items(tree, key=None, descending=None):
    """Reorder a kit table's top-level rows by column ``key`` using sort_value, keeping
    blank cells last either way, then restripe. With no ``key`` it re-applies the
    table's current ``sort_state``, so a page can call it after redrawing its rows."""
    if key is None:
        if not getattr(tree, "sort_state", None): return
        key, descending = tree.sort_state
    rows = tree.get_children("")
    values = {item: tree.set(item, key) for item in rows}
    filled = [item for item in rows if str(values[item]).strip() not in _BLANK]
    ordered = sorted(filled, key=lambda item: sort_value(values[item]), reverse=bool(descending))
    for index, item in enumerate(ordered + [item for item in rows if str(values[item]).strip() in _BLANK]):
        tree.move(item, "", index)
    tree.restripe()


def set_headings(tree, headings=None):
    """Retitle a kit table's columns from ``{key: title}``. Use it instead of
    ``tree.heading(text=...)``, so the sort arrow stays on the sorted column."""
    tree.base_headings.update(headings or {})
    key, descending = tree.sort_state or (None, False)
    for column, title in tree.base_headings.items():
        tree.heading(column, text=title + ((" ▼" if descending else " ▲") if column == key else ""))


def _images(widget):
    """The per-root image cache: PhotoImages belong to one Tk interpreter."""
    root = widget._root() if widget is not None else tk._default_root
    cache = getattr(root, "_kit_images", None)
    if cache is None: cache = root._kit_images = {}
    return root, cache


def _mix(color, other, amount):
    """``color`` moved ``amount`` (0-1) of the way towards ``other``."""
    a, b = ([int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in (color, other))
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _draw(root, size, paint, samples=4):
    """A size x size PhotoImage drawn pixel by pixel (no image files, so it works in the
    packaged exe). ``paint(x, y)`` gets a point in 0..1 coordinates and returns a colour
    or None; each pixel averages a grid of samples and is transparent where all miss."""
    image = tk.PhotoImage(master=root, width=size, height=size)
    rows, clear = [], []
    for y in range(size):
        row = []
        for x in range(size):
            hits = [paint((x + (i + .5) / samples) / size, (y + (j + .5) / samples) / size)
                    for i in range(samples) for j in range(samples)]
            colors = [hit for hit in hits if hit]
            if not colors:
                row.append("#FFFFFF"); clear.append((x, y)); continue
            mixed = colors[0]
            for count, color in enumerate(colors[1:], start=2):
                mixed = _mix(mixed, color, 1 / count)
            # Edge pixels fade towards the card colour by how much of them the shape covers.
            row.append(_mix(mixed, COLORS["card"], 1 - len(colors) / len(hits)))
        rows.append("{" + " ".join(row) + "}")
    image.put(" ".join(rows))
    for x, y in clear:
        image.transparency_set(x, y, True)
    return image


def dot(tone, master=None):
    """A round status dot (danger, warning, info, success or neutral) for a table's
    marker column: ``tree.insert(..., image=kit.dot("danger", tree))``. Drawn once per
    root and cached."""
    root, cache = _images(master)
    if ("dot", tone) not in cache:
        color = COLORS["subtle"] if tone == "neutral" else TONES.get(tone, TONES["neutral"])[0]
        cache["dot", tone] = _draw(root, px(10), lambda x, y: color if (x - .5) ** 2 + (y - .5) ** 2 <= .25 else None)
    return cache["dot", tone]


def _segment_distance(x, y, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


def checkbox(state, master=None):
    """A 14px checkbox image for a table's check column: 'off' (an empty box), 'on'
    (accent fill with a white tick) or 'disabled' (a greyed box). Cached per root."""
    root, cache = _images(master)
    if ("check", state) not in cache:
        size = px(14)
        border, fill = {"on": (COLORS["accent"], COLORS["accent"]),
                        "disabled": (COLORS["line"], COLORS["raised"])}.get(state, (COLORS["line_strong"], COLORS["card"]))
        edge, radius = 1 / size, 2.5 / 14
        tick = ((3.6 / 14, 7.3 / 14), (6.0 / 14, 9.7 / 14), (10.6 / 14, 4.6 / 14))
        def paint(x, y):
            # Signed distance to a rounded square filling the image (negative inside).
            qx, qy = abs(x - .5) - (.5 - radius), abs(y - .5) - (.5 - radius)
            distance = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
            if distance > 0: return None
            if state == "on" and min(_segment_distance(x, y, tick[i], tick[i + 1]) for i in (0, 1)) <= .9 / 14:
                return "#FFFFFF"
            return border if distance > -edge else fill
        cache["check", state] = _draw(root, size, paint)
    return cache["check", state]


def blank_image(master):
    """A transparent 1px image. A Label showing it with compound='center' takes its
    width and height in pixels instead of characters."""
    root, cache = _images(master)
    if "blank" not in cache:
        cache["blank"] = tk.PhotoImage(master=root, width=1, height=1)
        cache["blank"].transparency_set(0, 0, True)
    return cache["blank"]


def copy_rows(tree):
    """Copy the selected rows to the clipboard, one tab-separated line per row."""
    rows = tree.selection()
    if rows:
        tree.clipboard_clear()
        tree.clipboard_append("\n".join("\t".join(str(value) for value in tree.item(item, "values")) for item in rows))
    return "break"


def _focus_row(tree):
    """Give a table a focus row when it gets the keyboard, so the arrow keys work at once
    (Tk ignores them while no row has focus): the selected row, or else the first."""
    rows = tree.get_children("")
    if tree.focus() or not rows: return
    selection = tree.selection()
    item = selection[0] if selection else rows[0]
    tree.focus(item)
    if not selection: tree.selection_set(item)
    tree.see(item)


def table(parent, columns, height=10, selectmode="browse", icon="rules", sort=None, menu=None, marker=False,
          check=False):
    """A bordered Treeview with striped rows, slim scrollbars and an empty-state overlay.

    ``columns`` holds (key, heading, width[, stretch]) tuples.

    ``sort`` makes the headings clickable: a callable is given (key, descending) and
    redraws the rows in that order; True sorts the rows already shown (sort_items). The
    state is kept as ``tree.sort_state`` ((key, descending) or None) and the sorted
    heading shows an arrow. ``menu(iid)`` returns extra (label, command[, enabled])
    entries, or None for a separator, for a row's right-click menu after Copy.
    ``marker`` adds a narrow first column for status dots (``kit.dot``) and ``check``
    one for checkboxes (``kit.checkbox``); pass the image when inserting a row.

    The table shows a focus ring while it has the keyboard, gives itself a focus row
    when focused from the keyboard so the arrow keys work straight away (a mouse press
    selects only the row it lands on), and copies the selected rows with Ctrl+C."""
    frame = tk.Frame(parent, bg=COLORS["card"], bd=0, highlightthickness=1,
                     highlightbackground=COLORS["line"], highlightcolor=COLORS["line"])
    lead = marker or check
    tree = StripedTreeview(frame, columns=[key for key, *_ in columns], show="tree headings" if lead else "headings",
                           height=height, selectmode=selectmode, style="Lead.Treeview" if lead else "Treeview")
    if lead:
        tree.column("#0", width=px(28), minwidth=px(28), stretch=False, anchor="center")
        tree.heading("#0", text="")
    tree.base_headings, tree.sort_state = {}, None
    for key, title, width, *stretch in columns:
        tree.base_headings[key] = title
        tree.heading(key, text=title, anchor="w")
        tree.column(key, width=px(width), minwidth=px(70), anchor="w", stretch=bool(stretch[0]) if stretch else True)
    if sort:
        reorder = (lambda key, descending: sort_items(tree, key, descending)) if sort is True else sort
        def toggle(key):
            descending = tree.sort_state is not None and tree.sort_state[0] == key and not tree.sort_state[1]
            tree.sort_state = (key, descending)
            set_headings(tree)
            reorder(key, descending)
        for key in tree.base_headings:
            tree.heading(key, command=lambda k=key: toggle(k))
        tree.toggle_sort = toggle
    def ring(on):
        frame.configure(highlightbackground=COLORS["accent"] if on else COLORS["line"])
    # Only keyboard focus picks a focus row: a press on a heading or the blank area
    # below the rows also focuses the table, and must not select (and open) row 0.
    # The press binding runs before the class binding that queues the FocusIn.
    tree._mouse_focus = False
    tree.bind("<ButtonPress-1>", lambda event: setattr(tree, "_mouse_focus", True), add="+")
    tree.bind("<ButtonRelease-1>", lambda event: setattr(tree, "_mouse_focus", False), add="+")
    tree.bind("<FocusIn>", lambda event: (ring(True), tree._mouse_focus or _focus_row(tree)), add="+")
    tree.bind("<FocusOut>", lambda event: ring(False), add="+")
    tree.bind("<Control-c>", lambda event: copy_rows(tree), add="+")
    row_menu = popup_menu(tree)
    def build_menu(item):
        row_menu.delete(0, "end")
        row_menu.add_command(label="Copy", accelerator="Ctrl+C", command=lambda: copy_rows(tree))
        extra = list(menu(item) or ()) if menu else []
        if extra: row_menu.add_separator()
        for entry in extra:
            if entry is None: row_menu.add_separator(); continue
            label, command, *enabled = entry
            row_menu.add_command(label=label, command=command,
                                 state="normal" if not enabled or enabled[0] else "disabled")
        return row_menu
    def right_click(event):
        item = tree.identify_row(event.y)
        if not item: return None
        if item not in tree.selection(): tree.selection_set(item)
        tree.focus(item); tree.focus_set()
        _show_menu(build_menu(item), event)
        return "break"
    tree.build_menu = build_menu
    tree.bind("<Button-3>", right_click, add="+")
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


def _focus_step(widget, step):
    target = widget.tk_focusNext() if step > 0 else widget.tk_focusPrev()
    if target is not None: target.focus_set()
    return "break"


def _indent(text):
    if str(text.cget("state")) == "normal":
        try: text.tk.call("tk::TextInsert", text._w, "    ")  # Replaces a selection, as typing does.
        except tk.TclError: text.insert("insert", "    ")
        text.see("insert")
    return "break"


def text_area(parent, height, readonly=False, wrap="word", undo=False, font="mono", placeholder=None, tab=None):
    """A Text widget with a slim scrollbar inside a bordered frame.

    ``placeholder`` is muted text shown while the area is empty; write through
    ``set_text`` (or call ``refresh_placeholder``) so it comes and goes.

    ``tab`` sets what Tab does. 'focus' moves to the next control and Shift+Tab to the
    previous one; 'indent' inserts four spaces, and Ctrl+Tab (or Shift+Tab back) leaves
    the area. By default prose areas (font='body') use 'focus' and editable code areas
    'indent'; read-only areas keep Tk's behaviour, which already moves on."""
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
    if tab is None:
        tab = "focus" if font == "body" else None if readonly else "indent"
    if tab == "focus":
        text.bind("<Tab>", lambda event: _focus_step(event.widget, 1))
        text.bind("<Shift-Tab>", lambda event: _focus_step(event.widget, -1))
    elif tab == "indent":
        text.bind("<Tab>", lambda event: _indent(event.widget))
        for sequence, step in (("<Control-Tab>", 1), ("<Control-Shift-Tab>", -1), ("<Shift-Tab>", -1)):
            text.bind(sequence, lambda event, s=step: _focus_step(event.widget, s))
    if readonly:
        text.configure(state="disabled", bg=COLORS["raised"])
        frame.configure(bg=COLORS["raised"])
    if placeholder:
        text.placeholder_label = tk.Label(text, text=placeholder, font=FONTS["body"], fg=COLORS["muted"],
                                          bg=COLORS["raised"] if readonly else COLORS["card"], bd=0,
                                          justify="left", anchor="nw", wraplength=px(440))
        text.placeholder_label.bind("<Button-1>", lambda event: text.focus_set())
        _forward_right_click(text.placeholder_label, text)
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
    Rows are Tab stops with a focus ring; Up/Down move between them.

    ``set_status`` marks a step done (a green tick) or in error (a red !) with an
    optional note in place of its description; the current step keeps its accent
    number either way."""

    def __init__(self, parent, notebook, steps, enabled=lambda: True):
        super().__init__(parent, style="Card.TFrame")
        self.notebook, self.enabled, self.rows = notebook, enabled, []
        self.descriptions, self.status = [description for _, description in steps], {}
        for index, (title, description) in enumerate(steps):
            row = tk.Frame(self, bg=COLORS["card"], cursor="hand2", bd=0, takefocus=1, highlightthickness=1,
                           highlightbackground=COLORS["card"], highlightcolor=COLORS["accent"])
            row.pack(fill="x", pady=(0, px(2)))
            # Sized in pixels (through a blank image), so a tick or ! in another font keeps the same square.
            marker = tk.Label(row, text=str(index + 1), font=FONTS["strong_small"], image=blank_image(self),
                              compound="center", width=px(22), height=px(22), bd=0, padx=0, pady=0)
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

    def set_status(self, index, status, note=None):
        """Mark step ``index`` as 'done' or 'error', or clear it with None. ``note``
        (e.g. '2 issues') replaces the step's description while the status lasts."""
        if status is None: self.status.pop(index, None)
        else: self.status[index] = (status, note)
        self.render()

    def clear_status(self):
        """Return every step to its plain number and description."""
        self.status.clear()
        self.render()

    def paint(self, index, background, selected):
        row, marker, name, detail = self.rows[index]
        active = self.enabled()
        status, note = self.status.get(index, (None, None))
        for part in (row, name, detail):
            part.configure(bg=background)
        row.configure(highlightbackground=background, cursor="hand2" if active else "arrow")
        name.configure(fg=(COLORS["ink"] if selected else COLORS["ink_2"]) if active else COLORS["disabled"])
        detail.configure(text=note or self.descriptions[index],
                         fg=COLORS["disabled"] if not active else COLORS["danger"] if status == "error" and note
                         else COLORS["muted"])
        if selected:
            marker.configure(text=str(index + 1), font=FONTS["strong_small"], bg=COLORS["accent"], fg="#FFFFFF")
        elif status == "done":
            glyph = FONTS.get("icon_small")
            marker.configure(text=ICONS["check"] if glyph else "✓", font=glyph or FONTS["strong_small"],
                             bg=COLORS["success_soft"], fg=COLORS["success"])
        elif status == "error":
            marker.configure(text="!", font=FONTS["strong_small"], bg=COLORS["danger_soft"], fg=COLORS["danger"])
        else:
            marker.configure(text=str(index + 1), font=FONTS["strong_small"], bg=COLORS["hover"],
                             fg=COLORS["muted"] if active else COLORS["disabled"])

    def refresh(self):
        self.render()

    def render(self):
        current = self.notebook.index("current") if self.notebook.tabs() else 0
        for index in range(len(self.rows)):
            self.paint(index, COLORS["accent_soft"] if index == current else COLORS["card"], index == current)


# ------------------------------------------------------------------ dialogs
def place_dialog(window, parent, width=None, height=None, min_width=None, min_height=None):
    """Size a Toplevel in scaled pixels, clamp it to the parent and screen, and centre it.
    A dimension left out is measured from the content, and a measured height is never
    padded, so a short message box has no empty band under its buttons."""
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
    h = max(px(200) if height else 0, min(h, ah - px(24) if anchored else ah, screen_h - px(80)))
    if min_width or min_height:
        window.minsize(min(px(min_width or 0), w), min(px(min_height or 0), h))
    x = max(0, min(ax + max(0, (aw - w) // 2), screen_w - w))
    y = max(0, min(ay + max(0, (ah - h) // 3), screen_h - h))
    window.geometry(f"{w}x{h}+{x}+{y}")


def _enter_handler(window, primary):
    """The dialog's Enter key: a focused button presses itself; elsewhere (outside text
    areas) Enter presses the promoted button, or else ``primary``."""
    def submit(event):
        widget = event.widget
        if isinstance(widget, tk.Text): return
        if isinstance(widget, ttk.Button):
            if str(widget.cget("state")) != "disabled": widget.invoke()
            return
        target = getattr(window, "_enter_target", primary)
        if target is not None and target.winfo_exists() and str(target.cget("state")) != "disabled":
            target.invoke()
    return submit


def modal(window, parent, on_close, primary=None, focus=None):
    """Give a Toplevel the standard dialog behaviour: stays over its parent, Esc
    closes, Enter triggers the dialog's blue button (outside text areas), input is
    grabbed and ``focus`` receives the keyboard.

    With a ``primary`` (or once ``promote`` has run), Enter on a focused button presses
    that button and elsewhere presses the button last given to ``promote`` (nothing
    after ``promote(window, None, ...)``), or else ``primary``. A dialog with neither
    leaves Enter alone, so Enter on a focused button does nothing there."""
    window.transient(parent.winfo_toplevel())
    window.protocol("WM_DELETE_WINDOW", on_close)
    window.bind("<Escape>", lambda event: on_close())
    window._kit_submit = _enter_handler(window, primary)
    if primary is not None: window.bind("<Return>", window._kit_submit)
    try: window.grab_set()
    except tk.TclError: pass
    if focus is not None:
        try: focus.focus_set()
        except tk.TclError: pass


def _restyle(button, kind):
    small = str(button.cget("style")).startswith("Small.")
    button.configure(style=("Small." if small else "") + kind + ".TButton")


def promote(window, active, *others):
    """Make ``active`` the dialog's next step: it turns blue (Primary), ``others`` turn
    Secondary, and Enter in the dialog presses ``active`` from now on. Call it as a
    dialog moves on (for example Check, then Submit) so one button leads at a time.
    With ``active`` None no button leads and Enter presses nothing."""
    if active is not None: _restyle(active, "Primary")
    for button in others:
        _restyle(button, "Secondary")
    window._enter_target = active
    # A dialog made without a primary has no Enter binding yet; promoting gives it one.
    if not window.bind("<Return>"):
        window.bind("<Return>", getattr(window, "_kit_submit", None) or _enter_handler(window, None))


def action_row(parent, primary=None, secondary=(), close=None, aux=()):
    """Right-aligned dialog buttons in one order: [Primary] [secondary…] [Close], and
    ``aux`` helpers (Open in GitHub, Reload…) as ghost buttons at the left.

    Each argument is a (text, command) pair. Returns (frame, buttons) where
    ``buttons`` lists the right-hand buttons in that same visual order, then the aux
    buttons from left to right."""
    frame = ttk.Frame(parent, style="Card.TFrame")
    buttons = []
    specs = ([("Primary.TButton", primary)] if primary else []) + \
            [("Secondary.TButton", item) for item in secondary] + \
            ([("Secondary.TButton", close)] if close else [])
    for index, (style, (text, command)) in enumerate(reversed(specs)):
        button = ttk.Button(frame, text=text, command=command, style=style)
        button.pack(side="right", padx=(0 if index == len(specs) - 1 else px(8), 0))
        buttons.insert(0, button)
    for index, (text, command) in enumerate(aux):
        button = ttk.Button(frame, text=text, command=command, style="Ghost.TButton")
        button.pack(side="left", padx=(0 if index == 0 else px(4), 0))
        buttons.append(button)
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


class FieldError(Exception):
    """A validation error that belongs to one form field. FormDialog shows the message
    under the field named ``key`` (and outlines it in red) instead of in its banner."""

    def __init__(self, key, message):
        super().__init__(message)
        self.key = key


class FormDialog:
    """Small modal form. ``fields`` are (key, label, initial, hint) tuples.

    ``validate`` receives the values and returns them (possibly normalised) or
    raises. A FieldError is shown under its field, which turns red until it is
    edited; any other error is shown in a banner above the buttons, and the window
    grows to fit it."""

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
        self.vars, self.entries, self.hints, self.hint_texts = {}, {}, {}, {}
        first = None
        for key, label, initial, hint in fields:
            field_label(body, label)
            self.vars[key] = tk.StringVar(value=initial)
            entry = self.entries[key] = ttk.Entry(body, textvariable=self.vars[key], width=48)
            entry.pack(fill="x")
            first = first or entry
            self.hint_texts[key] = hint or ""
            self.hints[key] = ttk.Label(body, text=hint or "", style="Hint.TLabel", wraplength=px(400), justify="left")
            self.hints[key].pack(anchor="w", pady=(px(4), px(12)))
            self.vars[key].trace_add("write", lambda *args, k=key: self.clear_error(k))
        self.error = tk.StringVar()
        self.error_banner = Banner(body, self.error, "danger")
        self.actions, buttons = action_row(body, primary=(submit, self.submit), close=("Cancel", window.destroy))
        self.actions.pack(fill="x", side="bottom", pady=(px(8), 0))
        self.submit_button, self.cancel_button = buttons
        place_dialog(window, root)
        modal(window, root, window.destroy, primary=self.submit_button, focus=first)

    def field_error(self, key, message):
        """Flag one field: a red outline and ``message`` in place of its hint."""
        self.entries[key].configure(style="Error.TEntry")
        self.hints[key].configure(text=message, style="ErrorHint.TLabel")
        self.entries[key].focus_set()

    def clear_error(self, key):
        """Return a flagged field to its normal outline and hint (it is being edited)."""
        try:
            self.entries[key].configure(style="TEntry")
            self.hints[key].configure(text=self.hint_texts[key], style="Hint.TLabel")
        except tk.TclError:
            pass  # The dialog has closed.

    def fit(self):
        """Grow or shrink the window to its content's height, keeping its width, so a
        message never pushes the buttons out of view."""
        window = self.window
        window.update_idletasks()
        self.error_banner.rewrap()  # Wrap to the banner's real width before measuring.
        window.update_idletasks()
        width = window.winfo_width() if window.winfo_ismapped() else window.winfo_reqwidth()
        window.geometry(f"{width}x{window.winfo_reqheight()}")

    def submit(self):
        values = {key: var.get().strip() for key, var in self.vars.items()}
        try:
            self.result = self.validate(values)
        except Exception as error:
            if isinstance(error, FieldError) and error.key in self.entries:
                self.error.set(""); self.error_banner.pack_forget()
                self.field_error(error.key, str(error))
            else:
                self.error.set(str(error))
                if not self.error_banner.winfo_manager():
                    self.error_banner.pack(fill="x", before=self.actions, pady=(0, px(4)))
            self.fit()
            return
        self.window.destroy()

    def show(self):
        self.window.wait_window()
        return self.result


class MessageDialog:
    """A themed message box: an icon tile, a title, the message, optional ``details``
    in a read-only box with Copy details, and an OK button (plus ``cancel`` when
    given). ``result`` is True once OK is chosen. ``alert`` and ``confirm`` wrap it."""

    def __init__(self, parent, title, message, details=None, tone="info", icon=None, ok="OK", cancel=None,
                 destructive=False):
        self.result, self.details = False, details
        self.window = window = tk.Toplevel(parent)
        window.title(title); window.resizable(False, False)
        window.configure(bg=COLORS["card"])
        body = ttk.Frame(window, style="Card.TFrame", padding=(space("xl"), px(22), space("xl"), px(20)))
        body.pack(fill="both", expand=True)
        head = ttk.Frame(body, style="Card.TFrame"); head.pack(fill="x")
        head.columnconfigure(1, weight=1)
        icon = icon or {"danger": "warning", "warning": "warning", "success": "check"}.get(tone, "info")
        icon_tile(head, icon, tone).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, px(14)))
        wrap = px(480) - 2 * space("xl") - px(48)
        ttk.Label(head, text=title, style="CardTitle.TLabel", wraplength=wrap, justify="left").grid(
            row=0, column=1, sticky="w")
        ttk.Label(head, text=message, style="Body.TLabel", wraplength=wrap, justify="left").grid(
            row=1, column=1, sticky="w", pady=(px(4), 0))
        self.text = None
        if details:
            frame, self.text = text_area(body, 6, readonly=True)
            set_text(self.text, details)
            frame.pack(fill="both", expand=True, pady=(px(14), 0))
        self.actions, buttons = action_row(body, primary=(ok, self.accept),
                                           secondary=(("Copy details", self.copy),) if details else (),
                                           close=(cancel, self.close) if cancel else None)
        self.actions.pack(fill="x", pady=(px(20), 0))
        self.ok_button = buttons[0]
        self.copy_button = buttons[1] if details else None
        self.cancel_button = buttons[-1] if cancel else None
        if destructive: self.ok_button.configure(style="Danger.TButton")
        place_dialog(window, parent, width=480)
        # A destructive choice starts on Cancel, so Enter alone never deletes anything.
        modal(window, parent, self.close, primary=self.ok_button,
              focus=self.cancel_button if destructive and self.cancel_button else self.ok_button)

    def accept(self):
        self.result = True
        self.window.destroy()

    def close(self):
        self.window.destroy()

    def copy(self):
        self.window.clipboard_clear(); self.window.clipboard_append(self.details)
        self.copy_button.configure(text="Copied")

    def show(self):
        self.window.wait_window()
        return self.result


def alert(parent, title, message, details=None, tone="danger"):
    """Tell the analyst something in a themed dialog and wait until they close it.
    ``details`` (the full error text, say) go in a read-only box with Copy details;
    ``tone`` (danger, warning, info or success) sets the icon. Returns None."""
    MessageDialog(parent, title, message, details=details, tone=tone).show()


def confirm(parent, title, message, ok="OK", cancel="Cancel", destructive=False):
    """Ask a question in a themed dialog and wait for the answer: True only when ``ok``
    is chosen (Cancel, Esc and closing the window give False). ``destructive`` paints
    ``ok`` red and starts with the keyboard on ``cancel``."""
    return MessageDialog(parent, title, message, tone="danger" if destructive else "info",
                         icon="warning" if destructive else "help", ok=ok, cancel=cancel,
                         destructive=destructive).show()


class ScrollArea:
    """A vertically scrolling region for a detail pane: place ``frame`` and fill
    ``inner``. The inner frame always matches the visible width, the scrollbar shows
    only while the content overflows, and the mouse wheel scrolls the area while the
    pointer is over it. At either end, or when everything fits, the wheel passes on to
    the page; a table or text area inside scrolls itself first."""

    NOTCH = 120  # Wheel delta per notch on Windows; each notch scrolls three increments.

    def __init__(self, parent, background=None):
        background = background or COLORS["card"]
        self.frame = tk.Frame(parent, bg=background, bd=0, highlightthickness=0)
        self.canvas = tk.Canvas(self.frame, bg=background, highlightthickness=0, bd=0, yscrollincrement=px(16))
        self.scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview,
                                       style="Canvas.Vertical.TScrollbar" if background == COLORS["canvas"]
                                       else "Vertical.TScrollbar")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=autohide(
            self.scrollbar, lambda: self.scrollbar.pack(side="right", fill="y", before=self.canvas)))
        self.inner = ttk.Frame(self.canvas, style="TFrame" if background == COLORS["canvas"] else "Card.TFrame")
        self.item = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda event: self.changed(), add="+")
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.item, width=event.width), add="+")
        self.canvas.bind("<Enter>", lambda event: self.retag(), add="+")
        self.tag, self.carry = "KitScrollArea%d" % id(self), 0
        self.frame.bind_class(self.tag, "<MouseWheel>", self.wheel)
        self.frame.bind("<Destroy>", lambda event: event.widget is self.frame and self.frame.unbind_class(
            self.tag, "<MouseWheel>"), add="+")
        self.frame.scroll_area = self
        self.retag()

    def changed(self):
        """The content changed size: update the scroll range and route the wheel again."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.retag()

    def retag(self):
        """Route the wheel over every widget inside the area (call it after adding
        content without resizing the area; entering the area does it too)."""
        pending = [self.canvas]
        while pending:
            widget = pending.pop()
            tags = widget.bindtags()
            if self.tag not in tags: widget.bindtags((self.tag,) + tags)
            pending.extend(widget.winfo_children())

    @staticmethod
    def _can_scroll(widget, delta):
        first, last = widget.yview()
        return not ((first <= 0 and delta > 0) or (last >= 1 and delta < 0))

    def wheel(self, event):
        """Scroll for a wheel event over the area; returns 'break' when it scrolled."""
        widget = event.widget
        if isinstance(widget, str) or widget.winfo_class() == "TCombobox": return None
        # A table or text area under the pointer scrolls itself while it still can.
        if widget.winfo_class() in ("Text", "Treeview", "Listbox") and self._can_scroll(widget, event.delta): return None
        # Only the innermost area acts; it hands the wheel outwards once it reaches an end.
        areas, parent = [], widget
        while parent is not None:
            if getattr(parent, "scroll_area", None) is not None: areas.append(parent.scroll_area)
            parent = parent.master
        if not areas or areas[0] is not self: return None
        area = next((area for area in areas if area._can_scroll(area.canvas, event.delta)), None)
        if area is None: return None
        # Touchpads send many small deltas: add them up and scroll whole increments.
        area.carry += event.delta
        steps = int(area.carry * 3 / self.NOTCH)
        if steps:
            area.carry -= steps * self.NOTCH / 3
            area.canvas.yview_scroll(-steps, "units")
        return "break"
