"""Support tools: Check this PC, Copy support info and the update notice. Azure CLI and GitHub CLI
are never run (subprocess.run is replaced), and every folder is a temporary one."""
import json
import subprocess
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sentinel_app import __version__
from sentinel_app import desktop_app as ui
from sentinel_app import desktop_backend as backend
from sentinel_app import rule_builder_page
from sentinel_app import support_tools as support
from test_desktop import TENANT

AZ, GH = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd", r"C:\Program Files\GitHub CLI\gh.exe"
AZ_VERSION = ("version", "--output", "json")
GH_VERSION = ("--version",)
GH_STATUS = ("auth", "status", "--hostname", "github.com")
SIGNED_IN = ("github.com\n  ✓ Logged in to github.com account octo-analyst (keyring)\n  - Active account: true\n"
             "  - Git operations protocol: https\n  - Token: gho_************************************\n")
NOT_SIGNED_IN = "You are not logged into any GitHub hosts. To log in, run: gh auth login\n"


def answers(az="2.79.0", gh="2.63.0", status=(0, SIGNED_IN, "")):
    """subprocess.run answers keyed by the arguments after the executable."""
    return {AZ_VERSION: (0, json.dumps({"azure-cli": az, "azure-cli-core": az, "extensions": {}}), "") if az else (2, "", "az: 'version' is not an az command."),
            GH_VERSION: (0, f"gh version {gh} (2024-11-27)\nhttps://github.com/cli/cli/releases/tag/v{gh}\n", ""),
            GH_STATUS: status}


def fake_run(responses, calls=None):
    def run(args, **kwargs):
        if calls is not None: calls.append((tuple(args), kwargs))
        value = responses[tuple(args[1:])]
        if isinstance(value, BaseException): raise value
        code, out, err = value
        return subprocess.CompletedProcess(args, code, out, err)
    return run


def tools(az=AZ, gh=GH):
    return patch.object(support.shutil, "which", lambda name: dict(az=az, gh=gh).get(name))


class VersionTests(unittest.TestCase):
    def test_versions_compare_as_numbers(self):
        self.assertEqual(support.version_tuple("2.76.0"), (2, 76, 0))
        self.assertIsNone(support.version_tuple("latest"))
        self.assertTrue(support.newer("2.10.0", "2.9.9"))
        self.assertFalse(support.newer("2.2", "2.2.0"))
        self.assertFalse(support.newer("2.1.0", "2.2.0"))
        self.assertFalse(support.newer("not a version", "2.0.0"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def run_check(self, function, exe, responses):
        calls = []
        with patch.object(support.subprocess, "run", fake_run(responses, calls)):
            return function(exe), calls

    # ------------------------------------------------------------ Azure CLI
    def test_azure_cli_2_76_or_later_is_ok_and_runs_without_the_analysts_config(self):
        check, calls = self.run_check(support.check_azure_cli, AZ, answers(az="2.79.0"))
        self.assertTrue(check.ok); self.assertIn("2.79.0 is installed", check.detail); self.assertEqual(check.support, "2.79.0")
        (args, kwargs), = calls
        self.assertEqual(args, (AZ,) + AZ_VERSION)
        env = kwargs["env"]
        self.assertEqual(env["AZURE_CORE_COLLECT_TELEMETRY"], "false")
        self.assertIn("sentinel-check-", env["AZURE_CONFIG_DIR"])  # A throwaway folder, not ~/.azure.
        self.assertFalse(Path(env["AZURE_CONFIG_DIR"]).exists())  # And it is gone again.
        self.assertIs(kwargs["shell"], False); self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_old_missing_and_broken_azure_cli_need_action_with_a_fix(self):
        old, _ = self.run_check(support.check_azure_cli, AZ, answers(az="2.70.1"))
        self.assertFalse(old.ok); self.assertIn("2.70.1 is installed, but 2.76 or later is needed", old.detail)
        self.assertIn("reopen this app", old.detail)
        missing = support.check_azure_cli(None)
        self.assertFalse(missing.ok); self.assertTrue(missing.detail.startswith("Not installed."))
        broken, _ = self.run_check(support.check_azure_cli, AZ, answers(az=None))
        self.assertFalse(broken.ok); self.assertIn("didn't report its version", broken.detail)
        slow, _ = self.run_check(support.check_azure_cli, AZ, {AZ_VERSION: subprocess.TimeoutExpired("az", 60)})
        self.assertFalse(slow.ok); self.assertIn("within 60 seconds", slow.detail)

    # ------------------------------------------------------------ GitHub CLI
    def test_github_cli_version_and_absence(self):
        check, calls = self.run_check(support.check_github_cli, GH, answers(gh="2.63.0"))
        self.assertTrue(check.ok); self.assertEqual(check.support, "2.63.0")
        self.assertEqual(calls[0][1]["env"]["GH_NO_UPDATE_NOTIFIER"], "1")  # No release check against GitHub.
        missing = support.check_github_cli(None)
        self.assertFalse(missing.ok); self.assertIn("install GitHub CLI", missing.detail)

    def test_github_sign_in_states(self):
        signed_in, calls = self.run_check(support.check_github_signin, GH, answers())
        self.assertEqual(calls[0][0], (GH,) + GH_STATUS)
        self.assertTrue(signed_in.ok); self.assertEqual(signed_in.detail, "Signed in as octo-analyst.")
        self.assertNotIn("octo-analyst", signed_in.support)  # The account never reaches the support info.
        older, _ = self.run_check(support.check_github_signin, GH,
                                  answers(status=(0, "", "github.com\n  ✓ Logged in to github.com as octo-old (keyring)\n")))
        self.assertEqual(older.detail, "Signed in as octo-old.")
        out, _ = self.run_check(support.check_github_signin, GH, answers(status=(1, "", NOT_SIGNED_IN)))
        self.assertFalse(out.ok); self.assertEqual(out.action, "github_login"); self.assertIn("Not signed in", out.detail)
        invalid = ("github.com\n  X Failed to log in to github.com account octo-analyst (keyring)\n"
                   "  - The token in keyring is invalid.\n")
        stale, _ = self.run_check(support.check_github_signin, GH, answers(status=(1, invalid, "")))
        self.assertFalse(stale.ok); self.assertIn("no longer works", stale.detail); self.assertEqual(stale.action, "github_login")
        offline, _ = self.run_check(support.check_github_signin, GH,
                                    answers(status=(1, "X Timeout trying to log in to github.com account x\n", "")))
        self.assertIn("couldn't reach GitHub", offline.detail); self.assertEqual(offline.action, "")
        self.assertFalse(support.check_github_signin(None).ok)

    # ------------------------------------------------------------ data folder
    def test_data_folder_is_written_once_and_left_clean(self):
        check = support.check_data_folder(self.folder)
        self.assertTrue(check.ok); self.assertEqual(list(self.folder.iterdir()), [])
        with patch.object(support.Path, "write_text", side_effect=PermissionError(13, "Access is denied")):
            check = support.check_data_folder(self.folder)
        self.assertFalse(check.ok); self.assertIn("Access is denied", check.detail); self.assertIn("write access", check.detail)

    # ------------------------------------------------------------ update folder
    def latest(self, value):
        (self.folder / "latest.json").write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def test_latest_json_is_read_and_its_notes_kept_short(self):
        self.latest({"version": " 9.1.0 ", "notes": "Check this PC\n and   Copy support info. " + "x" * 400})
        latest = support.read_latest(self.folder)
        self.assertEqual(latest["version"], "9.1.0")
        self.assertTrue(latest["notes"].startswith("Check this PC and Copy support info."))
        self.assertLessEqual(len(latest["notes"]), support.NOTES_CHARS)
        (self.folder / "latest.json").write_bytes(b'\xef\xbb\xbf{"version": "9.2"}')  # Saved by Notepad, with a BOM.
        self.assertEqual(support.read_latest(self.folder), dict(version="9.2", notes=""))

    def test_a_missing_or_damaged_latest_json_is_explained(self):
        for value, reason in ((None, "can't be found"), ("{not json", "isn't valid JSON"),
                              ({"notes": "no version"}, 'has no "version"'), ({"version": 3}, 'has no "version"'),
                              ("[" + " " * support.LATEST_LIMIT + "]", "too large")):
            with self.subTest(reason=reason):
                (self.folder / "latest.json").unlink(missing_ok=True)
                if value is not None: self.latest(value)
                with self.assertRaises(ValueError) as caught: support.read_latest(self.folder)
                self.assertIn(reason, str(caught.exception))

    def test_app_version_check(self):
        unset = support.check_app_version("")
        self.assertTrue(unset.ok); self.assertIn("No update folder is set", unset.detail); self.assertEqual(unset.action, "set_update_folder")
        self.latest({"version": "99.0.0", "notes": "Faster rule lists."})
        update = support.check_app_version(str(self.folder))
        self.assertFalse(update.ok); self.assertEqual(update.action, "open_update_folder")
        self.assertIn("99.0.0 is available (you have " + __version__ + ")", update.detail)
        self.assertIn(str(self.folder), update.detail); self.assertIn("What's new: Faster rule lists.", update.detail)
        self.latest({"version": __version__})
        self.assertTrue(support.check_app_version(str(self.folder)).ok)
        gone = support.check_app_version(str(self.folder / "missing"))
        self.assertFalse(gone.ok); self.assertIn("couldn't be read", gone.detail)

    def test_quick_checks_report_first_and_results_keep_the_display_order(self):
        reported = []
        with tools(), patch.object(support.subprocess, "run", fake_run(answers())):
            checks = support.run_checks(self.folder, "", report=lambda check: reported.append(check.key))
        self.assertEqual([check.key for check in checks], [key for key, _ in support.CHECKS])
        self.assertEqual(reported[:2], ["data_folder", "app_version"]); self.assertEqual(reported[-1], "azure_cli")
        self.assertTrue(all(check.ok for check in checks))

    def test_the_guide_explains_the_support_tools(self):
        from sentinel_app import paths
        guide = (paths.DOCS / "DESKTOP-START-HERE.md").read_text(encoding="utf-8")
        for words in ("## Check this PC and support info", "**Copy support info**", "### Update notice",
                      '{"version": "2.2.0", "notes":', "`update_folder`", "last 100 lines"):
            self.assertIn(words, guide)

    def test_support_tools_make_no_web_requests_of_their_own(self):
        source = Path(support.__file__).read_text(encoding="utf-8")
        for module in ("urllib", "http.client", "socket", "requests"):
            self.assertNotIn("import " + module, source); self.assertNotIn("from " + module, source)


LOG = """2026-11-08T09:12:03  Sentinel Workspace 2.1.0
Traceback (most recent call last):
  File "C:\\app\\sentinel_app\\desktop_app.py", line 1281, in dispatch
    data()
  File "C:\\app\\sentinel_app\\rules_page.py", line 210, in loaded
    raise ValueError(message)
ValueError: Rule "Brute force" for Acme Corporation in 22222222-3333-4444-8555-666666666666 failed for analyst@acme.onmicrosoft.com with token=abc123 eyJhbGciOiJSUzI1NiJ9.eyJ0aWQiOiIxIn0.c2ln
    query: SigninLogs | where ResultType != 0
    severity: High
2026-11-08T10:00:00  Sentinel Workspace 2.1.0 (start-up)
Traceback (most recent call last):
  File "C:\\app\\sentinel_app\\desktop_app.py", line 1975, in main
    app = App(root)
          ^^^^^^^^^
KeyError: 'bluestone.onmicrosoft.com'
"""


class SupportInfoTests(unittest.TestCase):
    CLIENTS = [dict(name="Acme Corporation", tenant=TENANT), dict(name="Bluestone Health", tenant="bluestone.onmicrosoft.com")]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "logs" / "errors.log"
        self.log.parent.mkdir(); self.log.write_text(LOG, encoding="utf-8")

    def test_error_log_keeps_the_traceback_but_not_client_data(self):
        lines = support.log_tail(self.log, self.CLIENTS)
        text = "\n".join(lines)
        for kept in ("2026-11-08T09:12:03  Sentinel Workspace 2.1.0", 'File "C:\\app\\sentinel_app\\rules_page.py", line 210',
                     "    raise ValueError(message)", 'ValueError: Rule "…" for', "^^^^^^^^^", "KeyError: '…'",
                     "[2 lines left out]"):
            self.assertIn(kept, text)
        for secret in ("Acme", "22222222-3333", "analyst@", "abc123", "eyJhbGci", "bluestone", "SigninLogs", "severity",
                       "Brute force"):
            self.assertNotIn(secret, text)
        self.assertIn("<client>", text); self.assertIn("<id>", text); self.assertIn("token=<token>", text)

    def test_only_the_last_100_lines_are_read(self):
        self.log.write_text("".join(f"2026-11-08T09:12:{i % 60:02d}  Sentinel Workspace 2.1.0 entry {i}\n" for i in range(250)),
                            encoding="utf-8")
        lines = support.log_tail(self.log)
        self.assertEqual(len(lines), support.LOG_LINES); self.assertTrue(lines[-1].endswith("entry 249"))
        self.assertTrue(lines[0].endswith("entry 150"))
        self.assertEqual(support.log_tail(self.log.with_name("missing.log")), [])

    def test_a_long_message_is_cut_short(self):
        self.log.write_text('Traceback (most recent call last):\n  File "x.py", line 1, in f\n    f()\nOSError: '
                            + "word " * 100 + "\n", encoding="utf-8")
        last = support.log_tail(self.log)[-1]
        self.assertTrue(last.startswith("OSError: word")); self.assertLessEqual(len(last), len("OSError: ") + support.MESSAGE_CHARS)

    def test_support_text_lists_versions_checks_and_the_redacted_log(self):
        facts = support.system_facts("8.6.15", Path(self.temp.name))
        self.assertEqual([key for key, _ in facts], ["App version", "Windows", "Python", "Tk", "Data folder"])
        checks = [support.Check("azure_cli", "Azure CLI", True, "2.79.0 is installed.", "2.79.0"),
                  support.Check("github_signin", "GitHub sign-in", True, "Signed in as octo-analyst.", "signed in"),
                  support.Check("app_version", "App version", False, "x", "2.1.0; 2.2.0 is available in the update folder")]
        text = support.support_text(facts, checks, self.log, self.CLIENTS)
        self.assertTrue(text.startswith("Sentinel Workspace support info\nCollected "))
        for expected in ("App version: " + __version__, "Tk: 8.6.15", "Data folder: " + self.temp.name,
                         "- Azure CLI: OK (2.79.0)", "- GitHub sign-in: OK (signed in)", "- App version: needs action (",
                         "errors.log, last 100 lines"):
            self.assertIn(expected, text)
        for secret in ("octo-analyst", "Acme", TENANT, "bluestone", "SigninLogs"):
            self.assertNotIn(secret, text)
        self.log.unlink()
        self.assertIn("(empty: no unexpected errors have been recorded)", support.support_text(facts, checks, self.log))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def synchronous(thread):
    thread.run()


class AppSupportTests(unittest.TestCase):
    """The tools in the running window: sidebar links, Check this PC, Copy support info and the update notice."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"; self.data.mkdir()
        self.share = Path(self.temp.name) / "share"; self.share.mkdir()
        self.seeded_log = None
        self.addCleanup(self.no_hidden_errors)
        for module in (backend, ui):
            p = patch.object(module, "DATA", self.data); p.start(); self.addCleanup(p.stop)
        # A saved rule library makes the app check GitHub soon after start: never for real here.
        for p in (patch.object(ui, "create_handoff", return_value=None), tools(),
                  patch.object(support.subprocess, "run", fake_run(answers())),
                  patch.object(rule_builder_page.RuleBuilderPage, "github_probe", staticmethod(lambda: "octo-analyst"))):
            p.start(); self.addCleanup(p.stop)
        (self.data / "clients.json").write_text(json.dumps([{"name": "Acme Corporation", "tenant": TENANT}]))

    def start(self, settings=None):
        if settings is not None: (self.data / "settings.json").write_text(json.dumps(settings))
        root = tk.Tk(); root.withdraw()
        self.addCleanup(self.close, root)
        with patch.object(ui.threading.Thread, "start", synchronous):
            app = ui.App(root); root.update()
        return app

    def close(self, root):
        try:
            for timer in root.tk.call("after", "info"): root.after_cancel(timer)
            root.destroy()
        except tk.TclError:
            pass

    def no_hidden_errors(self):
        log = self.data / "logs" / "errors.log"
        if log.exists() and log.read_text(encoding="utf-8") != (self.seeded_log or ""):
            self.fail("Unexpected Tk callback error:\n" + log.read_text(encoding="utf-8"))

    def activity(self, app):
        app.poll()
        return app.logbox.get("1.0", "end")

    def latest(self, version, notes="Faster rule lists."):
        (self.share / "latest.json").write_text(json.dumps(dict(version=version, notes=notes)), encoding="utf-8")

    def run_update_check(self, app):
        """The start-up read is scheduled after a moment: run it now, on this thread."""
        with patch.object(ui.threading.Thread, "start", synchronous):
            app.check_for_update()
        app.poll(); app.root.update()

    # ------------------------------------------------------------ sidebar
    def test_sidebar_footer_offers_the_support_tools_under_help(self):
        app = self.start()
        links = [w.cget("text") for w in descendants(app.root) if isinstance(w, tk.Label) and w.cget("takefocus") == 1
                 and w.cget("text") in ("Help & prerequisites", "Check this PC", "Copy support info",
                                        "Open app data folder", "About Sentinel Workspace")]
        self.assertEqual(links, ["Help & prerequisites", "Check this PC", "Copy support info",
                                 "Open app data folder", "About Sentinel Workspace"])

    # ------------------------------------------------------------ update notice
    def test_newer_version_in_the_update_folder_shows_a_quiet_banner_on_connect(self):
        self.latest("99.0.0")
        app = self.start(dict(github_owner="example", github_repo="detections", update_folder=str(self.share)))
        self.assertFalse(app.update_banner.winfo_manager())  # Read after start-up, on a worker thread.
        self.run_update_check(app)
        self.assertEqual(app.update_banner.winfo_manager(), "grid")
        text = app.update_text.get()
        self.assertIn("99.0.0 is available (you have " + __version__ + ")", text)
        self.assertIn(str(self.share), text); self.assertIn("What's new: Faster rule lists.", text)
        self.assertEqual(app.update_banner.action_button.cget("text"), "Open folder")
        with patch.object(ui.os, "startfile", create=True) as startfile:
            app.update_banner.action_button.invoke()
        startfile.assert_called_once_with(str(self.share))
        self.assertIn("99.0.0 is available in", self.activity(app))
        self.assertEqual(app.status.get(), "Ready.")  # Quiet: no alert and no status message.

    def test_same_version_or_an_unreadable_folder_shows_no_banner(self):
        self.latest(__version__)
        app = self.start(dict(update_folder=str(self.share)))
        self.run_update_check(app)
        self.assertFalse(app.update_banner.winfo_manager())
        app.settings["update_folder"] = str(self.share / "offline")
        self.run_update_check(app)
        self.assertFalse(app.update_banner.winfo_manager())
        self.assertIn("Couldn't check for a newer version", self.activity(app))

    def test_no_update_folder_reads_nothing(self):
        with patch.object(support, "read_latest") as read:
            app = self.start(); app.root.update()
        read.assert_not_called(); self.assertFalse(app.update_banner.winfo_manager())

    def test_update_folder_is_saved_with_the_other_settings(self):
        app = self.start(dict(github_owner="example", github_repo="detections", managing_tenant_id=TENANT))
        self.latest("99.0.0")
        form = MagicMock()
        with patch.object(ui.kit, "FormDialog", return_value=form) as dialog, \
             patch.object(ui.threading.Thread, "start", synchronous):
            form.show.return_value = '"' + str(self.share / "latest.json") + '"'  # Explorer's Copy as path.
            validate = None
            def capture(*args, **kwargs):
                nonlocal validate; validate = kwargs["validate"]; return form
            dialog.side_effect = capture
            app.ask_update_folder(app.root)
            folder = validate(dict(folder=form.show.return_value))
            self.assertEqual(folder, str(self.share))
            with self.assertRaises(ui.kit.FieldError): validate(dict(folder="share\\builds"))
            self.assertEqual(validate(dict(folder="  ")), "")
            form.show.return_value = folder
            self.assertTrue(app.ask_update_folder(app.root))
        saved = json.loads((self.data / "settings.json").read_text())
        self.assertEqual(saved, dict(github_owner="example", github_repo="detections", managing_tenant_id=TENANT,
                                     update_folder=str(self.share)))
        app.poll(); app.root.update()
        self.assertEqual(app.update_banner.winfo_manager(), "grid")  # Checked again at once.
        self.assertTrue(app.save_update_folder(""))
        self.assertNotIn("update_folder", json.loads((self.data / "settings.json").read_text()))
        self.assertFalse(app.update_banner.winfo_manager())

    # ------------------------------------------------------------ Check this PC
    def open_check(self, app):
        with patch.object(ui.threading.Thread, "start", synchronous):
            dialog = app.open_check_pc()
        self.addCleanup(lambda: dialog.window.winfo_exists() and dialog.window.destroy())
        app.poll(); app.root.update()
        return dialog

    def test_check_this_pc_shows_each_line_with_ok_or_a_fix(self):
        app = self.start()
        with patch.object(support.shutil, "which", lambda name: AZ if name == "az" else None), \
             patch.object(support.subprocess, "run", fake_run(answers(az="2.70.0"))):
            dialog = self.open_check(app)
        pills = {key: row["pill"].cget("text") for key, row in dialog.rows.items()}
        self.assertEqual(pills, dict(azure_cli="●  Needs action", github_cli="●  Needs action",
                                     github_signin="●  Needs action", data_folder="●  OK", app_version="●  OK"))
        self.assertIn("2.70.0 is installed, but 2.76 or later is needed", dialog.rows["azure_cli"]["detail"].cget("text"))
        self.assertIn("install GitHub CLI", dialog.rows["github_cli"]["detail"].cget("text"))
        self.assertEqual(dialog.summary.get(), "3 checks need action. Each one says what to do.")
        self.assertEqual(dialog.rows["app_version"]["button"].cget("text"), "Set update folder…")
        self.assertFalse(dialog.rows["azure_cli"]["button"].winfo_manager())
        self.assertEqual(str(dialog.again_button.cget("state")), "normal")

    def test_check_again_and_sign_in_to_github(self):
        app = self.start()
        with patch.object(support.subprocess, "run", fake_run(answers(status=(1, "", NOT_SIGNED_IN)))):
            dialog = self.open_check(app)
        button = dialog.rows["github_signin"]["button"]
        self.assertEqual(button.cget("text"), "Sign in to GitHub")
        calls = []
        with patch.object(support.subprocess, "run", fake_run(answers(), calls)), \
             patch.object(ui.threading.Thread, "start", synchronous):
            dialog.again_button.invoke()
        app.poll(); app.root.update()
        self.assertEqual(len(calls), 3)  # az version, gh --version and gh auth status: nothing else runs.
        self.assertEqual(dialog.summary.get(), "Everything Sentinel Workspace needs is in place.")
        self.assertIn("Signed in as octo-analyst.", dialog.rows["github_signin"]["detail"].cget("text"))
        self.assertFalse(dialog.rows["github_signin"]["button"].winfo_manager())
        with patch.object(app, "github_login") as login:
            dialog.act(support.Check("github_signin", "GitHub sign-in", False, "", "", "github_login"))
        login.assert_called_once_with(); self.assertFalse(dialog.window.winfo_exists())

    def test_setting_the_update_folder_mid_check_checks_again_afterwards(self):
        app = self.start()
        started = []
        with patch.object(ui.threading.Thread, "start", lambda thread: started.append(thread)):
            dialog = app.open_check_pc()
            self.addCleanup(lambda: dialog.window.winfo_exists() and dialog.window.destroy())
            self.latest("99.0.0"); app.settings["update_folder"] = str(self.share)
            dialog.refresh()  # As after Set update folder…, while Azure CLI is still answering.
            self.assertEqual(len(started), 1)
            started[0].run(); app.poll(); app.root.update()
        self.assertEqual(len(started), 2)  # The second run started once the first had finished.
        started[1].run(); app.poll(); app.root.update()
        self.assertEqual(dialog.rows["app_version"]["pill"].cget("text"), "●  Needs action")
        self.assertEqual(dialog.rows["app_version"]["button"].cget("text"), "Open folder")

    def test_closing_before_the_checks_finish_is_harmless(self):
        app = self.start()
        started = []
        with patch.object(ui.threading.Thread, "start", lambda thread: started.append(thread)):
            dialog = app.open_check_pc()
        dialog.close()
        started[0].run(); app.poll(); app.root.update()  # The results arrive after the window has gone.

    # ------------------------------------------------------------ Copy support info
    def test_copy_support_info_copies_versions_and_a_redacted_log(self):
        self.seeded_log = LOG
        (self.data / "logs").mkdir(); (self.data / "logs" / "errors.log").write_text(LOG, encoding="utf-8")
        app = self.start()
        with patch.object(ui.threading.Thread, "start", synchronous), patch.object(ui.kit, "MessageDialog") as shown:
            app.copy_support_info(); app.poll()
        text = app.root.clipboard_get()
        title, message = shown.call_args.args[1:3]
        self.assertEqual(title, "Support info copied"); self.assertEqual(shown.call_args.kwargs["details"], text)
        self.assertIn("left out", message)
        for expected in ("App version: " + __version__, "Data folder: " + str(self.data), "- Azure CLI: OK (2.79.0)",
                         "- GitHub CLI: OK (2.63.0)", "Tk: " + str(app.root.tk.call("info", "patchlevel")),
                         "ValueError: Rule"):
            self.assertIn(expected, text)
        for secret in ("Acme", TENANT, "octo-analyst", "22222222-3333", "SigninLogs", "gho_"):
            self.assertNotIn(secret, text)
        self.assertEqual(app.status.get(), "Support info copied.")
        self.assertFalse(app.support_collecting)

    def test_copy_from_check_this_pc_uses_its_results(self):
        app = self.start()
        dialog = self.open_check(app)
        with patch.object(support.subprocess, "run", side_effect=AssertionError("no second run")), \
             patch.object(ui.kit, "MessageDialog") as shown:
            dialog.copy_button.invoke()
        self.assertEqual(shown.call_args.args[0], dialog.window)  # Over the dialog, which gets the keyboard back.
        self.assertIn("- GitHub sign-in: OK (signed in)", app.root.clipboard_get())

    def test_a_failed_collection_is_explained(self):
        app = self.start()
        with patch.object(ui.threading.Thread, "start", synchronous), \
             patch.object(support, "run_checks", side_effect=RuntimeError("disk full")), \
             patch.object(ui.kit, "alert") as alert:
            app.copy_support_info(); app.poll()
        self.assertEqual(alert.call_args.args[1], "Couldn't collect support info")
        self.assertEqual(alert.call_args.kwargs["details"], "disk full"); self.assertFalse(app.support_collecting)


if __name__ == "__main__":
    unittest.main()
