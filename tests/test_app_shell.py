"""Offline checks for the window shell: placement, one running copy, error logging, help, sign-in
errors, client switching and the per-client workspace memory. No Azure or GitHub calls."""
import json
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sentinel_app import desktop_app as ui
from sentinel_app import desktop_backend as backend
from sentinel_app.desktop_backend import Stop
from test_desktop import TENANT, SUB, WORKSPACE

SUB2 = "99999999-2222-4222-8222-222222222222"


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)
SECOND = dict(WORKSPACE, workspace_name="law-second", workspace_id="66666666-6666-4666-8666-666666666666",
              resource_group="rg-second")


class PlacementTests(unittest.TestCase):
    """plan_geometry with a mocked work area, frame and monitor layout."""

    def plan(self, remembered=None, area=(0, 0, 2560, 1400), frame=(16, 39), scale=1.0, on=lambda x, y: True, zoomed=False):
        with patch.object(ui, "px", lambda value: int(round(value * scale))):
            return ui.plan_geometry(remembered, area, frame, on, zoomed)

    def test_default_size_is_centred_when_it_fits(self):
        self.assertEqual(self.plan(), (1360, 900, 592, 230, 1120, 740, False))

    def test_150_percent_on_a_1080p_laptop_fits_above_the_taskbar_and_opens_maximised(self):
        area, frame = (0, 0, 1920, 1032), (16, 45)
        width, height, x, y, min_w, min_h, zoom = self.plan(area=area, frame=frame, scale=1.5)
        self.assertTrue(zoom)
        self.assertLessEqual(y + height + frame[1], area[3]); self.assertLessEqual(x + width + frame[0], area[2])
        self.assertEqual((min_w, min_h), (1680, 1032 - 45))  # The 740 px floor (1110) no longer wins.
        self.assertLessEqual(min_h + frame[1], area[3])

    def test_saved_size_is_clamped_to_the_work_area_but_not_maximised(self):
        width, height, x, y, min_w, min_h, zoom = self.plan("1800x1300+10+10", area=(0, 0, 1536, 912))
        self.assertEqual((width, height, zoom), (1520, 873, False))
        self.assertEqual((x, y), (0, 0))  # Pulled back inside the work area.

    def test_saved_position_on_a_monitor_left_of_the_primary_is_kept(self):
        on = lambda x, y: -1920 <= x < 1920 and 0 <= y < 1080
        result = self.plan("1400x900+-1800+60", area=(-1920, 0, 1920, 1040), on=on)
        self.assertEqual(result[:4], (1400, 900, -1800, 60))

    def test_position_off_every_monitor_is_centred_on_the_work_area(self):
        result = self.plan("1400x900+5000+60", area=(0, 0, 1920, 1040), on=lambda x, y: False)
        self.assertEqual(result[2:4], ((1920 - 16 - 1400) // 2, (1040 - 39 - 900) // 2))

    def test_remembered_maximised_window_opens_maximised(self):
        self.assertTrue(self.plan("1360x900+20+20", zoomed=True)[6])
        self.assertFalse(self.plan("1360x900+20+20")[6])


class SingleInstanceTests(unittest.TestCase):
    def test_second_copy_is_detected_by_the_named_mutex(self):
        kernel32 = MagicMock(); kernel32.CreateMutexW.return_value = 77
        with patch.object(ui, "_INSTANCE", []) as held:
            self.assertFalse(ui.claim_instance(kernel32, lambda: ui.ERROR_ALREADY_EXISTS))
            self.assertEqual(kernel32.CreateMutexW.call_args.args[2], "Local\\" + ui.APP_USER_MODEL_ID)
            self.assertTrue(ui.claim_instance(kernel32, lambda: 0))
            self.assertEqual(held, [77, 77])  # Kept referenced for the life of the process.

    def test_second_launch_brings_the_first_window_forward_and_opens_nothing(self):
        if ui.os.name != "nt": self.skipTest("Windows only")
        with patch.object(ui, "claim_instance", return_value=False), \
             patch.object(ui, "bring_existing_forward") as forward, patch.object(ui.tk, "Tk") as tk_root:
            ui.main()
        forward.assert_called_once_with(); tk_root.assert_not_called()

    def test_existing_window_is_restored_and_focused(self):
        class User32:
            def __init__(self, windows): self.windows, self.calls = windows, []
            def EnumWindows(self, callback, lparam):
                for hwnd in self.windows:
                    if not callback(hwnd, lparam): break
                return 1
            def GetWindowTextW(self, hwnd, buffer, size): buffer.value = self.windows[hwnd][0]; return 1
            def GetClassNameW(self, hwnd, buffer, size): buffer.value = self.windows[hwnd][1]; return 1
            def IsWindowVisible(self, hwnd): return 1
            def IsIconic(self, hwnd): return self.windows[hwnd][2]
            def ShowWindow(self, hwnd, command): self.calls.append(("show", hwnd, command))
            def SetForegroundWindow(self, hwnd): self.calls.append(("front", hwnd))
        user32 = User32({11: ("Sentinel Workspace - Microsoft Edge", "Chrome_WidgetWin_1", 0),
                         12: ("Acme Corporation — Sentinel Workspace", "TkTopLevel", 1)})
        self.assertTrue(ui.bring_existing_forward(user32))
        self.assertEqual(user32.calls, [("show", 12, 9), ("front", 12)])  # Restored from the taskbar, then focused.
        self.assertFalse(ui.bring_existing_forward(User32({11: ("Notes", "TkTopLevel", 0)})))


class ErrorLogTests(unittest.TestCase):
    def test_log_appends_and_keeps_its_newest_half_under_the_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "logs" / "errors.log"
            for index in range(60):
                ui.append_error_log(path, f"error {index:02d}\n" + "x" * 20, limit=400)
                self.assertLessEqual(path.stat().st_size, 400)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("error ") or text.startswith("x"))
            self.assertIn("error 59", text); self.assertNotIn("error 00", text)
            self.assertTrue(text.splitlines()[0] in {"x" * 20} or text.splitlines()[0].startswith("error "))


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        for module in (backend, ui):
            p = patch.object(module, "DATA", self.data); p.start(); self.addCleanup(p.stop)
        p = patch.object(ui, "create_handoff", return_value=None); p.start(); self.addCleanup(p.stop)
        (self.data / "clients.json").write_text(json.dumps([{"name": "Acme Corporation", "tenant": TENANT},
                                                            {"name": "Bluebird", "tenant": ""},
                                                            {"name": "Bluestone Health", "tenant": ""}]))
        self.roots = []
        self.app = self.start()

    def start(self):
        """Start the app, as after a restart: the previous window closes first (one Tk at a time keeps
        each window's variables in its own interpreter)."""
        for previous in self.roots: self.close(previous)
        root = tk.Tk(); root.withdraw(); self.roots.append(root)
        self.addCleanup(self.close, root)
        app = ui.App(root); root.update()
        return app

    def close(self, root):
        try:
            for timer in root.tk.call("after", "info"): root.after_cancel(timer)
            root.destroy()
        except tk.TclError:
            pass

    def activity(self, app=None):
        app = app or self.app; app.poll()
        return app.logbox.get("1.0", "end")

    # ------------------------------------------------------------ errors
    def test_unexpected_callback_errors_are_logged_to_a_file_and_the_status_bar(self):
        app = self.app
        self.assertEqual(app.root.report_callback_exception, app.callback_failed)
        button = tk.Button(app.root, command=lambda: 1 / 0)
        with patch.object(sys, "stderr"): button.invoke()
        log = self.data / "logs" / "errors.log"
        self.assertIn("ZeroDivisionError", log.read_text(encoding="utf-8"))
        self.assertIn("ZeroDivisionError", self.activity())
        self.assertEqual(app.status.get(), "Something went wrong. Details are in Session activity.")
        self.assertIn("Stopped", app.activity_badge.cget("text"))

    def test_failed_task_explains_itself_in_a_themed_alert(self):
        app = self.app; app.workspace = dict(WORKSPACE)
        raw = ("AuthorizationFailed: The client does not have authorization to perform action "
               "'Microsoft.SecurityInsights/alertRules/read'")
        def start(thread): thread.run()
        with patch.object(ui.threading.Thread, "start", start):
            app.work("Reading analytics rules…", MagicMock(side_effect=Stop(raw)), lambda result: None)
        with patch.object(ui.kit, "alert") as alert: app.poll()
        title, message = alert.call_args.args[1:3]
        self.assertEqual(title, "Reading analytics rules")
        self.assertIn("Microsoft Sentinel Reader", message); self.assertIn("law-example-sentinel", message)
        self.assertEqual(alert.call_args.kwargs["details"], raw)
        self.assertEqual(app.status.get(), "Couldn't finish: Your account can't read analytics rules in law-example-sentinel.")
        self.assertIn(raw, self.activity())  # The original text stays in Session activity.

    def test_failed_signin_is_explained_on_the_sign_in_card(self):
        app = self.app
        session = MagicMock(); session.env = {}
        with patch.object(ui, "Session", return_value=session), patch.object(app, "work") as work:
            app.login()
        done = work.call_args.args[2]
        raw = "AADSTS50079: Due to a configuration change made by your administrator, you must enroll in MFA."
        app.events.put(("done", (done, None, Stop(raw))))
        with patch.object(ui.kit, "alert") as alert: app.poll()
        alert.assert_not_called()  # Not a second message: the banner is where the analyst is looking.
        self.assertTrue(app.signin_error.winfo_manager())
        self.assertIn("multi-factor", app.signin_error_text.get())
        self.assertEqual(app.signin_error.action_button.cget("text"), "Copy details")
        app.signin_error.action_button.invoke(); self.assertEqual(app.root.clipboard_get(), raw)
        with patch.object(ui, "Session", return_value=session), patch.object(app, "work"):
            app.login()
        self.assertFalse(app.signin_error.winfo_manager())  # Hidden while the next sign-in runs.
        app.finish_signin()

    # ------------------------------------------------------------ help and about
    def test_help_opens_inside_the_app_with_keyboard_shortcuts(self):
        app = self.app
        self.assertTrue(app.root.bind("<F1>"))
        window = app.open_help(); self.addCleanup(lambda: window.winfo_exists() and window.destroy())
        text = window.document.get("1.0", "end")
        self.assertIn("Keyboard shortcuts", text); self.assertIn("F1 opens this guide", text)
        self.assertNotIn("**", text); self.assertNotIn("```", text)
        start = window.document.search("Keyboard shortcuts", "1.0")
        self.assertIn("h2", window.document.tag_names(start))
        window.destroy()
        with patch.object(ui.kit, "alert") as alert:
            self.assertIsNone(app.show_document("Missing", self.data / "nope.md"))
        alert.assert_called_once()

    def test_markdown_blocks_join_wrapped_lines(self):
        blocks = ui.markdown_blocks("# Title\n\nOne\ntwo.\n\n1. First\n   still first\n- Bullet\n```\ncode\n  kept\n```\n## Next")
        self.assertEqual(blocks, [("h1", "Title"), ("para", "One two."), ("item", "1.  First still first"),
                                  ("item", "•  Bullet"), ("code", "code\n  kept"), ("h2", "Next")])

    def test_about_shows_the_tk_patch_level_and_unwrapped_description(self):
        app = self.app
        app.open_about()
        window = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        self.addCleanup(lambda: window.winfo_exists() and window.destroy())
        labels = [w for w in descendants(window) if isinstance(w, ui.ttk.Label)]
        texts = [str(w.cget("text")) for w in labels]
        self.assertIn(str(app.root.tk.call("info", "patchlevel")), texts)
        description = next(w for w in labels if str(w.cget("text")).startswith("Connect to client tenants"))
        self.assertNotIn("\n", str(description.cget("text"))); self.assertGreater(int(str(description.cget("wraplength"))), 0)
        window.destroy()

    # ------------------------------------------------------------ clients and workspaces
    def connect(self, app, rows=(WORKSPACE, SECOND)):
        """Sign in and discover with fakes; returns the session."""
        session = MagicMock(); session.env = {}; session.account = {"user": {"name": "analyst@example.com"}}
        session.login.return_value = [dict(id=SUB, tenantId=TENANT, name="Production", isDefault=True),
                                      dict(id=SUB2, tenantId=TENANT, name="Development")]
        session.discover.return_value = [dict(row, subscription_id=sub) for row, sub in zip(rows, (SUB, SUB))]
        with patch.object(ui, "Session", return_value=session), patch.object(app, "work", side_effect=lambda t, task, done: done(task())), \
             patch.object(app.rules_page, "load"):
            app.login()
            app.finish_signin(); session.signed_in = True; app.render_session()
        return session

    def test_switching_clients_needs_no_confirmation_and_is_logged(self):
        app = self.app
        self.assertEqual(app.vars["client_name"].get(), "Acme Corporation")
        self.connect(app)
        self.assertIsNotNone(app.workspace)
        with patch.object(ui.kit, "confirm", side_effect=AssertionError("no prompt")):
            app.vars["client_name"].set("Bluestone Health"); app.client_changed(SimpleNamespace())
        self.assertIsNone(app.workspace); self.assertFalse(app.subscriptions)
        self.assertIn("Switched from Acme Corporation to Bluestone Health.", self.activity())
        self.assertEqual(app.signin_main.cget("text"), "Sign in to Bluestone Health")
        self.assertEqual(str(app.signin_main.cget("style")), "Primary.TButton")
        self.assertEqual(app.workspace_context.get(), "Bluestone Health  ·  not signed in")
        self.assertEqual(str(app.sub_refresh.cget("state")), "disabled")  # The session belongs to Acme.

    def test_switching_back_lists_the_clients_subscriptions_again(self):
        app = self.app
        session = self.connect(app)
        app.wsbox.current(1)
        with patch.object(app.rules_page, "load"): app.workspace_changed(SimpleNamespace())
        session.refresh_subscriptions.return_value = session.login.return_value
        run = lambda title, task, done: done(task())
        app.vars["client_name"].set("Bluebird"); app.client_changed(SimpleNamespace())
        session.refresh_subscriptions.assert_not_called()  # Acme's session isn't used for Bluebird.
        with patch.object(app, "work", side_effect=run), patch.object(app.rules_page, "load"):
            app.vars["client_name"].set("Acme Corporation"); app.client_changed(SimpleNamespace())
        session.refresh_subscriptions.assert_called_once_with()
        self.assertEqual(app.workspace["workspace_id"], SECOND["workspace_id"])  # Its last workspace returns.
        self.assertEqual(app.workspace_context.get(), "Acme Corporation  ·  law-second")
        self.assertEqual(app.connection_note.get(), "Signed in as analyst@example.com. Selected law-second.")
        # Type-ahead lists them once the typing pauses; until then the note says what to do.
        app.vars["client_name"].set("Bluebird"); app.client_changed(SimpleNamespace())
        session.refresh_subscriptions.reset_mock()
        with patch.object(app, "work", side_effect=run), patch.object(app.rules_page, "load"):
            app.type_client(SimpleNamespace(char="a", state=0))
            self.assertEqual(app.vars["client_name"].get(), "Acme Corporation")
            session.refresh_subscriptions.assert_not_called()
            self.assertEqual(app.connection_note.get(), "Signed in. Select Refresh to list the subscriptions for Acme Corporation.")
            app.busy, app.operation_title = True, ui.LISTING_SUBSCRIPTIONS; app.sync_connection()
            self.assertEqual(app.connection_note.get(), "Listing the subscriptions for Acme Corporation…")
            app.busy = False
            app.root.after_cancel(app.typed_timer); app.reset_typed()
        session.refresh_subscriptions.assert_called_once_with()
        self.assertEqual(app.workspace["workspace_id"], SECOND["workspace_id"])

    def test_each_client_gets_its_last_workspace_back_after_sign_in(self):
        app = self.app
        session = self.connect(app)
        self.assertEqual(app.workspace["workspace_name"], "law-example-sentinel")
        app.wsbox.current(1)
        with patch.object(app.rules_page, "load"): app.workspace_changed(SimpleNamespace())
        state = json.loads((self.data / "ui-state.json").read_text())
        self.assertEqual(state["last_workspace"]["Acme Corporation"],
                         dict(subscription_id=SUB, workspace_id=SECOND["workspace_id"]))
        # Next start: the same client signs in and lands on that workspace, not the first one.
        again = self.start()
        session = self.connect(again)
        self.assertEqual(again.workspace["workspace_id"], SECOND["workspace_id"])
        self.assertIn("Selected law-second.", again.status.get())
        session.discover.assert_called_once_with(SUB, TENANT)
        # A remembered subscription that isn't the default is chosen as well.
        state["last_workspace"]["Acme Corporation"]["subscription_id"] = SUB2
        (self.data / "ui-state.json").write_text(json.dumps(state))
        third = self.start(); session = self.connect(third)
        session.discover.assert_called_once_with(SUB2, TENANT)

    def test_type_ahead_picks_a_client_by_its_first_letters(self):
        app = self.app
        key = lambda char: app.type_client(SimpleNamespace(char=char, state=0))
        self.assertEqual(key("b"), "break"); self.assertEqual(app.vars["client_name"].get(), "Bluebird")
        key("l"); key("u"); key("e"); key("s")
        self.assertEqual(app.vars["client_name"].get(), "Bluestone Health")
        app.reset_typed(); key("a")
        self.assertEqual(app.vars["client_name"].get(), "Acme Corporation")
        self.assertIsNone(app.type_client(SimpleNamespace(char="\x01", state=4)))  # Ctrl+A is left alone.

    def test_workspace_ids_wait_behind_a_remembered_disclosure(self):
        app = self.app
        self.assertFalse(app.ids_button.winfo_manager()); self.assertFalse(app.details_panel.winfo_manager())
        self.assertIn("Sign in", app.connection_note.get())
        self.connect(app)
        self.assertTrue(app.ids_button.winfo_manager()); self.assertFalse(app.details_panel.winfo_manager())
        self.assertEqual(app.ids_button.cget("text"), "+  Show workspace IDs")
        # The actions sit directly under the workspace picker, above the IDs.
        order = app.workspace_row.master.pack_slaves()
        self.assertEqual(order.index(app.workspace_row), order.index(app.wsbox.master) + 1)
        app.ids_button.invoke()
        self.assertTrue(app.details_panel.winfo_manager()); self.assertEqual(app.ids_button.cget("text"), "−  Hide workspace IDs")
        app.save_state()
        self.assertIs(json.loads((self.data / "ui-state.json").read_text())["show_ids"], True)
        tenant = next(label for key, label in zip(app.details.keys, app.details.values) if key == "Tenant ID")
        self.assertTrue(tenant.bind("<Button-3>"))  # Right-click copies one ID.
        app.details.copy("Tenant ID", tenant); self.assertEqual(app.root.clipboard_get(), TENANT)
        tenant.configure(text=TENANT[:8] + "…")  # Shown shortened in a narrow panel: Copy still takes the whole ID.
        app.details.copy("Tenant ID", tenant); self.assertEqual(app.root.clipboard_get(), TENANT)
        again = self.start(); self.connect(again)
        self.assertTrue(again.details_panel.winfo_manager())

    def test_maximised_window_is_remembered_with_its_normal_size(self):
        app = self.app
        app.normal_geometry = "1300x850+30+40"
        with patch.object(app.root, "state", return_value="zoomed"): app.save_state()
        state = json.loads((self.data / "ui-state.json").read_text())
        self.assertEqual((state["zoomed"], state["geometry"]), (True, "1300x850+30+40"))
        app = self.start(); self.assertTrue(app.start_zoomed)
        # Closed while minimised (Close window on the taskbar): the state before minimising is kept.
        configure = SimpleNamespace(widget=app.root)
        for before, zoomed in (("zoomed", True), ("normal", False)):
            with patch.object(app.root, "state", return_value=before), \
                 patch.object(app.root, "geometry", return_value="1250x800+60+70"):
                app.track_geometry(configure)
            normal = app.normal_geometry  # Maximised, it is still the size the window had before.
            with patch.object(app.root, "state", return_value="iconic"):
                app.track_geometry(configure); app.save_state()
            state = json.loads((self.data / "ui-state.json").read_text())
            self.assertEqual((state["zoomed"], state["geometry"]), (zoomed, normal))
            self.assertEqual(normal == "1250x800+60+70", not zoomed)

    def test_wheel_deltas_from_a_touchpad_add_up(self):
        app = self.app; app.root.geometry("1120x500"); app.root.deiconify(); app.root.update()
        canvas = app.page_canvases[0]
        self.assertEqual(int(str(canvas.cget("yscrollincrement"))), ui.px(20))
        event = lambda delta: SimpleNamespace(widget=canvas, delta=delta)
        with patch.object(canvas, "yview", return_value=(0.0, 0.5)), patch.object(canvas, "yview_scroll") as scroll:
            for _ in range(3): app.scroll_page(event(-30))  # A quarter notch each: one increment per 40.
            self.assertEqual([call.args for call in scroll.call_args_list], [(1, "units"), (1, "units")])
            scroll.reset_mock(); app.scroll_page(event(120))  # Turning back starts again from zero: a whole notch.
            self.assertEqual(scroll.call_args.args, (-3, "units"))


if __name__ == "__main__":
    unittest.main()
