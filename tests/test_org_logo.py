"""The optional organization logo in the sidebar, and the script that makes its pre-sized set.
Every image here is generated; no logo file is read from the repository."""
import struct
import sys
import tempfile
import tkinter as tk
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import make_logo_sizes as sizes_script  # noqa: E402
from sentinel_app import desktop_app as ui  # noqa: E402
from sentinel_app import org_logo  # noqa: E402

WHITE, CLEAR = bytes((255, 255, 255, 255)), bytes(4)


def png(width, height, rows, colour=6, kind=0, palette=None, alpha=None):
    """PNG bytes for raw rows in ``colour`` type, every row written with filter ``kind`` (0-4)."""
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    out, prev = b"", bytes(width * bpp)
    for row in rows:
        row, line = bytes(row), bytearray()
        for i, x in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            p = a + b - c
            paeth = a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else b if abs(p - b) <= abs(p - c) else c
            line.append((x - (0, a, b, (a + b) // 2, paeth)[kind]) & 255)
        out += bytes([kind]) + bytes(line)
        prev = row

    def chunk(name, payload):
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0))
    if palette: body += chunk(b"PLTE", palette)
    if alpha: body += chunk(b"tRNS", alpha)
    return sizes_script.SIGNATURE + body + chunk(b"IDAT", zlib.compress(out)) + chunk(b"IEND", b"")


def block_logo(width=120, height=40, margin=0):
    """An RGBA logo: an opaque white bar on the left half, clear elsewhere, inside ``margin`` clear pixels."""
    rows = []
    for y in range(height + 2 * margin):
        inside = margin <= y < margin + height
        rows.append(b"".join(WHITE if inside and margin <= x < margin + width // 2 else CLEAR
                             for x in range(width + 2 * margin)))
    return width + 2 * margin, height + 2 * margin, rows


class LogoChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_a_full_set_covers_each_display_scaling_step(self):
        self.assertEqual(org_logo.heights(), [20, 25, 30, 35, 40, 45, 50, 60])

    def test_the_nearest_height_is_chosen_and_the_larger_of_a_tie(self):
        self.assertEqual(org_logo.choose({20: 0, 25: 0, 30: 0}, 27), 25)
        self.assertEqual(org_logo.choose({20: 0, 30: 0}, 25), 30)
        self.assertEqual(org_logo.choose({40: 0}, 20), 40)
        self.assertIsNone(org_logo.choose({}, 20))

    def test_only_logo_height_png_files_count(self):
        for name in ("logo-20.png", "LOGO-25.PNG", "logo.png", "logo-x.png", "logo-0.png", "notes.txt", "logo-30.png.bak"):
            (self.folder / name).write_bytes(b"x")
        (self.folder / "logo-40.png").mkdir()
        self.assertEqual(sorted(org_logo.sizes(self.folder)), [20, 25])
        self.assertEqual(org_logo.sizes(self.folder / "missing"), {})


class LogoImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = tk.Tk(); self.root.withdraw(); self.addCleanup(self.root.destroy)

    def write(self, height, width):
        w, h, rows = block_logo(width, height)
        (self.folder / f"logo-{height}.png").write_bytes(sizes_script.encode(w, h, rows))

    def test_the_logo_nearest_the_wanted_height_is_shown(self):
        self.write(20, 56); self.write(30, 85)
        picture = org_logo.image(self.root, self.folder, 28, max_width=200)
        self.assertEqual((picture.width(), picture.height()), (85, 30))

    def test_nothing_is_shown_for_a_missing_broken_oversized_or_too_wide_logo(self):
        self.assertIsNone(org_logo.image(self.root, self.folder / "missing", 20, max_width=200))
        (self.folder / "logo-20.png").write_bytes(b"not a png")
        self.assertIsNone(org_logo.image(self.root, self.folder, 20, max_width=200))
        self.write(20, 300)
        self.assertIsNone(org_logo.image(self.root, self.folder, 20, max_width=200))  # Wider than the sidebar.
        (self.folder / "logo-20.png").write_bytes(b"\0" * (org_logo.MAX_BYTES + 1))
        self.assertIsNone(org_logo.image(self.root, self.folder, 20, max_width=200))
        (self.folder / "logo-20.png").unlink(); self.write(60, 56)  # The only file is far taller than wanted.
        self.assertIsNone(org_logo.image(self.root, self.folder, 20, max_width=200))


class SidebarLogoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        p = patch.object(ui, "DATA", self.data); p.start(); self.addCleanup(p.stop)

    def app(self):
        root = tk.Tk(); root.withdraw()
        app = ui.App(root); root.update()
        self.addCleanup(self.close, app)
        return app

    @staticmethod
    def close(app):
        app.set_busy(False)
        for timer in app.root.tk.call("after", "info"): app.root.after_cancel(timer)
        app.root.destroy()

    def labels_showing(self, app, picture):
        found, stack = [], [app.root]
        while stack:
            widget = stack.pop(); stack.extend(widget.winfo_children())
            if isinstance(widget, tk.Label) and picture is not None and str(widget.cget("image")) == str(picture):
                found.append(widget)
        return found

    def test_no_logo_by_default(self):
        self.assertIsNone(self.app().org_logo)

    def test_the_branding_folder_logo_sits_under_the_sidebar_links(self):
        sizes_script.make_sizes(self.write_source(), self.data / org_logo.FOLDER)
        app = self.app()
        self.assertIsNotNone(app.org_logo)
        self.assertEqual(app.org_logo.height(), org_logo.choose(org_logo.heights(), ui.px(org_logo.HEIGHT)))
        label, = self.labels_showing(app, app.org_logo)
        order = label.master.pack_slaves()
        self.assertIs(order[-1], label)  # Packed last in the sidebar footer: under the links.
        self.assertIn("About Sentinel Workspace", [w.cget("text") for w in order[:-1] if "text" in w.keys()])

    def write_source(self):
        w, h, rows = block_logo(240, 80, margin=10)
        path = self.data / "source.png"; path.write_bytes(sizes_script.encode(w, h, rows))
        return path


class MakeLogoSizesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_every_row_filter_and_colour_type_decodes(self):
        rows = [bytes((x * 37 + y * 11) & 255 for x in range(12)) for y in range(5)]  # 3 RGBA pixels a row.
        for kind in range(5):
            with self.subTest(kind=kind):
                self.assertEqual(sizes_script.decode(png(3, 5, rows, kind=kind)), (3, 5, rows))
        grey_alpha = png(2, 1, [bytes((10, 200, 30, 0))], colour=4, kind=4)
        self.assertEqual(sizes_script.decode(grey_alpha)[2], [bytes((10, 10, 10, 200, 30, 30, 30, 0))])
        rgb = png(1, 2, [bytes((1, 2, 3)), bytes((4, 5, 6))], colour=2, kind=2)
        self.assertEqual(sizes_script.decode(rgb)[2], [bytes((1, 2, 3, 255)), bytes((4, 5, 6, 255))])
        paletted = png(2, 1, [bytes((1, 0))], colour=3, palette=bytes((9, 9, 9, 250, 0, 0)), alpha=bytes((0,)))
        self.assertEqual(sizes_script.decode(paletted)[2], [bytes((250, 0, 0, 255, 9, 9, 9, 0))])  # Index 1, then 0.

    def test_unsupported_pngs_are_refused_plainly(self):
        sixteen = bytearray(png(1, 1, [bytes(4)])); sixteen[24] = 16  # IHDR bit depth.
        with self.assertRaisesRegex(ValueError, "8-bit, non-interlaced"): sizes_script.decode(bytes(sixteen))
        with self.assertRaisesRegex(ValueError, "Not a PNG"): sizes_script.decode(b"GIF89a")

    def test_shrinking_averages_coverage_without_darkening_edges(self):
        rows = [WHITE + CLEAR, CLEAR + WHITE]  # Half the pixels white, half clear (black underneath).
        self.assertEqual(sizes_script.shrink(2, 2, rows, 1), (1, 1, [bytes((255, 255, 255, 128))]))
        w, h, small = sizes_script.shrink(*block_logo(120, 40), 20)
        self.assertEqual((w, h), (60, 20))
        self.assertEqual(small[10][4 * 5:4 * 6], WHITE)    # Inside the bar.
        self.assertEqual(small[10][4 * 45:4 * 46], CLEAR)  # Outside it.

    def test_the_set_is_trimmed_and_written_for_each_height_up_to_the_source(self):
        w, h, rows = block_logo(240, 40, margin=8)  # 40 high once its clear margin is trimmed.
        source = self.folder / "logo.png"; source.write_bytes(sizes_script.encode(w, h, rows))
        made = sizes_script.make_sizes(source, self.folder / "branding")
        self.assertEqual(made, {20: 60, 25: 75, 30: 90, 35: 105, 40: 120})  # 45 and up would be enlarged.
        written = sorted(p.name for p in (self.folder / "branding").iterdir())
        self.assertEqual(written, [f"logo-{h}.png" for h in (20, 25, 30, 35, 40)])
        self.assertEqual(sizes_script.decode((self.folder / "branding" / "logo-40.png").read_bytes())[:2], (120, 40))

    def test_unusable_logos_explain_themselves(self):
        clear = self.folder / "clear.png"; clear.write_bytes(sizes_script.encode(4, 4, [CLEAR * 4] * 4))
        with self.assertRaisesRegex(ValueError, "fully transparent"): sizes_script.make_sizes(clear, self.folder / "b")
        tiny = self.folder / "tiny.png"; tiny.write_bytes(sizes_script.encode(4, 4, [WHITE * 4] * 4))
        with self.assertRaisesRegex(ValueError, "only 4 pixels high"): sizes_script.make_sizes(tiny, self.folder / "b")
        (self.folder / "bad.png").write_bytes(b"nope")
        with patch("sys.stderr"):
            self.assertEqual(sizes_script.main([str(self.folder / "bad.png"), str(self.folder / "b")]), 1)


if __name__ == "__main__":
    unittest.main()
