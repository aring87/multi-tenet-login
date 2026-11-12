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
import subprocess
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
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))  # System aware, as main() is.
except (AttributeError, OSError):
    pass

import tkinter as tk  # noqa: E402
from sentinel_app import desktop_app as ui  # noqa: E402
from sentinel_app import desktop_backend as backend  # noqa: E402
from sentinel_app import catalog_page, query_runner, rule_builder_page  # noqa: E402
from sentinel_app import ui_kit as kit  # noqa: E402
from sentinel_app import advice  # noqa: E402
from sentinel_app import support_tools as support  # noqa: E402
from sentinel_app.repository_catalog import catalog  # noqa: E402
from sentinel_app.gov_onboarding import GOV_CLOUD  # noqa: E402
from sentinel_app.gov_onboarding_dialog import GovOnboardingDialog  # noqa: E402
from sentinel_app.rule_review_dialog import ReviewDialog  # noqa: E402
from sentinel_app.assignment_dialog import AssignmentDialog  # noqa: E402
from sentinel_app.preview_dialog import PreviewDialog  # noqa: E402
from sentinel_app.workflow_history_dialog import WorkflowHistoryDialog  # noqa: E402
from sentinel_app.review_history import ReviewHistoryDialog  # noqa: E402
from test_desktop import WORKSPACE, TENANT, SUB, WS  # noqa: E402
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
                query='SigninLogs\n| where ResultType != "0"\n| summarize Attempts = count() by UserPrincipalName, IPAddress\n| where Attempts > 20',
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
    # The IDs match the sample workspace rules (RULES), so Drift has something to compare: acme's
    # target is the connected workspace and raises one rule's severity; MFA fatigue isn't deployed yet.
    rules = {"brute-force": ("r1", "Brute force against Entra ID accounts", "High", True),
             "impossible-travel": ("r2", "Impossible travel sign-in", "Medium", True),
             "inbox-forwarding": ("r7", "Suspicious inbox forwarding rule", "High", False),
             "mfa-fatigue": ("44444444-1111-4111-8111-111111111111", "MFA fatigue push notifications", "Medium", True)}
    files = {}
    for slug, (rule_id, name, severity, enabled) in rules.items():
        files["rules/sentinel/" + slug + ".yml"] = json.dumps(dict(
            id=rule_id, name=name, severity=severity, enabled=enabled)).encode()
    acme = [dict(path="rules/sentinel/brute-force.yml"),
            dict(path="rules/sentinel/impossible-travel.yml", overrides=dict(severity="High")),
            dict(path="rules/sentinel/mfa-fatigue.yml")]
    for client, azure, enabled, assigned in (
            ("acme", dict(workspace_name=WORKSPACE["workspace_name"], workspace_id=WS), True, acme),
            ("bluestone", dict(workspace_name="bluestone-sentinel"), True, ["brute-force"]),
            ("fabrikam", dict(workspace_name="fabrikam-law"), False, ["inbox-forwarding", "brute-force"]),
            ("northwind", dict(workspace_name="northwind-prod"), True, [])):
        target = dict(version=1, target=client + "-primary", client=client, enabled=enabled, azure=azure,
                      rules=[item if isinstance(item, dict) else dict(path="rules/sentinel/" + item + ".yml")
                             for item in assigned])
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


TEST_COLUMNS = ["TimeGenerated", "UserPrincipalName", "IPAddress", "ResultType", "Attempts"]
TEST_SYNTAX_ERROR = ('ERROR: Bad Request({"error":{"message":"The request had some invalid properties","code":'
                     '"BadArgumentError","innererror":{"code":"SyntaxError","message":"A recognition error occurred '
                     'in the query.","innererror":{"code":"SYN0002","message":"Query could not be parsed at \'wher\' '
                     'on line [2,2]","line":2,"pos":2,"token":"wher"}}}})')


def test_rows():
    people = ["adele.vance", "alex.wilber", "diego.siciliani", "grady.archie", "isaiah.langer", "megan.bowen"]
    return [["2026-11-09T%02d:%02d:00Z" % (8 + index // 4, (index * 13) % 60), people[index % 6] + "@contoso.com",
             "203.0.113.%d" % (20 + index * 7), "50126", str(24 + index * 3)] for index in range(12)]


def fail_github(self, repository, progress=None):
    raise RuntimeError("GitHub read failed (HTTP 404): repository not found or no access.")


# Check this PC and Copy support info: the tools' answers, so Azure CLI and GitHub CLI never run, and an
# update folder that is never opened.
SHARE = "\\\\fileserver\\SOC\\Sentinel Workspace"
LATEST = dict(version="2.2.0", notes="Check this PC and Copy support info in the sidebar.")
TOOL_ANSWERS = {("version", "--output", "json"): (0, '{"azure-cli": "2.79.0"}', ""),
                ("--version",): (0, "gh version 2.63.0 (2024-11-27)\n", ""),
                ("auth", "status", "--hostname", "github.com"):
                    (1, "", "You are not logged into any GitHub hosts. To log in, run: gh auth login\n")}
TOOLS = {"az": "C:\\Program Files\\Microsoft SDKs\\Azure\\CLI2\\wbin\\az.cmd", "gh": "C:\\Program Files\\GitHub CLI\\gh.exe"}
SAMPLE_ERRORS = """2026-11-08T09:12:03  Sentinel Workspace 2.1.0
Traceback (most recent call last):
  File "sentinel_app\\desktop_app.py", line 1281, in dispatch
    data()
  File "sentinel_app\\rules_page.py", line 210, in loaded
    self.render()
KeyError: 'Brute force against Entra ID accounts'
"""


def answer_tool(args, timeout, env=None):
    code, out, err = TOOL_ANSWERS[tuple(args[1:])]
    return subprocess.CompletedProcess(args, code, out, err)


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
    app.set_busy(True, immediate=True); app.status.set("Waiting for Microsoft sign-in…"); app.render_session()
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
    app.github_pill.set("GitHub: aring", "success"); app.github_status.set("Signed in to GitHub as aring.")
    app.log("Signed in. Found 2 subscriptions."); app.log("Loading workspaces…")
    app.log("Found 2 workspaces in this subscription. Selected law-example-sentinel.")
    shot(root, "03-connect-connected")
    session.cloud = GOV_CLOUD; app.sync_connection()
    mfa = ("AADSTS50076: Due to a configuration change made by your administrator, or because you moved to a new "
           "location, you must use multi-factor authentication to access the resource.")
    app.status.set("Couldn't finish: " + advice.first_sentence(advice.friendly(mfa)))
    app.activity_badge.set("Stopped", "danger"); app.show_signin_error(mfa, advice.lookup(mfa))
    shot(root, "04-connect-gcc-high-error")
    app.status.set("Ready."); app.activity_badge.set("Ready", "success"); app.hide_signin_error()

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
    dialog.failed(dialog.review.get("1.0", "end-1c").replace("\n\nNothing changed.", "") +
                  "\nSTOPPED: Role assignment failed: AuthorizationFailed. The signed-in account can't assign roles on "
                  "rg-example-sentinel.\nAsk the client's administrator for Owner or User Access Administrator, then "
                  "select Preview again.")
    dialog.controls(); shot(dialog.window, "07-dialog-gcc-high-error")
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
    app.set_busy(True, immediate=True); app.status.set("Reading analytics rules…"); shot(root, "12-rules-loading")
    app.set_busy(False)
    page.rows = RULES; page.collection = dict(records=RULES, status="collected")
    page.loaded_session = session; page.loaded_workspace = dict(WORKSPACE)
    page.build_cache(); page.render(); page.label_filters()
    page.summary.set("10 rules from law-example-sentinel"); page.sync(); root.update()
    shot(root, "13-rules-populated")
    first = page.tree.get_children()[0]
    page.tree.selection_set(first); page.tree.focus(first); page.selected(); root.update(); shot(root, "14-rules-detail")
    page.show_notice(advice.friendly("AuthorizationFailed: the client does not have authorization to perform action "
                                     "'Microsoft.SecurityInsights/alertRules/read'", workspace="law-example-sentinel"))
    page.summary.set("Some rules couldn't be read. 10 rules from law-example-sentinel.")
    shot(root, "15-rules-error"); page.banner.pack_forget()
    page.search.set("zzz"); shot(root, "16-rules-no-match"); page.search.set("")

    # ---- Rules: library
    app.rule_view.set("repository"); root.update(); cat = app.catalog_page
    for _ in range(50):
        settle(root, 100)
        if not app.busy: break
    cat.mode.set("GitHub repository"); cat.invalidate(); cat.show_source(False)  # Saved, not read yet: folded.
    app.status.set("Ready."); app.activity_badge.set("Ready", "success"); shot(root, "17-library-empty")
    snapshot = library(); cat.snapshot = snapshot; cat.view.set("Clients"); cat.render()
    cat.loaded(snapshot, "isi-security/detection-as-code")
    cat.tree.selection_set("0"); cat.selected(); root.update(); shot(root, "18-library-clients")
    cat.view.set("Rules"); root.update(); cat.tree.selection_set("0"); cat.selected(); root.update(); shot(root, "19-library-rules")
    cat.snapshot = None; cat.render()
    cat.failed("GitHub read failed (HTTP 404): repository not found or no access.")
    shot(root, "20-library-error")
    cat.hide_error(); cat.snapshot = snapshot; cat.render(); cat.loaded(snapshot, "isi-security/detection-as-code")
    row = cat.visible[0]
    assign = AssignmentDialog(app, cat.snapshot, row); shot(assign.window, "21-dialog-assign"); assign.window.destroy()
    preview = PreviewDialog(app, cat.snapshot, row); shot(preview.window, "22-dialog-preview"); preview.window.destroy()
    history = WorkflowHistoryDialog(app); settle(root, 800); shot(history.window, "23-dialog-history"); history.window.destroy()

    # ---- Rules: drift between the library's acme target and the workspace rules read above
    app.rule_view.set("drift"); root.update(); drift = app.drift_page
    different = next(iid for iid in drift.tree.get_children() if drift.entries[int(iid)]["kind"] == "different")
    drift.tree.selection_set(different); drift.tree.focus(different); drift.selected(); root.update()
    shot(root, "39-rules-drift")
    # A library rule live here but not assigned to acme: Assign to this client…, and the dialog it opens.
    unassigned = next((iid for iid in drift.tree.get_children()
                       if drift.entries[int(iid)]["kind"] == "live_only" and drift.entries[int(iid)]["rule"]), None)
    if unassigned is not None:
        drift.tree.selection_set(unassigned); drift.tree.focus(unassigned); drift.selected(); root.update()
        shot(root, "39b-rules-drift-assign")
        opened = []
        with patch("sentinel_app.drift_page.AssignmentDialog",
                   side_effect=lambda *args, **kwargs: opened.append(AssignmentDialog(*args, **kwargs))):
            drift.action_button.invoke()  # As the button opens it: this client ticked, at the rule's live state.
        shot(opened[0].window, "39c-drift-assign-dialog"); opened[0].window.destroy()

    # ---- Rule builder
    app.show_page("builder"); root.update(); builder = app.rule_builder
    builder.github_checked("aring", None)
    shot(root, "24-builder-new")
    builder.load_form(valid_form()); builder.vars["name"].set("Brute force against Entra ID accounts")
    shot(root, "25-builder-basics-filled")
    builder.steps.select(1); root.update(); shot(root, "26-builder-query")
    # Run test query, answered here with sample rows and a sample syntax error (no Azure call).
    query = builder.texts["query"].get("1.0", "end-1c")
    builder.test_passed(dict(columns=TEST_COLUMNS, rows=test_rows(), more=False, partial=None, elapsed=1.84,
                             seconds=query_runner.test_window("Scheduled", builder.vars["queryPeriod"].get()),
                             workspace_name=WORKSPACE["workspace_name"]), session, query)
    root.update(); shot(root, "45-builder-test-query")
    builder.test_failed(query_runner.failure(TEST_SYNTAX_ERROR), dict(WORKSPACE), query, session)
    root.update(); shot(root, "46-builder-test-query-error")
    builder.clear_test(); app.root.update(); app.page_canvases[2].yview_moveto(0)
    # The KQL check under the query box, with a misspelt table and step.
    kit.set_text(builder.texts["query"], 'SignLogs\n| wher ResultType != "0"\n| summarize Attempts = count() by UserPrincipalName')
    builder.check_kql(); root.update(); shot(root, "47-builder-kql-check")
    # The completion list while a column is typed, and the Query box in another theme.
    box = builder.texts["query"]
    kit.set_text(box, 'SigninLogs\n| where ResultType != "0"\n| where Us'); builder.check_kql()
    box.focus_force(); root.update()  # The list closes when the box loses the keyboard: let the focus settle first.
    box.mark_set("insert", "end-1c"); builder.editor.suggest(); root.update()
    shot(root, "48-builder-kql-complete"); builder.editor.hide()
    builder.set_kql_theme("Midnight"); kit.set_text(box, query); builder.check_kql(); root.update()
    shot(root, "49-builder-kql-midnight"); builder.set_kql_theme("Daylight")
    kit.set_text(builder.texts["query"], query); builder.check_kql(); builder.clear_test()
    app.status.set("Ready."); app.activity_badge.set("Ready", "success")  # Not the test's flash.
    builder.steps.select(2); root.update(); shot(root, "27-builder-mitre")
    builder.vars["name"].set(""); builder.validate(); root.update(); shot(root, "28-builder-error")
    builder.vars["name"].set("Brute force against Entra ID accounts"); builder.validate(); root.update()
    shot(root, "29-builder-review-checked")
    builder.github_checked(None, "GitHub read failed. Run gh auth login")  # Not signed in: the toolbar says so.
    shot(root, "30-builder-github-bar"); builder.github_checked("aring", None)
    review = ReviewDialog(builder); shot(review.window, "31-dialog-submit"); review.window.destroy()
    submissions = ReviewHistoryDialog(builder); settle(root, 800); shot(submissions.window, "32-dialog-my-submissions")
    submissions.window.destroy()

    # ---- Help, a failed task, and the offer to restore unsaved builder work
    help_window = app.open_help(); shot(help_window, "36-dialog-help"); help_window.destroy()
    raw = "AuthorizationFailed: the client does not have authorization to perform action 'Microsoft.SecurityInsights/alertRules/read'"
    alert = kit.MessageDialog(root, "Couldn't read analytics rules", advice.friendly(raw, workspace="law-example-sentinel"),
                              details=raw, tone="danger")
    shot(alert.window, "37-dialog-task-error"); alert.window.destroy()
    restore = kit.MessageDialog(root, "Restore unsaved rule?", "Sentinel Workspace closed before your changes to "
                                "“Brute force against Entra ID accounts” were saved. Restore the copy kept on "
                                "02 Nov 2026 at 14:20?", tone="info", icon="help", ok="Restore", cancel="Discard")
    shot(restore.window, "38-dialog-restore"); restore.window.destroy()

    # ---- Support tools: the update notice on Connect, Check this PC and Copy support info
    with patch.object(support, "_run", answer_tool), patch.object(support.shutil, "which", TOOLS.get), \
         patch.object(support, "read_latest", lambda folder: dict(LATEST)), tempfile.TemporaryDirectory() as logs:
        app.settings["update_folder"] = SHARE
        app.show_page("connect"); app.update_checked(SHARE, dict(LATEST), None); settle(root)
        shot(root, "41-connect-update-notice")
        with patch.object(threading.Thread, "start", lambda thread: None):  # Caught while it checks.
            check = app.open_check_pc()
        shot(check.window, "42-dialog-check-pc-checking")
        support.run_checks(data, SHARE, report=check.show); check.finished()
        shot(check.window, "43-dialog-check-pc-results")
        logged, app.error_log = app.error_log, Path(logs) / "errors.log"
        app.error_log.write_text(SAMPLE_ERRORS, encoding="utf-8")
        info = app.support_info_dialog(app.support_info([check.checks[key] for key, _ in support.CHECKS]), check.window)
        shot(info.window, "44-dialog-support-info"); info.window.destroy(); check.window.destroy()
        app.error_log = logged
        del app.settings["update_folder"]; app.check_for_update()

    # ---- A large window (the shots above are at the smallest size the app allows)
    app.toggle_ids(True)  # The workspace IDs, opened.
    root.geometry("1700x1100+20+10"); app.show_page("connect"); settle(root, 500); shot(root, "33-connect-wide")
    app.show_page("rules"); app.rule_view.set("workspace"); settle(root, 500); shot(root, "34-rules-wide")
    app.rule_view.set("drift"); settle(root, 500); shot(root, "40-rules-drift-wide")
    app.show_page("builder"); settle(root, 500); shot(root, "35-builder-wide")
    # A very wide window: the builder takes the width, and test results get the room.
    root.geometry("2200x1250+0+0"); builder.steps.select(1); settle(root, 500)
    builder.test_passed(dict(columns=TEST_COLUMNS, rows=test_rows(), more=False, partial=None, elapsed=1.84,
                             seconds=query_runner.test_window("Scheduled", builder.vars["queryPeriod"].get()),
                             workspace_name=WORKSPACE["workspace_name"]), session, query)
    settle(root, 500); shot(root, "48-builder-test-query-extra-wide")

    for timer in root.tk.call("after", "info"):
        root.tk.call("after", "cancel", timer)
    root.destroy()
    # A failing Tk callback only shows as 'Something went wrong' in a shot; print it here too.
    log = data / "logs" / "errors.log"
    errors = log.read_text(encoding="utf-8") if log.exists() else ""
    for p in patches: p.stop()
    temp.cleanup()
    print("Saved to", shots.folder.resolve())
    if errors:
        print("Unexpected errors during the capture:", errors, sep=chr(10), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
