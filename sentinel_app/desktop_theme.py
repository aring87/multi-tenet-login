"""Design tokens and ttk styles for the Windows desktop interface.

Every colour, font and size used by the desktop lives here so the pages and
dialogs read as one system. Fonts fall back gracefully when the Windows 11
families are not installed.
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

COLORS = dict(
    canvas="#F4F6FA", card="#FFFFFF", raised="#F8FAFC", hover="#F1F5F9",
    line="#E2E8F0", line_strong="#CBD5E1",
    ink="#0F172A", ink_2="#334155", muted="#64748B", subtle="#94A3B8", disabled="#A0AEC0",
    accent="#2563EB", accent_hover="#1D4ED8", accent_pressed="#1E40AF",
    accent_soft="#EEF4FF", accent_line="#BFD3FE", accent_disabled="#9DB7F2",
    success="#157F3C", success_soft="#E9F7EF",
    warning="#B45309", warning_soft="#FEF5E6",
    danger="#B42318", danger_soft="#FEF1F0", danger_line="#F5C2BD",
    sidebar="#0B1526", sidebar_hover="#131F35", sidebar_active="#1A2A47", sidebar_line="#1B2940",
    sidebar_ink="#E7ECF4", sidebar_muted="#8593AA", sidebar_accent="#60A5FA", sidebar_success="#4ADE80",
)

# Tone name -> (foreground, soft background) for pills, banners and status dots.
TONES = dict(neutral=("#475569", "#F1F5F9"), info=(COLORS["accent"], COLORS["accent_soft"]),
             success=(COLORS["success"], COLORS["success_soft"]),
             warning=(COLORS["warning"], COLORS["warning_soft"]),
             danger=(COLORS["danger"], COLORS["danger_soft"]))

# Layout spacing scale, in 96-DPI pixels; always wrap in px().
SPACE = dict(xs=4, sm=8, md=12, lg=16, xl=24, page=32)

# Segoe Fluent Icons / Segoe MDL2 Assets share these code points.
ICONS = dict(connect="", rules="", builder="", shield="", cloud="",
             key="", library="", info="", warning="", check="",
             document="", activity="", help="")

FONTS = {}
_SCALE = [1.0]


def px(value):
    """Scale a 96-DPI pixel measurement to the current display."""
    return int(round(value * _SCALE[0]))


def _first(families, *names, fallback="TkDefaultFont"):
    return next((name for name in names if name in families), fallback)


def _resolve_fonts(root):
    families = set(tkfont.families(root))
    text = _first(families, "Segoe UI Variable Text", "Segoe UI", fallback="TkDefaultFont")
    display = _first(families, "Segoe UI Variable Display", "Segoe UI", fallback=text)
    strong = _first(families, "Segoe UI Semibold", fallback=text)
    strong_weight = "normal" if strong == "Segoe UI Semibold" else "bold"
    mono = _first(families, "Cascadia Mono", "Consolas", "Courier New", fallback="TkFixedFont")
    icons = _first(families, "Segoe Fluent Icons", "Segoe MDL2 Assets", fallback="")
    FONTS.clear()
    FONTS.update(
        body=(text, 10), small=(text, 9),
        strong=(strong, 10, strong_weight), strong_small=(strong, 9, strong_weight),
        eyebrow=(strong, 9, strong_weight), field=(strong, 9, strong_weight),
        title=(display, 12, "bold"), page=(display, 19, "bold"), brand=(display, 12, "bold"),
        stat=(display, 20, "bold"), mono=(mono, 9),
        icon=(icons, 12) if icons else None, icon_large=(icons, 15) if icons else None,
        icon_small=(icons, 10) if icons else None)


def app_icon(root, size=32):
    """Window and taskbar icon: a white shield on the accent colour."""
    image = tk.PhotoImage(master=root, width=size, height=size)
    image.put(COLORS["accent"], to=(0, 0, size, size))
    top, bottom, centre, half = size * 3 // 16, size * 27 // 32, (size - 1) / 2, size * 9 / 32
    for y in range(top, bottom):
        progress = (y - top) / (bottom - top)
        width = half if progress < .5 else half * (1 - (progress - .5) / .5) ** .8
        left, right = int(round(centre - width)), int(round(centre + width)) + 1
        if right > left: image.put("#FFFFFF", to=(left, y, right, y + 1))
    return image


def configure_theme(root):
    c = COLORS
    _SCALE[0] = max(1.0, float(root.tk.call("tk", "scaling")) * 72 / 96)
    _resolve_fonts(root)
    f = FONTS
    root.configure(bg=c["canvas"])
    font_spec = lambda value: "{%s} %d" % value[:2]
    for pattern, value in (("*TCombobox*Listbox.font", font_spec(f["body"])),
                           ("*TCombobox*Listbox.background", c["card"]),
                           ("*TCombobox*Listbox.foreground", c["ink"]),
                           ("*TCombobox*Listbox.selectBackground", c["accent_soft"]),
                           ("*TCombobox*Listbox.selectForeground", c["ink"]),
                           ("*TCombobox*Listbox.borderWidth", 0),
                           ("*Text.background", c["card"]), ("*Text.foreground", c["ink"]),
                           ("*Text.font", font_spec(f["mono"])), ("*Text.relief", "flat"),
                           ("*Text.borderWidth", 0), ("*Text.highlightThickness", 1),
                           ("*Text.highlightBackground", c["line"]), ("*Text.highlightColor", c["accent_line"]),
                           ("*Text.padX", px(12)), ("*Text.padY", px(10)),
                           ("*Text.selectBackground", c["accent_line"]), ("*Text.selectForeground", c["ink"]),
                           ("*Text.inactiveSelectBackground", c["line"]), ("*Text.insertBackground", c["ink"]),
                           ("*Toplevel.background", c["canvas"])):
        root.option_add(pattern, value)
    style = ttk.Style(root); style.theme_use("clam")
    style.configure(".", font=f["body"], foreground=c["ink"], background=c["canvas"],
                    bordercolor=c["line"], lightcolor=c["canvas"], darkcolor=c["canvas"],
                    troughcolor=c["hover"], selectbackground=c["accent_soft"], selectforeground=c["ink"],
                    focuscolor=c["accent"], insertcolor=c["ink"])
    style.configure("TFrame", background=c["canvas"])
    style.configure("Card.TFrame", background=c["card"])
    style.configure("Raised.TFrame", background=c["raised"])
    style.configure("TLabel", background=c["canvas"], foreground=c["ink"])
    style.configure("TSeparator", background=c["line"])
    for name, font, color, background in (
            ("CardTitle", f["title"], c["ink"], c["card"]),
            ("CardSubtitle", f["body"], c["muted"], c["card"]),
            ("Field", f["field"], c["ink_2"], c["card"]),
            ("Muted", f["body"], c["muted"], c["card"]),
            ("Hint", f["small"], c["muted"], c["card"]),
            ("Body", f["body"], c["ink_2"], c["card"]),
            ("Section", f["field"], c["muted"], c["card"]),
            ("Value", f["mono"], c["ink"], c["card"]),
            ("Prose", f["body"], c["ink"], c["card"]),
            ("Key", f["small"], c["muted"], c["card"]),
            ("RaisedKey", f["small"], c["muted"], c["raised"]),
            ("RaisedValue", f["mono"], c["ink"], c["raised"]),
            ("RaisedProse", f["body"], c["ink"], c["raised"]),
            ("RaisedMuted", f["body"], c["muted"], c["raised"]),
            ("Stat", f["stat"], c["ink"], c["card"]),
            ("PageTitle", f["page"], c["ink"], c["card"]),
            ("PageSubtitle", f["body"], c["muted"], c["card"]),
            ("Context", f["body"], c["muted"], c["canvas"])):
        style.configure(name + ".TLabel", font=font, foreground=color, background=background)

    # Buttons: flat fills, 1px borders, a coloured border for keyboard focus.
    pad = (px(14), px(7))
    for name, background, foreground, border, hover, pressed in (
            ("TButton", c["card"], c["ink"], c["line_strong"], c["hover"], c["line"]),
            ("Secondary.TButton", c["card"], c["ink"], c["line_strong"], c["hover"], c["line"]),
            ("Primary.TButton", c["accent"], "#FFFFFF", c["accent"], c["accent_hover"], c["accent_pressed"]),
            ("Ghost.TButton", c["card"], c["accent"], c["card"], c["accent_soft"], c["accent_line"]),
            ("Disclosure.TButton", c["card"], c["ink_2"], c["card"], c["hover"], c["line"])):
        style.configure(name, padding=pad, background=background, foreground=foreground,
                        bordercolor=border, lightcolor=background, darkcolor=background,
                        relief="raised", borderwidth=1, focusthickness=0, focuscolor=background,
                        font=f["strong"], width=-6, anchor="w" if name.startswith("Disclosure") else "center")
        style.map(name,
                  background=[("disabled", c["raised"] if name != "Primary.TButton" else c["accent_disabled"]),
                              ("pressed", pressed), ("active", hover)],
                  lightcolor=[("disabled", c["raised"] if name != "Primary.TButton" else c["accent_disabled"]),
                              ("pressed", pressed), ("active", hover)],
                  darkcolor=[("disabled", c["raised"] if name != "Primary.TButton" else c["accent_disabled"]),
                             ("pressed", pressed), ("active", hover)],
                  foreground=[("disabled", c["disabled"] if name != "Primary.TButton" else "#FFFFFF")],
                  bordercolor=[("disabled", c["line"] if name != "Primary.TButton" else c["accent_disabled"]),
                               ("focus", c["accent"])])
    # Keyboard focus on the filled primary button: a white inner ring inside a darker edge.
    style.configure("Primary.TButton", focusthickness=px(1), focuscolor="#FFFFFF")
    style.map("Primary.TButton", bordercolor=[("disabled", c["accent_disabled"]), ("focus", c["accent_pressed"])])
    style.configure("Small.Secondary.TButton", padding=(px(10), px(4)), font=f["strong_small"])
    style.configure("Small.Primary.TButton", padding=(px(10), px(4)), font=f["strong_small"])
    style.configure("Small.Ghost.TButton", padding=(px(8), px(4)), font=f["strong_small"])

    # Inputs.
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=c["card"], background=c["card"], foreground=c["ink"],
                        bordercolor=c["line_strong"], lightcolor=c["card"], darkcolor=c["card"],
                        padding=(px(10), px(6)), arrowsize=px(12), arrowcolor=c["muted"], insertwidth=1)
        style.map(name, bordercolor=[("focus", c["accent"]), ("hover", c["subtle"])],
                  lightcolor=[("focus", c["accent_line"])],
                  fieldbackground=[("disabled", c["raised"]), ("readonly", c["card"])],
                  background=[("disabled", c["raised"]), ("readonly", c["card"]), ("active", c["card"])],
                  foreground=[("disabled", c["muted"])], arrowcolor=[("disabled", c["disabled"])],
                  selectbackground=[("readonly", c["card"]), ("!focus", c["line"])],
                  selectforeground=[("readonly", c["ink"])])

    for name in ("Error.TEntry", "Error.TCombobox", "Error.TSpinbox"):
        style.configure(name, bordercolor=c["danger"], lightcolor=c["danger_line"])
        style.map(name, bordercolor=[("focus", c["danger"]), ("hover", c["danger"])],
                  lightcolor=[("focus", c["danger_line"])])

    for name in ("TCheckbutton",):
        style.configure(name, background=c["card"], foreground=c["ink_2"], padding=(0, px(4)),
                        indicatorbackground=c["card"], indicatorforeground="#FFFFFF",
                        upperbordercolor=c["line_strong"], lowerbordercolor=c["line_strong"],
                        indicatormargin=(0, 0, px(8), 0), indicatorsize=px(15), focusthickness=0, font=f["body"])
        style.map(name, background=[("active", c["card"])],
                  foreground=[("disabled", c["disabled"])],
                  indicatorbackground=[("disabled", c["raised"]), ("selected", c["accent"]), ("pressed", c["accent_soft"])],
                  upperbordercolor=[("selected", c["accent"]), ("focus", c["accent"])],
                  lowerbordercolor=[("selected", c["accent"]), ("focus", c["accent"])])

    # Tables.
    row = max(px(34), int(tkfont.Font(root, font=f["body"]).metrics("linespace") * 2.2))
    style.configure("Treeview", background=c["card"], fieldbackground=c["card"], foreground=c["ink"],
                    rowheight=row, borderwidth=0, relief="flat", font=f["body"])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview.Heading", background=c["raised"], foreground=c["muted"], font=f["strong_small"],
                    padding=(px(3), px(8)), relief="flat", borderwidth=0,
                    bordercolor=c["line"], lightcolor=c["raised"], darkcolor=c["line"])
    style.map("Treeview.Heading", background=[("active", c["hover"])], lightcolor=[("active", c["hover"])])
    style.map("Treeview", background=[("selected", c["accent_soft"])], foreground=[("selected", c["ink"])])

    # Slim, arrowless scrollbars.
    for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
        style.layout(orient + ".TScrollbar", [(orient + ".Scrollbar.trough", {"sticky": sticky, "children": [
            (orient + ".Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure(orient + ".TScrollbar", background=c["line"], troughcolor=c["card"],
                        bordercolor=c["card"], lightcolor=c["line"], darkcolor=c["line"],
                        gripcount=0, borderwidth=0, arrowsize=px(9), width=px(9))
        style.map(orient + ".TScrollbar", background=[("pressed", c["subtle"]), ("active", c["line_strong"])],
                  lightcolor=[("pressed", c["subtle"]), ("active", c["line_strong"])],
                  darkcolor=[("pressed", c["subtle"]), ("active", c["line_strong"])])
        style.configure("Canvas." + orient + ".TScrollbar", troughcolor=c["canvas"], bordercolor=c["canvas"])
        style.layout("Canvas." + orient + ".TScrollbar", style.layout(orient + ".TScrollbar"))

    # Notebooks: hidden tabs for app-drawn navigation, quiet tabs for dialogs.
    style.configure("Hidden.TNotebook", background=c["canvas"], borderwidth=0, tabmargins=0, padding=0,
                    bordercolor=c["canvas"], lightcolor=c["canvas"], darkcolor=c["canvas"])
    style.layout("Hidden.TNotebook.Tab", [])
    style.configure("CardHidden.TNotebook", background=c["card"], borderwidth=0, tabmargins=0, padding=0,
                    bordercolor=c["card"], lightcolor=c["card"], darkcolor=c["card"])
    style.layout("CardHidden.TNotebook.Tab", [])
    style.configure("TNotebook", background=c["canvas"], bordercolor=c["line"], tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=c["canvas"], foreground=c["muted"], padding=(px(14), px(7)),
                    bordercolor=c["line"], lightcolor=c["canvas"], font=f["strong_small"])
    style.map("TNotebook.Tab", background=[("selected", c["card"])], foreground=[("selected", c["ink"])],
              lightcolor=[("selected", c["card"])])

    style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["card"], borderwidth=0,
                    thickness=px(3), bordercolor=c["card"], lightcolor=c["accent"], darkcolor=c["accent"])
    return style
