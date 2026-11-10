"""Make the sidebar logo set from one large PNG: logo-20.png, logo-25.png … logo-60.png.

    py scripts\\make_logo_sizes.py <logo.png> <folder>

<folder> is the desktop-data\\branding folder of the analyst package (or your own, to see it
when running from source). The app shows the file nearest the height it needs at the display's
scaling (sentinel_app/org_logo.py). Each size is shrunk by averaging every source pixel it
covers, so edges stay smooth; fully transparent margins are trimmed first. Use a logo made for
dark backgrounds (usually the white version): the sidebar is dark navy.

Python only, no image libraries: reads 8-bit PNGs (grey, RGB, palette, with or without alpha),
not interlaced. A large logo takes a few seconds."""
import argparse
import itertools
import operator
import struct
import sys
import zlib
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sentinel_app import org_logo  # noqa: E402

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # PNG colour type -> samples per pixel.


def chunks(data):
    """(type, payload) for each chunk of a PNG file's bytes."""
    if not data.startswith(SIGNATURE):
        raise ValueError("Not a PNG file.")
    pos = len(SIGNATURE)
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        yield kind, data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IEND":
            return
    raise ValueError("The PNG file is cut short.")


def unfilter(raw, width, height, bpp):
    """The image rows (bytearrays) from a PNG's decompressed data, undoing each row's filter."""
    stride = width * bpp
    if len(raw) < height * (stride + 1):
        raise ValueError("The PNG image data is cut short.")
    rows, prev, pos = [], bytearray(stride), 0
    for _ in range(height):
        kind, row = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if kind == 1:    # Sub
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 255
        elif kind == 2:  # Up
            row = bytearray(map(lambda x, b: (x + b) & 255, row, prev))
        elif kind == 3:  # Average
            for i in range(stride):
                row[i] = (row[i] + ((row[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif kind == 4:  # Paeth
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif kind != 0:
            raise ValueError(f"Unknown PNG row filter {kind}.")
        rows.append(row)
        prev = row
    return rows


def decode(data):
    """(width, height, rows) for PNG bytes, each row RGBA bytes."""
    header, palette, alpha, compressed = None, None, None, []
    for kind, payload in chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload[:13])
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            alpha = payload
        elif kind == b"IDAT":
            compressed.append(payload)
    if header is None:
        raise ValueError("The PNG file has no header.")
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour not in CHANNELS or interlace:
        raise ValueError("Save the logo as an 8-bit, non-interlaced PNG.")
    if colour == 3 and not palette:
        raise ValueError("The PNG file's palette is missing.")
    rows = unfilter(zlib.decompress(b"".join(compressed)), width, height, CHANNELS[colour])
    rgba = []
    for row in rows:
        if colour == 6:
            rgba.append(bytes(row)); continue
        out = bytearray(width * 4)
        if colour == 2:
            out[0::4], out[1::4], out[2::4] = row[0::3], row[1::3], row[2::3]
            out[3::4] = bytes([255]) * width
        elif colour in (0, 4):
            grey = row[0::CHANNELS[colour]]
            out[0::4] = out[1::4] = out[2::4] = grey
            out[3::4] = row[1::2] if colour == 4 else bytes([255]) * width
        else:  # Palette, with tRNS alpha per entry.
            for x, index in enumerate(row):
                out[4 * x:4 * x + 3] = palette[3 * index:3 * index + 3]
                out[4 * x + 3] = alpha[index] if alpha and index < len(alpha) else 255
        rgba.append(bytes(out))
    return width, height, rgba


def encode(width, height, rows):
    """PNG bytes for RGBA rows (bytes of width * 4)."""
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def trim(width, height, rows):
    """The image cropped to the pixels that aren't fully transparent."""
    used = [y for y, row in enumerate(rows) if any(row[3::4])]
    if not used:
        raise ValueError("The logo is fully transparent.")
    top, bottom = used[0], used[-1] + 1
    columns = bytearray(width)
    for row in rows[top:bottom]:
        columns = bytearray(map(operator.or_, columns, row[3::4]))
    left = next(x for x in range(width) if columns[x])
    right = max(x for x in range(width) if columns[x]) + 1
    return right - left, bottom - top, [row[4 * left:4 * right] for row in rows[top:bottom]]


def shrink(width, height, rows, new_height):
    """RGBA rows ``new_height`` high (width in proportion), each pixel the alpha-weighted average of the
    source pixels it covers."""
    new_width = max(1, round(width * new_height / height))
    ys = [round(j * height / new_height) for j in range(new_height + 1)]
    xs = [round(i * width / new_width) for i in range(new_width + 1)]
    out = []
    for j in range(new_height):
        sums = [[0] * width for _ in range(4)]  # Premultiplied red, green, blue, then alpha, per column.
        for row in rows[ys[j]:ys[j + 1]]:
            a = row[3::4]
            for channel in range(3):
                sums[channel] = list(map(operator.add, sums[channel], map(operator.mul, row[channel::4], a)))
            sums[3] = list(map(operator.add, sums[3], a))
        prefix = [[0, *itertools.accumulate(column)] for column in sums]
        line = bytearray(new_width * 4)
        for i in range(new_width):
            area = (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j])
            r, g, b, alpha = (p[xs[i + 1]] - p[xs[i]] for p in prefix)
            if alpha and area:
                line[4 * i:4 * i + 4] = bytes((round(r / alpha), round(g / alpha), round(b / alpha),
                                               min(255, round(alpha / area))))
        out.append(bytes(line))
    return new_width, new_height, out


def make_sizes(source, folder, heights=None):
    """Write logo-<height>.png into ``folder`` for each height (no larger than the source), and return
    {height: width}."""
    width, height, rows = trim(*decode(Path(source).read_bytes()))
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    made = {}
    for target in heights or org_logo.heights():
        if target > height:
            continue
        w, h, small = shrink(width, height, rows, target)
        (folder / f"logo-{target}.png").write_bytes(encode(w, h, small))
        made[target] = w
    if not made:
        raise ValueError(f"The logo is only {height} pixels high; use one at least {min(org_logo.heights())} high.")
    return made


def main(argv=None):
    parser = argparse.ArgumentParser(description="Make the sidebar logo set from one large PNG.")
    parser.add_argument("source", help="The logo PNG, for example the white version for dark backgrounds")
    parser.add_argument("folder", help="Where to write logo-<height>.png, normally desktop-data\\branding")
    args = parser.parse_args(argv)
    try:
        made = make_sizes(args.source, args.folder)
    except (OSError, ValueError, zlib.error) as error:
        print("Couldn't make the logo set:", error, file=sys.stderr)
        return 1
    print("Wrote", ", ".join(f"logo-{h}.png ({w}x{h})" for h, w in sorted(made.items())), "to", args.folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
