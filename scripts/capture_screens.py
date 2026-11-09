"""Drive the desktop app through every screen and dialog with sample data and save a
PNG of each, for design reviews and documentation.

    py scripts\\capture_screens.py <output-folder>

The app runs for real (Windows only) but with its data folder redirected to a temporary
directory and every Azure and GitHub call replaced, so nothing on this PC or in any
tenant or repository is read or changed. Each window is rendered with PrintWindow, so
only the app's own pixels are ever captured, whatever else is on the screen.
"""
import ctypes
import json
import struct
import sys
import tempfile
import threading
import time
import zlib
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    pass

import tkinter as tk  # noqa: E402
from sentinel_app import desktop_app as ui  # noqa: E402
from sentinel_app import desktop_backend as backend  # noqa: E402
from sentinel_app import catalog_page, rule_builder_page  # noqa: E402
from sentinel_app import ui_kit as kit  # noqa: E402
from sentinel_app.repository_catalog import catalog  # noqa: E402
from sentinel_app.gov_onboarding import GOV_CLOUD  # noqa: E402
from sentinel_app.gov_onboarding_dialog import GovOnboardingDialog  # noqa: E402
from sentinel_app.rule_review_dialog import ReviewDialog  # noqa: E402
from sentinel_app.assignment_dialog import AssignmentDialog  # noqa: E402
from sentinel_app.preview_dialog import PreviewDialog  # noqa: E402
from sentinel_app.workflow_history_dialog import WorkflowHistoryDialog  # noqa: E402
from sentinel_app.review_history import ReviewHistoryDialog  # noqa: E402
from test_desktop import WORKSPACE, TENANT, SUB  # noqa: E402
from test_rule_drafts import valid_form  # noqa: E402

user32, gdi32, dwm = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.dwmapi
PW_RENDERFULLCONTENT, DWMWA_EXTENDED_FRAME_BOUNDS = 2, 9


# ----------------------------------------------------------------- capture
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def window_bounds(hwnd):
    """The visible frame of a window, without the invisible resize borders."""
    rect = wintypes.RECT()
    if dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def png(width, height, rgb_rows):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + row for row in rgb_rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def capture(hwnd, path):
    """Render ``hwnd`` into a bitmap with PrintWindow and save it as a PNG, cropped to the
    visible frame. Nothing is read from the screen, so other windows never appear."""
    rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
    full_w, full_h = rect.right - rect.left, rect.bottom - rect.top
    x, y, w, h = window_bounds(hwnd)
    left, top = x - rect.left, y - rect.top
    screen = user32.GetDC(0)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, full_w, full_h)
    gdi32.SelectObject(memory, bitmap)
    user32.PrintWindow(hwnd, memory, PW_RENDERFULLCONTENT)
    info = BITMAPINFOHEADER(); info.biSize = ctypes.sizeof(info); info.biWidth = full_w; info.biHeight = -full_h
    info.biPlanes = 1; info.biBitCount = 32; info.biCompression = 0
    buffer = ctypes.create_string_buffer(full_w * full_h * 4)
    gdi32.GetDIBits(memory, bitmap, 0, full_h, buffer, ctypes.byref(info), 0)
    gdi32.DeleteObject(bitmap); gdi32.DeleteDC(memory); user32.ReleaseDC(0, screen)
    data = buffer.raw
    rows = []
    for row in range(top, top + h):
        line = data[(row * full_w + left) * 4:(row * full_w + left + w) * 4]
        rows.append(bytes(b for pixel in zip(line[2::4], line[1::4], line[0::4]) for b in pixel))
    Path(path).write_bytes(png(w, h, rows))


def settle(root, ms=350):
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        root.update(); time.sleep(0.02)


class Shots:
    def __init__(self, root, folder):
        self.root, self.folder = root, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def take(self, window, name):
        try: window.attributes("-topmost", True)
        except tk.TclError: pass
        window.lift(); window.update_idletasks()
        settle(self.root)
        capture(int(window.wm_frame(), 16), self.folder / (name + ".png"))
        print("captured", name)


# ----------------------------------------------------------------- sample data
def rule(name, display, enabled, severity, kind="Scheduled", **props):
    base = dict(displayName=display, enabled=enabled, severity=severity,
                description="Detects " + display.lower() + " across sign-in and audit telemetry, correlating the actor, "
                            "the target resource and the originating IP so the analyst can triage quickly.",
                query="SigninLogs\n| where ResultType != 0\n| summarize Attempts = count() by UserPrincipalName, IPAddress\n| where Attempts > 20",
                queryFrequency="PT1H", queryPeriod="PT4H", triggerOperator="GreaterThan", triggerThreshold=0,
                tactics=["InitialAccess", "CredentialAccess"], techniques=["T1110", "T1078"],
                lastModifiedUtc="2026-10-28T14:12:00Z")
    base.update(props)
    return dict(name=name, kind=kind, id="/subscriptions/" + SUB + "/rules/" + name, properties=base)


RULES = [rule("r1", "Brute force against Entra ID accounts", True, "High"),
         rule("r2", "Impossible travel sign-in", True, "Medium"),
         rule("r3", "Mass download from SharePoint", False, "Medium"),
         rule("r4", "New global administrator added", True, "High", kind="NRT", queryFrequency=None, queryPeriod=None),
         rule("r5", "Rare process launched by Office application", None, "Low"),
         rule("r6", "Legacy authentication protocol used", True, "Informational"),
         rule("r7", "Suspicious inbox forwarding rule", False, "High"),
         rule("r8", "Anomalous Azure resource deployment", True, "Low"),
         rule("r9", "Password spray detected by Identity Protection", True, "High", kind="Fusion"),
         rule("r10", "Multiple failed MFA challenges", True, "Medium")]


def library():
    rules = {"brute-force": ("Brute force against Entra ID accounts", "High", True),
             "impossible-travel": ("Impossible travel sign-in", "Medium", True),
             "inbox-forwarding": ("Suspicious inbox forwarding rule", "High", False)}
    files = {}
    for index, (slug, (name, severity, enabled)) in enumerate(rules.items(), start=1):
        files["rules/sentinel/" + slug + ".yml"] = json.dumps(dict(
            id=f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111",
            name=name, severity=severity, enabled=enabled)).encode()
    for client, workspace, enabled, assigned in (("acme", "acme-primary", True, ["brute-force", "impossible-travel"]),
                                                 ("bluestone", "bluestone-sentinel", True, ["brute-force"]),
                                                 ("fabrikam", "fabrikam-law", False, ["inbox-forwarding", "brute-force"]),
                                                 ("northwind", "northwind-prod", True, [])):
        target = dict(version=1, target=client + "-primary", client=client, enabled=enabled,
                      azure=dict(workspace_name=workspace),
                      rules=[dict(path="rules/sentinel/" + slug + ".yml") for slug in assigned])
        files["clients/" + client + "/workspace.yml"] = json.dumps(target).encode()
    snapshot = catalog(files, "isi-security/detection-as-code / main", "9f3c2a1", identity="aring")
    snapshot.update(repository="isi-security/detection-as-code", private=True, can_push=True,
                    loaded_at="2026-11-02T14:20:00Z", source="isi-security/detection-as-code / main")
    return snapshot


class Session:
    """Stands in for a signed-in Azure CLI session; nothing is called."""
    signed_in = True
    cloud = "AzureCloud"
    account = {"user": {"name": "analyst@example.com"}}


def fail_github(self, repository, progress=None):
    raise RuntimeError("GitHub read failed (HTTP 404): repository not found or no access.")


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "screens"
    temp = tempfile.TemporaryDirectory()
    data = Path(temp.name)
    (data / "clients.json").write_text(json.dumps([
        {"name": "Acme Corporation", "tenant": TENANT}, {"name": "Bluestone Health", "tenant": "bluestone.onmicrosoft.com"},
        {"name": "Fabrikam Defense (GCC High)", "tenant": "fabrikam.onmicrosoft.us"}]))
    (data / "settings.json").write_text(json.dumps({"github_owner": "isi-security", "github_repo": "detection-as-code"}))
    (data / "ui-state.json").write_text(json.dumps({"geometry": "1360x900+40+20", "client": "Acme Corporation"}))
    patches = [patch.object(ui, "DATA", data), patch.object(backend, "DATA", data),
               patch.object(ui, "create_handoff", return_value=None),
               patch.object(catalog_page.GitHubReader, "load", fail_github),
               patch.object(rule_builder_page.RuleBuilderPage, "github_probe", staticmethod(lambda: "aring"))]
    for p in patches: p.start()
    root = tk.Tk()
    app = ui.App(root)
    root.geometry("1360x900+40+20")  # Clamped to the app's minimum size on a scaled display.
    root.attributes("-topmost", True)
    root.update(); settle(root, 600)
    shots = Shots(root, folder); shot = shots.take
    app.rule_builder.github_checking = True  # The builder never probes GitHub during a capture.

    # ---- Connect: signed out, signing in, connected, GCC High and an error
    app.show_page("connect"); shot(root, "01-connect-signed-out")
    app.signin_active = True; app.signin_cancel = threading.Event(); app.signin_handoff = object()
    app.cancel_signin_button.configure(state="normal"); app.signin_button.configure(state="normal")
    app.signin_panel.pack(fill="x", pady=(ui.px(12), 0), after=app.signin_main)
    app.set_busy(True); app.status.set("Waiting for Microsoft sign-in…"); app.render_session()
    app.log("Waiting for Microsoft sign-in…"); app.log("Sign-in window check: msedge.exe — Sign in to your account (sign-in window)")
    shot(root, "02-connect-signing-in")
    app.set_busy(False); app.finish_signin()

    session = Session(); app.session = session
    app.subscriptions = [dict(id=SUB, tenantId=TENANT, name="Acme Production", state="Enabled", isDefault=True),
                         dict(id="99999999-2222-4222-8222-222222222222", tenantId=TENANT, name="Acme Development", state="Enabled")]
    app.subbox.configure(values=[app.subscription_label(r) for r in app.subscriptions]); app.subbox.current(0)
    app.workspaces = [dict(WORKSPACE), dict(WORKSPACE, workspace_name="law-acme-dev", resource_group="rg-acme-dev")]
    app.wsbox.configure(values=[r["workspace_name"] + "  ·  " + r["resource_group"] for r in app.workspaces]); app.wsbox.current(0)
    app.workspace = dict(WORKSPACE)
    app.identity.set("Signed in: analyst@example.com\n" + app.workspace_rows(dict(tenantId=TENANT, id=SUB), app.workspace))
    app.github_pill.set("Signed in", "success"); app.github_status.set("Signed in to GitHub as aring.")
    app.log("Signed in. Found 2 subscriptions."); app.log("Loading workspaces…")
    app.log("Found 2 workspaces in this subscription. Selected law-example-sentinel.")
    shot(root, "03-connect-connected")
    session.cloud = GOV_CLOUD; app.sync_connection()
    app.status.set("Couldn't finish: AADSTS50076: Due to a configuration change made by your administrator, you must use "
                   "multi-factor authentication.")
    app.activity_badge.set("Stopped", "danger")
    shot(root, "04-connect-gcc-high-error")
    app.status.set("Ready."); app.activity_badge.set("Ready", "success")

    # ---- Dialogs reached from Connect
    dialog = GovOnboardingDialog(app); shot(dialog.window, "05-dialog-gcc-high-empty")
    dialog.show("READ-ONLY PLAN for fabrikam-workspace-gcch\n\nApp registration:   create  ISI Sentinel Pipeline - Preview\n"
                "App registration:   create  ISI Sentinel Pipeline - Deploy\n"
                "Federated credential: create  repo:isi-security@123/detection-as-code@456:environment:sentinel-preview\n"
                "Role assignment:    create  Microsoft Sentinel Reader on rg-example-sentinel\n"
                "Role assignment:    create  Log Analytics Reader on rg-example-sentinel\n"
                "Role assignment:    create  Microsoft Sentinel Contributor on rg-example-sentinel\n"
                "Target file:        create  clients/fabrikam/fabrikam-workspace-gcch.yml\n\nNothing changed.")
    dialog.reviewed = dialog.config(); dialog.controls()
    dialog.say("GCC High onboarding plan ready. Nothing changed. Review the plan, then select Apply reviewed setup.", "success")
    shot(dialog.window, "06-dialog-gcc-high-previewed")
    dialog.say("Stopped. The reason is at the end of the plan below.", "danger"); shot(dialog.window, "07-dialog-gcc-high-error")
    dialog.window.destroy(); session.cloud = "AzureCloud"; app.sync_connection()

    form = kit.FormDialog(root, "New client", "Clients are saved on this PC only. They never contain credentials.",
                          [("name", "Client name", "", "Shown in the client list."),
                           ("tenant", "Tenant ID or verified domain", "", "Optional. Leave blank to discover after sign-in.")],
                          submit="Add client",
                          validate=lambda v: (_ for _ in ()).throw(kit.FieldError("name", "Enter a client name.")))
    shot(form.window, "08-dialog-new-client"); form.submit(); shot(form.window, "09-dialog-new-client-error"); form.window.destroy()
    app.open_about(); about = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    shot(about, "10-dialog-about"); about.destroy()

    # ---- Rules: workspace rules
    app.show_page("rules"); app.rule_view.set("workspace"); root.update()
    page = app.rules_page; page.reset(); shot(root, "11-rules-empty")
    page.summary.set("Loading rules from law-example-sentinel…"); page.empty.show("Loading rules from law-example-sentinel…", busy=True)
    app.set_busy(True); app.status.set("Reading analytics rules…"); shot(root, "12-rules-loading"); app.set_busy(False)
    page.rows = RULES; page.collection = dict(records=RULES, status="collected")
    page.loaded_session = session; page.loaded_workspace = dict(WORKSPACE)
    page.build_cache(); page.render()
    enabled = sum(v[1] == "Enabled" for v, _ in page.cache); disabled = sum(v[1] == "Disabled" for v, _ in page.cache)
    for key, value in (("total", len(RULES)), ("enabled", enabled), ("disabled", disabled), ("unspecified", len(RULES) - enabled - disabled)):
        page.tiles[key].set(str(value))
    page.summary.set("10 rules from law-example-sentinel"); page.sync()
    page.tree.selection_set("0"); page.selected(); root.update()
    shot(root, "13-rules-populated")
    app.scroll_into_view(page.detail_card.frame); shot(root, "14-rules-detail")
    app.page_canvases[1].yview_moveto(0)
    page.notice.set("Couldn't read all the analytics rules: AuthorizationFailed: the client does not have authorization to "
                    "perform action 'Microsoft.SecurityInsights/alertRules/read'")
    page.banner.pack(fill="x", pady=(0, ui.px(12)), before=page.summary_label)
    page.summary.set("Some rules couldn't be read. 10 rules from law-example-sentinel.")
    shot(root, "15-rules-error"); page.banner.pack_forget()
    page.search.set("zzz"); shot(root, "16-rules-no-match"); page.search.set("")

    # ---- Rules: library
    app.rule_view.set("repository"); root.update(); cat = app.catalog_page
    for _ in range(50):
        settle(root, 100)
        if not app.busy: break
    cat.mode.set("GitHub repository"); cat.snapshot = None; cat.render(); cat.note.set("Select Refresh library to load this source.")
    app.status.set("Ready."); app.activity_badge.set("Ready", "success"); shot(root, "17-library-empty")
    cat.snapshot = library(); cat.render()
    cat.note.set("Loaded from isi-security/detection-as-code today at 14:20 as aring. Select Refresh library to get the latest.")
    cat.tree.selection_set("0"); cat.selected(); root.update(); shot(root, "18-library-clients")
    cat.view.set("Rules"); root.update(); cat.tree.selection_set("0"); cat.selected(); root.update(); shot(root, "19-library-rules")
    cat.note.set("The rule library is unavailable: GitHub read failed (HTTP 404): repository not found or no access.")
    cat.empty.show("The rule library couldn't be read. Check the source, then select Refresh library.", ("Refresh library", cat.load))
    shot(root, "20-library-error"); cat.render()
    row = cat.visible[0]
    assign = AssignmentDialog(app, cat.snapshot, row); shot(assign.window, "21-dialog-assign"); assign.window.destroy()
    preview = PreviewDialog(app, cat.snapshot, row); shot(preview.window, "22-dialog-preview"); preview.window.destroy()
    history = WorkflowHistoryDialog(app); settle(root, 800); shot(history.window, "23-dialog-history"); history.window.destroy()

    # ---- Rule builder
    app.show_page("builder"); root.update(); builder = app.rule_builder
    builder.github_checked("aring", None)
    shot(root, "24-builder-new")
    builder.load_form(valid_form()); builder.vars["name"].set("Brute force against Entra ID accounts")
    shot(root, "25-builder-basics-filled")
    builder.steps.select(1); root.update(); shot(root, "26-builder-query")
    builder.steps.select(2); root.update(); shot(root, "27-builder-mitre")
    builder.vars["name"].set(""); builder.validate(); root.update(); shot(root, "28-builder-error")
    builder.vars["name"].set("Brute force against Entra ID accounts"); builder.validate(); root.update()
    shot(root, "29-builder-review-checked")
    builder.github_checked(None, "GitHub read failed. Run gh auth login")  # Not signed in: the toolbar says so.
    shot(root, "30-builder-github-bar"); builder.github_checked("aring", None)
    review = ReviewDialog(builder); shot(review.window, "31-dialog-submit"); review.window.destroy()
    submissions = ReviewHistoryDialog(builder); settle(root, 800); shot(submissions.window, "32-dialog-my-submissions")
    submissions.window.destroy()

    # ---- A large window (the shots above are at the smallest size the app allows)
    root.geometry("1700x1100+20+10"); app.show_page("connect"); settle(root, 500); shot(root, "33-connect-wide")
    app.show_page("rules"); app.rule_view.set("workspace"); settle(root, 500); shot(root, "34-rules-wide")
    app.show_page("builder"); settle(root, 500); shot(root, "35-builder-wide")

    for timer in root.tk.call("after", "info"):
        root.tk.call("after", "cancel", timer)
    root.destroy()
    for p in patches: p.stop()
    temp.cleanup()
    print("Saved to", shots.folder.resolve())


if __name__ == "__main__":
    main()
