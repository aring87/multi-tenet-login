"""An organization's own logo at the foot of the sidebar, read from desktop-data/branding.

The app ships with no logo. An organization adds one by putting pre-sized PNGs named
``logo-<height>.png`` in that folder; scripts/make_logo_sizes.py makes the set from one large
PNG. The app shows the file whose height is nearest the size it needs at the display's scaling,
so the logo is never stretched or blurred. A missing, empty or unreadable folder shows nothing,
and so does a file too large to be a sidebar logo."""
import re
import tkinter as tk
from pathlib import Path

FOLDER = "branding"
HEIGHT = 20                 # Logo height at 100% display scaling, in pixels.
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)  # Windows display scaling steps the set covers.
NAME = re.compile(r"logo-(\d{1,3})\.png", re.IGNORECASE)
MAX_BYTES = 512 * 1024      # A pre-sized logo is a few KB; anything larger isn't one.


def heights(base=HEIGHT, scales=SCALES):
    """The logo heights a full set has, one per display scaling step: 20, 25, 30 … 60 by default."""
    return sorted({round(base * scale) for scale in scales})


def sizes(folder):
    """{height: path} for the logo files in ``folder``; empty when there are none or it can't be read."""
    found = {}
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return found
    for path in entries:
        match = NAME.fullmatch(path.name)
        if match and int(match.group(1)) > 0 and path.is_file():
            found[int(match.group(1))] = path
    return found


def choose(available, wanted):
    """The available height nearest ``wanted`` (the larger of two equally near), or None."""
    return min(available, key=lambda height: (abs(height - wanted), -height)) if available else None


def image(root, folder, wanted, max_width):
    """A PhotoImage of the logo nearest ``wanted`` pixels high, or None when there is none to show: no
    files, an unreadable or oversized file, or one wider than ``max_width`` or over twice ``wanted`` high."""
    found = sizes(folder)
    height = choose(found, wanted)
    if height is None:
        return None
    path = found[height]
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        picture = tk.PhotoImage(master=root, file=str(path))
    except (OSError, tk.TclError):
        return None
    if not 0 < picture.width() <= max_width or not 0 < picture.height() <= 2 * wanted:
        return None
    return picture
