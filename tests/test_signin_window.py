import unittest
from unittest.mock import MagicMock
from signin_window import WindowsSignIn, is_signin

class SignInWindowTests(unittest.TestCase):
    def handoff(self, before, current):
        helper=WindowsSignIn.__new__(WindowsSignIn)
        helper.before=before;helper.raised=set()
        helper.user=MagicMock();helper.user.IsIconic.return_value=False
        helper.user.SetForegroundWindow.return_value=True
        helper.snapshot=MagicMock(return_value=current)
        return helper

    def test_only_supported_signin_windows_qualify(self):
        self.assertTrue(is_signin("msedge.exe","Sign in to your account - Microsoft Edge"))
        self.assertTrue(is_signin("Microsoft.AAD.BrokerPlugin.exe","Microsoft account"))
        self.assertFalse(is_signin("notepad.exe","Sign in to your account"))
        self.assertFalse(is_signin("chrome.exe","Unrelated browser tab"))

    def test_existing_window_not_automatically_focused(self):
        window={100:(42,"chrome.exe","Sign in to your account")}
        helper=self.handoff(window,window)
        self.assertFalse(helper.bring_forward())
        helper.user.SetForegroundWindow.assert_not_called()
        self.assertTrue(helper.bring_forward(manual=True))

    def test_new_signin_window_raised_only_once_automatically(self):
        helper=self.handoff({}, {100:(42,"chrome.exe","Sign in to your account")})
        self.assertTrue(helper.bring_forward())
        self.assertFalse(helper.bring_forward())
        helper.user.SetForegroundWindow.assert_called_once_with(100)

    def test_reused_browser_with_changed_title_is_detected(self):
        helper=self.handoff({100:(42,"chrome.exe","New tab")},
                            {100:(42,"chrome.exe","Sign in to your account")})
        helper.user.IsIconic.return_value=True
        self.assertTrue(helper.bring_forward())
        helper.user.ShowWindow.assert_called_once_with(100,9)

    def test_windows_focus_denial_is_not_reported_as_success(self):
        helper=self.handoff({}, {100:(42,"chrome.exe","Sign in to your account")})
        helper.user.SetForegroundWindow.return_value=False
        self.assertFalse(helper.bring_forward())
        self.assertFalse(helper.raised)
