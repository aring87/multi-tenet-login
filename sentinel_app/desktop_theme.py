"""Design tokens and ttk styles for the Windows desktop interface.

Every colour, font and size used by the desktop lives here so the pages and
dialogs read as one system. Fonts fall back gracefully when the Windows 11
families are not installed.
"""
import math
import struct
import zlib
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

COLORS = dict(
    canvas="#F4F6FA", card="#FFFFFF", raised="#F8FAFC", hover="#F1F5F9", stripe="#F6F8FB",
    line="#E2E8F0", line_strong="#CBD5E1", shadow="#DCE2EC",
    # muted is the grey for text (AA contrast on card and canvas); subtle is for non-text marks only;
    # control_stroke is a control's own edge, such as an empty checkbox (3:1 on every surface).
    ink="#0F172A", ink_2="#334155", muted="#5B6778", subtle="#94A3B8", disabled="#A0AEC0",
    control_stroke="#808DA1",
    accent="#2563EB", accent_hover="#1D4ED8", accent_pressed="#1E40AF",
    accent_soft="#EEF4FF", accent_line="#BFD3FE",
    # Selected table rows: stronger while the table has the keyboard, quieter when it does not.
    selection="#D3E2FD", selection_inactive="#E2EAF8",
    success="#157F3C", success_soft="#E9F7EF",
    warning="#B45309", warning_soft="#FEF5E6",
    danger="#B42318", danger_hover="#9A1D14", danger_pressed="#7F1810", danger_soft="#FEF1F0", danger_line="#F5C2BD",
    sidebar="#0B1526", sidebar_hover="#131F35", sidebar_active="#1A2A47", sidebar_line="#1B2940",
    sidebar_ink="#E7ECF4", sidebar_muted="#8593AA", sidebar_accent="#60A5FA", sidebar_success="#4ADE80",
)

# Tone name -> (foreground, soft background) for pills, banners and status dots.
TONES = dict(neutral=("#475569", "#F1F5F9"), info=(COLORS["accent"], COLORS["accent_soft"]),
             success=(COLORS["success"], COLORS["success_soft"]),
             warning=(COLORS["warning"], COLORS["warning_soft"]),
             danger=(COLORS["danger"], COLORS["danger_soft"]))

# Colour schemes for the Rule builder's Query box (kql_editor), the first being the default. Each has
# the box's own colours, one colour per part of a query (kql_assist.KINDS, plus column for the
# completion list), the marks the KQL check draws and the completion list's surface. Every text colour
# is AA (4.5:1) on its background and readable (3:1) on a mark; test_kql_editor checks.
KQL_THEMES = {
    "Daylight": dict(  # The app's own card: ink on white, with the accent blue for the steps.
        background=COLORS["card"], ink=COLORS["ink"], muted=COLORS["muted"], caret=COLORS["ink"],
        selection=COLORS["accent_line"], selection_inactive=COLORS["line"], thumb=COLORS["line"],
        thumb_active=COLORS["line_strong"],
        comment="#64748B", string="#15803D", number="#C2410C", step="#1D4ED8", keyword="#7C3AED",
        function="#0E7490", table="#BE185D", variable="#9D174D", operator="#475569", column=COLORS["ink"],
        error=COLORS["danger"], error_soft=COLORS["danger_soft"], warning=COLORS["warning"],
        warning_soft=COLORS["warning_soft"],
        popup=COLORS["card"], popup_line=COLORS["line_strong"], popup_selected=COLORS["selection"]),
    "Midnight": dict(  # The sidebar's navy, with cool blues and mint.
        background="#0B1526", ink="#E2E8F0", muted="#8593AA", caret="#F8FAFC",
        selection="#1E3A5F", selection_inactive="#1B2940", thumb="#27385A", thumb_active="#3A4F7A",
        comment="#7B8BA5", string="#86EFAC", number="#FDBA74", step="#60A5FA", keyword="#C4B5FD",
        function="#5EEAD4", table="#F9A8D4", variable="#FBCFE8", operator="#94A3B8", column="#E2E8F0",
        error="#F87171", error_soft="#4C1D24", warning="#FBBF24", warning_soft="#45310B",
        popup="#111E36", popup_line="#2B3D61", popup_selected="#1E3A5F"),
    "Neon": dict(  # Deep indigo with hot pink steps, cyan functions and lime strings.
        background="#1A1033", ink="#F5F3FF", muted="#9D95C4", caret="#F5F3FF",
        selection="#3B2A6B", selection_inactive="#2A1D4F", thumb="#3B2A6B", thumb_active="#54408F",
        comment="#8E86B5", string="#A3E635", number="#FDE047", step="#F472B6", keyword="#C084FC",
        function="#22D3EE", table="#FB923C", variable="#FDBA74", operator="#A5B4FC", column="#F5F3FF",
        error="#FB7185", error_soft="#4A1530", warning="#FACC15", warning_soft="#3F3212",
        popup="#221544", popup_line="#43317A", popup_selected="#3B2A6B"),
}
KQL_THEME = "Daylight"

# Layout spacing scale, in 96-DPI pixels; always wrap in px().
SPACE = dict(xs=4, sm=8, md=12, lg=16, xl=24, page=32)

# Segoe Fluent Icons / Segoe MDL2 Assets share these code points.
ICONS = dict(connect="", rules="", builder="", shield="", cloud="",
             key="", library="", info="", warning="", check="",
             document="", activity="", help="", search="")

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
    small = _first(families, "Segoe UI Variable Small", fallback=text)
    # GDI lists the variable font's named instances under names cut to 31 characters.
    strong = _first(families, "Segoe UI Variable Text Semibold", "Segoe UI Semibold", fallback=text)
    display_strong = _first(families, "Segoe UI Variable Display Semib", "Segoe UI Semibold", fallback=display)
    # A real Semibold family is drawn at its own weight; only a plain family needs synthetic bold.
    weight = lambda family: "normal" if "Semib" in family else "bold"
    mono = _first(families, "Cascadia Mono", "Consolas", "Courier New", fallback="TkFixedFont")
    icons = _first(families, "Segoe Fluent Icons", "Segoe MDL2 Assets", fallback="")
    FONTS.clear()
    FONTS.update(
        body=(text, 10), small=(small, 9),
        strong=(strong, 10, weight(strong)), strong_small=(strong, 9, weight(strong)),
        eyebrow=(strong, 9, weight(strong)), field=(strong, 9, weight(strong)),
        title=(display_strong, 12, weight(display_strong)), page=(display_strong, 19, weight(display_strong)),
        brand=(display_strong, 12, weight(display_strong)), stat=(display_strong, 22, weight(display_strong)),
        mono=(mono, 9),
        icon=(icons, 12) if icons else None, icon_large=(icons, 15) if icons else None,
        icon_hero=(icons, 26) if icons else None, icon_small=(icons, 10) if icons else None)


def app_icon(root, size=32):
    """The product mark as a Tk image; see brand.py for the drawing."""
    from .brand import mark
    return mark(root, size)


# ------------------------------------------------------------------ checkboxes
# Checkbox state -> (fill, border, tick) as COLORS names; no tick draws an empty box.
CHECKBOX = dict(off=("card", "control_stroke", None), hover=("hover", "muted", None), pressed=("line", "muted", None),
                disabled=("raised", "line_strong", None), on=("accent", "accent", "#FFFFFF"),
                on_hover=("accent_hover", "accent_hover", "#FFFFFF"),
                on_pressed=("accent_pressed", "accent_pressed", "#FFFFFF"), on_disabled=("disabled", "disabled", "#FFFFFF"))
# The ttk checkbox indicator: (widget states, CHECKBOX state, focus ring), first match wins; else 'off'.
CHECK_INDICATOR = ((("disabled", "selected"), "on_disabled", False), (("disabled",), "disabled", False),
                   (("pressed", "focus", "selected"), "on_pressed", True), (("pressed", "focus"), "pressed", True),
                   (("pressed", "selected"), "on_pressed", False), (("pressed",), "pressed", False),
                   (("active", "focus", "selected"), "on_hover", True), (("active", "focus"), "hover", True),
                   (("focus", "selected"), "on", True), (("focus",), "off", True),
                   (("active", "selected"), "on_hover", False), (("active",), "hover", False),
                   (("selected",), "on", False))
_TICK = ((3.6, 7.3), (6.0, 9.7), (10.6, 4.6))  # The tick's corners on a 14-unit box.
_SHAPES, _PNGS = {}, {}


def _segment_distance(x, y, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


def _box_shape(box, margin, samples=4):
    """How many of each pixel's samples² points fall on the tick, the 1px border, the fill and
    the focus ring of a rounded ``box``-pixel square with ``margin`` pixels around it for the
    ring. Worked out once per size and shared by every Tk root."""
    if (box, margin) not in _SHAPES:
        size, unit, centre = box + 2 * margin, box / 14, box / 2 + margin
        inner, radius = box / 2 - 2.5 * box / 14, 2.5 * box / 14
        tick = [(margin + x * unit, margin + y * unit) for x, y in _TICK]
        cells = []
        for row in range(size):
            for column in range(size):
                counts = [0, 0, 0, 0]
                for i in range(samples):
                    for j in range(samples):
                        x, y = column + (i + .5) / samples, row + (j + .5) / samples
                        qx, qy = abs(x - centre) - inner, abs(y - centre) - inner
                        distance = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
                        if distance > 0:
                            if margin / 2 < distance <= margin: counts[3] += 1
                        elif min(_segment_distance(x, y, tick[0], tick[1]),
                                 _segment_distance(x, y, tick[1], tick[2])) <= .9 * unit: counts[0] += 1
                        else: counts[1 if distance > -1 else 2] += 1
                cells.append(counts)
        _SHAPES[box, margin] = (size, samples * samples, cells)
    return _SHAPES[box, margin]


def _png(width, height, rgba):
    """Encode RGBA bytes as a PNG, which Tk reads with its alpha channel."""
    rows = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def checkbox_png(state, box, margin=0, focus=False):
    """A CHECKBOX state drawn as PNG data: antialiased with real alpha, so its rounded edge blends
    into any row or surface; ``focus`` adds an accent ring in the margin. Cached per process."""
    key = (state, box, margin, focus)
    if key not in _PNGS:
        rgb = lambda name: tuple(int(COLORS.get(name, name)[i:i + 2], 16) for i in (1, 3, 5))
        fill, border, tick = (rgb(name) if name else None for name in CHECKBOX.get(state, CHECKBOX["off"]))
        ring = rgb("accent") if focus else None
        size, total, cells = _box_shape(box, margin)
        data = bytearray()
        for counts in cells:
            # A pixel's colour is the average of what its samples hit; its alpha, how many hit.
            parts = [(color, n) for color, n in zip((tick or fill, border, fill, ring), counts) if n and color]
            hits = sum(n for _, n in parts)
            if not hits: data += b"\0\0\0\0"; continue
            data += bytes(round(sum(color[k] * n for color, n in parts) / hits) for k in range(3))
            data.append(round(255 * hits / total))
        _PNGS[key] = _png(size, size, bytes(data))
    return _PNGS[key]


def checkbox_image(root, state, box, margin=0, focus=False):
    """checkbox_png as a PhotoImage of ``root`` (images belong to one Tk interpreter), cached on it."""
    cache = getattr(root, "_kit_images", None)
    if cache is None: cache = root._kit_images = {}
    key = ("checkbox", state, box, margin, focus)
    if key not in cache:
        cache[key] = tk.PhotoImage(master=root, data=checkbox_png(state, box, margin, focus))
    return cache[key]


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
                           ("*TCombobox*Listbox.selectBackground", c["selection"]),
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
            ("Eyebrow", f["eyebrow"], c["muted"], c["card"]),
            ("PageTitle", f["page"], c["ink"], c["card"]),
            ("PageSubtitle", f["body"], c["muted"], c["card"]),
            ("Context", f["body"], c["muted"], c["canvas"])):
        style.configure(name + ".TLabel", font=font, foreground=color, background=background)
    # A form field's hint becomes this while the field holds an error (FormDialog).
    style.configure("ErrorHint.TLabel", font=f["small"], foreground=c["danger"], background=c["card"])

    # Buttons: flat fills, 1px borders, a coloured border for keyboard focus. Disabled buttons
    # go quiet: bordered ones (Primary and Danger included) look like a disabled Secondary, and
    # borderless ones (Ghost, Disclosure) keep their card fill and only grey their text.
    pad = (px(14), px(7))
    for name, background, foreground, border, hover, pressed, quiet, focus in (
            ("TButton", c["card"], c["ink"], c["line_strong"], c["hover"], c["line"], False, c["accent"]),
            ("Secondary.TButton", c["card"], c["ink"], c["line_strong"], c["hover"], c["line"], False, c["accent"]),
            ("Primary.TButton", c["accent"], "#FFFFFF", c["accent"], c["accent_hover"], c["accent_pressed"], False,
             c["accent_pressed"]),
            ("Danger.TButton", c["danger"], "#FFFFFF", c["danger"], c["danger_hover"], c["danger_pressed"], False,
             c["danger_pressed"]),
            ("Ghost.TButton", c["card"], c["accent"], c["card"], c["accent_soft"], c["accent_line"], True, c["accent"]),
            ("Disclosure.TButton", c["card"], c["ink_2"], c["card"], c["hover"], c["line"], True, c["accent"])):
        filled = foreground == "#FFFFFF"
        style.configure(name, padding=pad, background=background, foreground=foreground,
                        bordercolor=border, lightcolor=background, darkcolor=background,
                        relief="raised", borderwidth=1, focusthickness=px(1) if filled else 0,
                        focuscolor="#FFFFFF" if filled else background,
                        font=f["strong"], width=-6, anchor="w" if name.startswith("Disclosure") else "center")
        off = c["card"] if quiet else c["raised"]
        style.map(name,
                  background=[("disabled", off), ("pressed", pressed), ("active", hover)],
                  lightcolor=[("disabled", off), ("pressed", pressed), ("active", hover)],
                  darkcolor=[("disabled", off), ("pressed", pressed), ("active", hover)],
                  foreground=[("disabled", c["disabled"])],
                  # Keyboard focus: an accent border, or on a filled button a white inner ring inside a darker edge.
                  bordercolor=[("disabled", c["card"] if quiet else c["line"]), ("focus", focus)])
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
    # A search box: room on the left for the magnifier glyph that kit.placeholder(search=True) draws.
    style.configure("Search.TEntry", padding=(px(32), px(6), px(10), px(6)))

    for name in ("Error.TEntry", "Error.TCombobox", "Error.TSpinbox"):
        style.configure(name, bordercolor=c["danger"], lightcolor=c["danger_line"])
        style.map(name, bordercolor=[("focus", c["danger"]), ("hover", c["danger"])],
                  lightcolor=[("focus", c["danger_line"])])

    # Checkboxes: a drawn Fluent box in place of clam's cross, one image per state (CHECK_INDICATOR),
    # with an accent ring for keyboard focus instead of a dotted one round the label. The element's
    # width leaves the gap before the label.
    box, ring = px(16), px(2)
    if "Kit.Checkbutton.indicator" not in style.element_names():
        image = lambda state, focus=False: checkbox_image(root, state, box, ring, focus)
        style.element_create("Kit.Checkbutton.indicator", "image", image("off"),
                             *[(*states, image(state, focus)) for states, state, focus in CHECK_INDICATOR],
                             width=box + 2 * ring + px(6), sticky="w")
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Kit.Checkbutton.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": "w"})]})])
    style.configure("TCheckbutton", background=c["card"], foreground=c["ink_2"], padding=(0, px(2)), font=f["body"])
    style.map("TCheckbutton", background=[("active", c["card"])], foreground=[("disabled", c["disabled"])])

    # Tables.
    row = max(px(30), int(tkfont.Font(root, font=f["body"]).metrics("linespace") * 1.85))
    style.configure("Treeview", background=c["card"], fieldbackground=c["card"], foreground=c["ink"],
                    rowheight=row, borderwidth=0, relief="flat", font=f["body"])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    # Tables with a leading dot or checkbox column (kit.table marker/check): a flat list, so
    # the first column holds just the centred image, with no expand indicator beside it.
    style.layout("Lead.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
        ("Treeitem.image", {"sticky": ""})]})])
    # Cells start 10 px further in, as entries and cards do (cell text already sits 3 px inside its padding,
    # so headings take 3 px more to stay in line). Tables with a lead column already have that gutter (the
    # dot or checkbox), so they keep Tk's own padding. ui_kit.StripedTreeview.PAD follows these numbers.
    style.configure("Treeview.Heading", background=c["raised"], foreground=c["muted"], font=f["strong_small"],
                    padding=(px(10) + px(3), px(8), px(4), px(8)), relief="flat", borderwidth=0,
                    bordercolor=c["line"], lightcolor=c["raised"], darkcolor=c["line"])
    style.configure("Treeview.Cell", padding=(px(10), 0, px(4), 0))
    style.configure("Lead.Treeview.Heading", padding=(px(3), px(8)))
    style.configure("Lead.Treeview.Cell", padding=0)  # Tk's default.
    style.map("Treeview.Heading", background=[("active", c["hover"])], lightcolor=[("active", c["hover"])])
    # The selected row beats the stripe tag (checked on Tk 8.6.15), so striping needs no special case.
    style.map("Treeview", background=[("selected", "focus", c["selection"]), ("selected", c["selection_inactive"])],
              foreground=[("selected", c["ink"])])

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

    # Split views: the sash is a wide, invisible gap on the canvas, dragged by its cursor.
    style.configure("TPanedwindow", background=c["canvas"])
    style.configure("Sash", sashthickness=px(12), background=c["canvas"], gripcount=0)

    style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["card"], borderwidth=0,
                    thickness=px(3), bordercolor=c["card"], lightcolor=c["accent"], darkcolor=c["accent"])
    return style
