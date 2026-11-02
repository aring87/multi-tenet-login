"""The product mark, drawn at any size with no image files or extra packages.

A rounded tile in the brand blue carrying a white shield with a check cut out
of it. Every edge is anti-aliased from signed-distance functions and the result
is encoded as a PNG with a real alpha channel, so the same drawing serves the
window and taskbar icon, the sidebar, the About box and the .exe icon.
"""
import math
import struct
import zlib

# Tile gradient, top to bottom, and the tile corner radius as a share of its size.
TILE_TOP = (0x3B, 0x7B, 0xF2)
TILE_BOTTOM = (0x1E, 0x4A, 0xC4)
TILE_RADIUS = 0.22
WHITE = (0xFF, 0xFF, 0xFF)

# Shield geometry in unit coordinates (origin top-left, centre at x = 0.5).
SHIELD_TOP, SHIELD_HALF_WIDTH, SHIELD_SHOULDER, SHIELD_POINT = 0.235, 0.255, 0.50, 0.795
# Check mark: two strokes through the shield, as (x, y) points about the centre line.
CHECK = ((-0.125, 0.49), (-0.04, 0.58), (0.145, 0.385))
CHECK_HALF_WIDTH = 0.036

# The circles whose arcs form the lower half of the shield: centred on the shoulder
# line, offset so each passes through its shoulder corner and the bottom point.
_OFFSET = ((SHIELD_POINT - SHIELD_SHOULDER) ** 2 - SHIELD_HALF_WIDTH ** 2) / (2 * SHIELD_HALF_WIDTH)
_RADIUS = SHIELD_HALF_WIDTH + _OFFSET


def _rounded_square(u, v, radius):
    """Signed distance to a unit square with rounded corners; negative inside."""
    qx, qy = abs(u - 0.5) - (0.5 - radius), abs(v - 0.5) - (0.5 - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _shield(u, v):
    x = u - 0.5
    distance = max(SHIELD_TOP - v, abs(x) - SHIELD_HALF_WIDTH)
    if v > SHIELD_SHOULDER:
        right = math.hypot(x + _OFFSET, v - SHIELD_SHOULDER) - _RADIUS
        left = math.hypot(x - _OFFSET, v - SHIELD_SHOULDER) - _RADIUS
        distance = max(distance, right, left)
    return distance


def _segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _check(u, v):
    x = u - 0.5
    (ax, ay), (bx, by), (cx, cy) = CHECK
    return min(_segment(x, v, ax, ay, bx, by), _segment(x, v, bx, by, cx, cy)) - CHECK_HALF_WIDTH


def _coverage(distance, size):
    return max(0.0, min(1.0, 0.5 - distance * size))


def render_rgba(size, tile=True):
    """Return the mark as ``size`` x ``size`` RGBA bytes (row-major)."""
    rows = bytearray()
    for y in range(size):
        v = (y + 0.5) / size
        mix = v
        tile_colour = tuple(round(TILE_TOP[i] + (TILE_BOTTOM[i] - TILE_TOP[i]) * mix) for i in range(3))
        for x in range(size):
            u = (x + 0.5) / size
            alpha = _coverage(_rounded_square(u, v, TILE_RADIUS), size) if tile else 0.0
            colour = tile_colour
            shield = _coverage(_shield(u, v), size) * (1.0 - _coverage(_check(u, v), size))
            if shield:
                if tile:
                    colour = tuple(round(colour[i] + (WHITE[i] - colour[i]) * shield) for i in range(3))
                else:
                    colour, alpha = WHITE, shield
            rows += bytes((*colour, round(alpha * 255)))
    return bytes(rows)


def _chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def render_png(size, tile=True):
    """The mark as PNG bytes with an alpha channel; Tk's PhotoImage reads these directly."""
    rgba = render_rgba(size, tile)
    stride = size * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def render_ico(sizes=(16, 20, 24, 32, 40, 48, 64, 128, 256)):
    """A Windows .ico holding PNG-compressed images at each size (Vista and later)."""
    images = [(size, render_png(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    return header + entries + payload


def mark(root, size, tile=True):
    """A Tk PhotoImage of the mark, cached on ``root`` per size.

    The cache lives on the Tk instance rather than in a module dictionary: every
    Tk root is called "." and images belong to the interpreter that made them, so
    a process-wide cache would hand a later window (or test) a dead image."""
    import tkinter as tk
    cache = getattr(root, "_brand_marks", None)
    if cache is None:
        cache = root._brand_marks = {}
    key = (size, tile)
    if key not in cache:
        cache[key] = tk.PhotoImage(master=root, data=render_png(size, tile))
    return cache[key]


def window_icons(root, sizes=(16, 20, 24, 32, 48, 64, 128)):
    """Images for ``root.iconphoto``, one per size so Windows picks a crisp one everywhere."""
    return [mark(root, size) for size in sizes]
