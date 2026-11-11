"""The Rule builder's Query box as an editor: a colour for each part of the query, a list of
completions while typing, and the check's suggested spellings put in on request.

kql_check and kql_assist decide what to show; this draws it on the kit's text area. The colour
schemes are desktop_theme.KQL_THEMES.

Keys, while the list is open: Up/Down (Page Up/Down) choose, Tab inserts, Esc closes. Enter inserts
only once a choice was made with the arrows, or for a step being typed after a |; otherwise it starts a
new line as usual, so a finished word is never replaced by surprise. Ctrl+Space opens the list
anywhere, and Ctrl+. puts in the suggested spelling at the caret."""
import tkinter as tk
from tkinter import ttk

from . import kql_assist as assist
from . import kql_check
from . import ui_kit as kit
from .desktop_theme import FONTS, KQL_THEME, KQL_THEMES, px

SYNTAX = tuple("syntax-" + kind for kind in assist.KINDS)
MARKS = ("kql-warning", "kql-error")  # Drawn over the colours; an error over a warning.
ROWS = 8      # Choices in view at once.
WIDEST = 46   # Characters of a choice shown before it is cut short.
# Typed letters before the list opens by itself: none straight after a |, where only a step fits.
NEEDED = {"step": 0, "kind": 0, "type": 1, "table": 2, "value": 2}
HINT = "Tab inserts  ·  Esc closes"


def theme_name(name):
    """``name`` when it is a colour scheme, else the default (a saved choice may be from a newer version)."""
    return name if name in KQL_THEMES else KQL_THEME


def fix_label(fixes):
    """What the link under the Query box says for the check's suggested spellings."""
    if not fixes: return ""
    return "Change to " + fixes[0].fix.strip() if len(fixes) == 1 else f"Fix all {len(fixes)}"


class KqlEditor:
    def __init__(self, text, theme=None):
        self.text, self.root = text, text._root()
        self.theme = None
        self.report = None  # The last check of a query that isn't empty.
        self.changed = None  # Called after a suggested spelling is put in (the builder re-reads the query).
        # The open list: what it offers, the row chosen, whether the arrows chose it, whether Ctrl+Space opened it.
        self.offer, self.chosen, self.navigated, self.forced = None, 0, False, False
        self.popup = self.list = self.hint = None
        self.pending = None
        for tag in SYNTAX: text.tag_configure(tag)
        for tag in MARKS: text.tag_configure(tag, underline=True)
        for tag in SYNTAX: text.tag_lower(tag)
        text.tag_raise("kql-error"); text.tag_raise("sel")
        # A bind tag ahead of the widget's own, so these keys reach the list first and fall through
        # (Tab to the kit's indent, Enter to a new line) whenever it has no use for them.
        tag = self.tag = "KqlEditor" + str(id(self))
        text.bindtags((tag,) + text.bindtags())
        bind = lambda sequence, handler: text.bind_class(tag, sequence, handler)
        bind("<KeyPress>", self.key)
        bind("<Tab>", lambda event: self.accept() if self.offer else None)
        bind("<Return>", self.enter)
        bind("<Shift-Return>", lambda event: self.hide())  # Run test query, with the list out of the way.
        bind("<Escape>", lambda event: self.hide() or "break" if self.offer else None)
        for sequence, step in (("<Up>", -1), ("<Down>", 1), ("<Prior>", -(ROWS - 1)), ("<Next>", ROWS - 1)):
            bind(sequence, lambda event, s=step: self.move(s) if self.offer else None)
        bind("<Control-space>", lambda event: self.suggest(force=True) or "break")
        bind("<Control-period>", lambda event: self.fix_here() or "break")
        bind("<Button-3>", self.right_click)
        for sequence in ("<Button-1>", "<FocusOut>", "<MouseWheel>", "<Destroy>"):
            bind(sequence, lambda event: self.hide())
        # The list is drawn over the window, so it also goes when the window is resized or the page under it
        # changes. Windows also sends <Configure> when a window is only raised or moved; the list moves with it.
        self.size = None
        self.root.bind("<Configure>", self.resized, add="+")
        parent = text.master
        while parent is not None:
            if isinstance(parent, ttk.Notebook): parent.bind("<<NotebookTabChanged>>", lambda event: self.hide(), add="+")
            parent = parent.master
        self.set_theme(theme)

    # ------------------------------------------------------------ colours
    def set_theme(self, name):
        """Draw the box, its marks and the list in the colour scheme ``name`` (the default when unknown)."""
        name = theme_name(name)
        if name == self.theme: return
        self.theme, text, c = name, self.text, KQL_THEMES[name]
        text.configure(bg=c["background"], fg=c["ink"], insertbackground=c["caret"], selectbackground=c["selection"],
                       selectforeground=c["ink"], inactiveselectbackground=c["selection_inactive"])
        text.master.configure(bg=c["background"])
        label = getattr(text, "placeholder_label", None)
        if label is not None: label.configure(bg=c["background"], fg=c["muted"])
        family, size = FONTS["mono"][:2]
        for kind, tag in zip(assist.KINDS, SYNTAX):
            style = {"comment": "italic", "step": "bold"}.get(kind)
            text.tag_configure(tag, foreground=c[kind], font=(family, size, style) if style else (family, size))
        for tag, tone in (("kql-error", "error"), ("kql-warning", "warning")):
            text.tag_configure(tag, background=c[tone + "_soft"])
            try: text.tag_configure(tag, underlinefg=c[tone])
            except tk.TclError: pass  # Tk before 8.6.6 underlines in the text's own colour.
        # The scrollbar the kit put beside the text takes the box's colours too.
        style, bar = ttk.Style(self.root), "Kql.Vertical.TScrollbar"
        style.layout(bar, style.layout("Vertical.TScrollbar"))
        style.configure(bar, troughcolor=c["background"], bordercolor=c["background"], background=c["thumb"],
                        lightcolor=c["thumb"], darkcolor=c["thumb"])
        for option in ("background", "lightcolor", "darkcolor"):
            style.map(bar, **{option: [("pressed", c["thumb_active"]), ("active", c["thumb_active"])]})
        for child in text.master.winfo_children():
            if isinstance(child, ttk.Scrollbar): child.configure(style=bar)
        self.hide()
        if self.popup is not None: self.popup.destroy(); self.popup = self.list = self.hint = None

    def paint(self):
        """Colour the query as it is now and return its check (None while the box is empty)."""
        text = self.text
        query = text.get("1.0", "end-1c")
        for tag in SYNTAX: text.tag_remove(tag, "1.0", "end")
        if not query.strip(): return None
        report = kql_check.check(query)
        place = kql_check._Source(report.text).position
        for kind, start, end in assist.spans(report):
            text.tag_add("syntax-" + kind, "%d.%d" % place(start), "%d.%d" % place(end))
        return report

    def check(self):
        """Colour the query and mark each problem the check finds in it. Returns the check, or None
        while the box is empty."""
        text = self.text
        for tag in MARKS: text.tag_remove(tag, "1.0", "end")
        report = self.report = self.paint()
        for issue in report.issues if report else ():
            start = f"{issue.line}.{issue.column}"
            text.tag_add("kql-" + issue.severity, start, f"{start}+{issue.length}c")
        return report

    # ------------------------------------------------------------ suggested spellings
    def fixes(self):
        """The marked problems whose suggested spelling can still be put in, in query order: those the
        last check found, where the query hasn't changed since."""
        if self.report is None or str(self.text.cget("state")) != "normal": return []
        lines = self.report.text.split("\n")
        return [issue for issue in self.report.fixes
                if self.text.get(*self.span(issue)) == lines[issue.line - 1][issue.column:issue.column + issue.length]]

    @staticmethod
    def span(issue):
        start = f"{issue.line}.{issue.column}"
        return start, f"{start}+{issue.length}c"

    def fix_at(self, index):
        """The fixable problem under the text position ``index``, or None."""
        text = self.text
        return next((issue for issue in self.fixes() if text.compare(self.span(issue)[0], "<=", index)
                     and text.compare(index, "<=", self.span(issue)[1])), None)

    def apply(self, issues):
        """Write each issue's suggested spelling over it, as one step for Ctrl+Z. Returns how many."""
        text, issues = self.text, [issue for issue in issues if issue in self.fixes()]
        if not issues: return 0
        self.hide()
        automatic = text.cget("autoseparators")
        text.configure(autoseparators=False); text.edit_separator()
        try:
            for issue in sorted(issues, key=lambda issue: (issue.line, issue.column), reverse=True):
                start, end = self.span(issue)
                text.delete(start, end); text.insert(start, issue.fix)
        finally:
            text.edit_separator(); text.configure(autoseparators=automatic)
        (self.changed or self.check)()
        return len(issues)

    def fix_here(self):
        """Ctrl+.: put in the suggested spelling at the caret, or the first one on its line."""
        text = self.text
        issue = self.fix_at("insert") or next(
            (issue for issue in self.fixes() if issue.line == int(text.index("insert").split(".")[0])), None)
        return self.apply([issue]) if issue else 0

    def right_click(self, event):
        """On a marked word with a suggested spelling, offer it above the usual Cut, Copy and Paste."""
        issue = self.fix_at(self.text.index(f"@{event.x},{event.y}"))
        if issue is None: return None  # The kit's own edit menu.
        self.hide()
        kit.show_edit_menu(self.text, event, [("Change to " + issue.fix.strip(), lambda: self.apply([issue]))])
        return "break"

    # ------------------------------------------------------------ completions
    def key(self, event):
        """After a key that changes the query, look again at what could be written at the caret."""
        if event.keysym in ("BackSpace", "Delete"):
            if self.offer: self.later()
        elif event.char and event.char.isprintable():  # Ctrl+V and Ctrl+Z send control characters, not these.
            self.later()
        elif event.keysym not in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Caps_Lock"):
            self.hide()  # The caret moved away (arrows, Home, End) or something else happened.

    def later(self):
        """Suggest once the key has been written into the text (the class binding runs after this one)."""
        if self.pending is None: self.pending = self.root.after_idle(self.suggest)

    def suggest(self, force=False):
        """Open, update or close the list for the caret's position. ``force`` (Ctrl+Space) opens it
        whatever has been typed."""
        self.pending = None
        text = self.text
        if not text.winfo_exists(): return
        if str(text.cget("state")) != "normal" or text.tag_ranges("sel"): return self.hide()
        offer = assist.complete(text.get("1.0", "end-1c"), len(text.get("1.0", "insert")))
        if offer is None: return self.hide()
        # An open list stays while its word is still being typed; a closed one waits for enough of a word.
        same = self.offer is not None and self.offer.start == offer.start
        if not (force or (same and (offer.word or self.forced)) or len(offer.word) >= NEEDED.get(offer.context, 2)):
            return self.hide()
        before = self.offer.choices[self.chosen] if same and self.navigated else None
        if not same: self.forced = False
        self.forced = self.forced or force
        self.chosen = offer.choices.index(before) if before in offer.choices else 0  # A row picked with the arrows stays picked.
        self.navigated = before in offer.choices
        self.offer = offer
        self.show()

    def build(self):
        c = KQL_THEMES[self.theme]
        self.popup = tk.Frame(self.root, bg=c["popup_line"], bd=0, highlightthickness=0)
        # No padding at the sides: each row starts and ends with a space instead, so the chosen row's
        # background runs from edge to edge.
        self.list = tk.Text(self.popup, height=1, width=20, wrap="none", font=FONTS["mono"], bg=c["popup"], fg=c["ink"],
                            bd=0, highlightthickness=0, padx=0, pady=px(3), cursor="arrow", takefocus=0,
                            state="disabled", spacing1=px(2), spacing3=px(2), insertwidth=0)
        self.list.pack(padx=1, pady=(1, 0))
        self.hint = tk.Label(self.popup, text=HINT, font=FONTS["small"], bg=c["popup"], fg=c["muted"], anchor="w",
                             padx=kit.font(self.text, "mono").measure(" "), pady=px(3), bd=0)
        self.hint.pack(fill="x", padx=1, pady=(0, 1))
        for kind in assist.KINDS + ("column",): self.list.tag_configure(kind, foreground=c[kind])
        self.list.tag_configure("note", foreground=c["muted"])
        self.list.tag_configure("chosen", background=c["popup_selected"])
        self.list.tag_raise("chosen")
        # Its own bind tag only: the list is never typed in or selected from, just pointed at.
        tag = self.tag + "List"
        self.list.bindtags((tag,))
        self.list.bind_class(tag, "<Button-1>", self.click)
        self.list.bind_class(tag, "<Motion>", self.hover)
        self.list.bind_class(tag, "<MouseWheel>", lambda event: self.list.yview_scroll(-1 if event.delta > 0 else 1, "units") or "break")
        for widget in (self.popup, self.hint):  # A click on the frame or the hint keeps the keyboard in the query.
            widget.bind("<Button-1>", lambda event: "break")

    def show(self):
        """Draw the list for self.offer just under the word being typed (above it when there is no room)."""
        if self.popup is None: self.build()
        offer, text, view = self.offer, self.text, self.list
        names = [choice.text if len(choice.text) <= WIDEST else choice.text[:WIDEST - 1] + "…" for choice in offer.choices]
        width = max(len(name) for name in names)
        view.configure(state="normal"); view.delete("1.0", "end")
        for index, (name, choice) in enumerate(zip(names, offer.choices)):
            if index: view.insert("end", "\n")
            view.insert("end", " " + name.ljust(width), choice.kind)
            view.insert("end", "   " + choice.note + " ", "note")
        view.configure(state="disabled", height=min(ROWS, len(names)),
                       width=width + 5 + max(len(choice.note) for choice in offer.choices))
        self.mark_chosen()
        box = text.bbox(f"insert - {len(offer.word)}c")
        if box is None: return self.hide()  # Scrolled out of the box.
        root, popup = self.root, self.popup
        popup.update_idletasks()
        wide, high = popup.winfo_reqwidth(), popup.winfo_reqheight()
        # The names line up under the word being typed: back by the border and each row's leading space.
        x = text.winfo_rootx() - root.winfo_rootx() + box[0] - 1 - kit.font(text, "mono").measure(" ")
        y = text.winfo_rooty() - root.winfo_rooty() + box[1]
        below = y + box[3] + px(2)
        if below + high > root.winfo_height() and y - high - px(2) >= 0: below = y - high - px(2)
        popup.place(x=max(0, min(x, root.winfo_width() - wide)), y=below)
        popup.lift()
        self.size = (root.winfo_width(), root.winfo_height())

    def resized(self, event):
        """Close the list when the window's size changes: the query under it may have moved."""
        if event.widget is self.root and self.offer and (event.width, event.height) != self.size: self.hide()

    def mark_chosen(self):
        view = self.list
        view.tag_remove("chosen", "1.0", "end")
        view.tag_add("chosen", f"{self.chosen + 1}.0", f"{self.chosen + 2}.0")
        view.see(f"{self.chosen + 1}.0")

    def hide(self):
        if self.pending is not None:
            try: self.root.after_cancel(self.pending)
            except tk.TclError: pass
            self.pending = None
        self.offer, self.navigated, self.forced = None, False, False
        if self.popup is not None:
            try: self.popup.place_forget()
            except tk.TclError: pass

    def move(self, step):
        self.chosen = max(0, min(len(self.offer.choices) - 1, self.chosen + step))
        self.navigated = True
        self.mark_chosen()
        return "break"

    def row(self, event):
        index = int(self.list.index(f"@{event.x},{event.y}").split(".")[0]) - 1
        return index if self.offer and 0 <= index < len(self.offer.choices) else None

    def hover(self, event):
        index = self.row(event)
        if index is not None and index != self.chosen:
            self.chosen = index; self.mark_chosen()

    def click(self, event):
        index = self.row(event)
        if index is not None:
            self.chosen = index; self.accept()
        return "break"

    def enter(self, event):
        """Enter inserts a choice made with the arrows, or completes a step being typed after a |.
        Anything else is the analyst ending the line: the list closes and Enter does what it always does."""
        if not self.offer: return None
        offer, choice = self.offer, self.offer.choices[self.chosen]
        if self.navigated or (offer.context == "step" and offer.word and choice.text != offer.word):
            return self.accept()
        self.hide()
        return None

    def accept(self):
        """Write the chosen completion over the word being typed, as one step for Ctrl+Z."""
        offer, text = self.offer, self.text
        if not offer: return None
        choice = offer.choices[self.chosen]
        self.hide()
        # What already follows the caret isn't written twice: the ( of a call, the space after a step.
        following = text.get("insert", "insert+1c")
        tail = "" if choice.tail and following == choice.tail[0] else choice.tail
        automatic = text.cget("autoseparators")
        text.configure(autoseparators=False); text.edit_separator()
        try:
            text.delete(f"insert - {len(offer.word)}c", "insert")
            text.insert("insert", choice.text + tail)
            if tail and choice.back: text.mark_set("insert", f"insert - {choice.back}c")
            elif choice.tail == " " and not tail: text.mark_set("insert", "insert + 1c")  # Past the space that was there.
        finally:
            text.edit_separator(); text.configure(autoseparators=automatic)
        text.see("insert")
        return "break"
