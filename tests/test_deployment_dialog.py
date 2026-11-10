from unittest.mock import patch
import unittest

import test_preview_dialog as preview_tests
from sentinel_app.deployment_dialog import DeploymentDialog


class DeploymentDialogTests(unittest.TestCase):
    setUp = preview_tests.PreviewDialogTests.setUp
    cleanup = preview_tests.PreviewDialogTests.cleanup
    work = preview_tests.PreviewDialogTests.work
    prepare = preview_tests.PreviewDialogTests.prepare

    def ready(self):
        self.prepare()
        with patch.object(self.app, "work", side_effect=self.work):
            self.dialog.start(); self.dialog.refresh_status()

    def review(self):
        self.ready()
        with patch.object(self.app, "work", side_effect=self.work):
            return DeploymentDialog(self.dialog)

    def test_deployment_only_available_after_refreshed_success(self):
        self.prepare()
        self.assertEqual(str(self.dialog.deploy_button.cget("state")), "disabled")
        with patch("sentinel_app.preview_dialog.DeploymentDialog") as dialog:
            self.dialog.open_deployment()
        dialog.assert_not_called()
        with patch.object(self.app, "work", side_effect=self.work):self.dialog.start()
        self.assertEqual(str(self.dialog.deploy_button.cget("state")), "disabled")
        with patch.object(self.app, "work", side_effect=self.work):self.dialog.refresh_status()
        self.assertEqual(str(self.dialog.deploy_button.cget("state")), "normal")

    def test_review_shows_destinations_states_and_revision_without_dispatch(self):
        dialog = self.review()
        self.assertEqual(len(self.remote.dispatched), 1)
        text = dialog.review.get("1.0", "end")
        self.assertIn("Mode: DEPLOY", text)
        self.assertIn("Configured rule state:", text)
        self.assertIn("example-primary", text); self.assertIn("client-1-primary", text)
        self.assertNotIn("client-0-primary", text)
        self.assertIn(self.remote.head, text)
        self.assertIn("may start immediately", text)
        self.assertEqual(str(dialog.start_button.cget("state")), "normal")
        dialog.close()
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_blue_button_moves_from_start_to_refresh_but_enter_stays_unbound(self):
        dialog = self.review()
        style = lambda button: str(button.cget("style"))
        self.assertEqual((style(dialog.start_button), style(dialog.status_button)), ("Primary.TButton", "Secondary.TButton"))
        self.assertEqual((style(dialog.open_button), dialog.open_button.pack_info()["side"]), ("Ghost.TButton", "left"))
        with patch.object(self.app, "work", side_effect=self.work): dialog.start()
        self.assertEqual((style(dialog.start_button), style(dialog.status_button)), ("Secondary.TButton", "Primary.TButton"))
        self.assertEqual(dialog.window.bind("<Return>"), "")  # A deployment never starts from the keyboard.
        self.assertEqual(dialog.open_button.cget("text"), "Open in GitHub")

    def test_the_review_has_the_keyboard(self):
        self.root.deiconify(); self.dialog.window.deiconify(); self.root.update()
        dialog = self.review(); self.root.update()
        self.assertIs(dialog.window.focus_lastfor(), dialog.review)  # So it scrolls from the keyboard.
        self.assertEqual(str(dialog.review.cget("takefocus")), "1")

    def test_submit_is_explicit_and_reopening_cannot_duplicate(self):
        dialog = self.review()
        with patch.object(self.app, "work", side_effect=self.work):dialog.start()
        self.assertEqual(len(self.remote.dispatched), 2)
        self.assertEqual(self.remote.dispatched[-1]["inputs"]["mode"], "deploy")
        self.assertEqual(str(dialog.start_button.cget("state")), "disabled")
        dialog.close()
        with patch.object(self.app, "work", side_effect=self.work):
            reopened = DeploymentDialog(self.dialog); reopened.start(); reopened.refresh()
        self.assertEqual(len(self.remote.dispatched), 2)
        reopened.runs.selection_set("0")
        with patch("sentinel_app.deployment_dialog.webbrowser.open") as browser:reopened.open_run()
        self.assertTrue(browser.call_args.args[0].endswith("/actions/runs/102"))

    def test_uncertain_submission_stays_locked_and_keeps_workflow_link(self):
        dialog = self.review(); self.remote.lose_response = True
        with patch.object(self.app, "work", side_effect=self.work):dialog.start()
        self.assertIn("unconfirmed", dialog.record["results"][0]["state"])
        self.assertEqual(str(dialog.start_button.cget("state")), "disabled")
        with patch("sentinel_app.deployment_dialog.webbrowser.open") as browser:dialog.open_run()
        self.assertIn("/actions/workflows/", browser.call_args.args[0])
        with patch.object(self.app, "work", side_effect=self.work):dialog.start()
        self.assertEqual(len(self.remote.dispatched), 2)

    def test_preparation_failure_has_no_deployment_button(self):
        self.ready(); self.remote.head = "f" * 40
        with patch.object(self.app, "work", side_effect=self.work):dialog = DeploymentDialog(self.dialog)
        self.assertIsNone(dialog.record)
        self.assertIn("changed", dialog.notice.get())
        self.assertEqual(str(dialog.start_button.cget("state")), "disabled")
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_preview_and_deployment_controls_fit_their_minimum_windows(self):
        dialog = self.review()
        self.root.deiconify(); self.dialog.window.deiconify()
        self.dialog.window.geometry("850x680"); dialog.window.geometry("800x620")
        self.root.update()
        for window, buttons in ((self.dialog.window, (self.dialog.deploy_button, self.dialog.close_button)),
                                (dialog.window, dialog.buttons)):
            bottom = window.winfo_rooty() + window.winfo_height()
            right = window.winfo_rootx() + window.winfo_width()
            for button in buttons:
                self.assertTrue(button.winfo_ismapped())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), right)
