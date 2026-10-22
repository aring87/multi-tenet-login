import unittest
from unittest.mock import MagicMock
from sentinel_app.signin_window import WindowsSignIn, is_signin

class SignInWindowTests(unittest.TestCase):
    def handoff(self, before, current):
        helper=WindowsSignIn.__new__(WindowsSignIn)
        helper.before=before;helper.raised=set()
        helper.user=MagicMock();helper.user.IsIconic.return_value=False
        helper.snapshot=MagicMock(return_value=current)
        # Like Windows: a window is in front only after a SetForegroundWindow that was allowed.
        helper.state={"front":None,"allow":True}
        def set_front(hwnd):
            if helper.state["allow"]:helper.state["front"]=hwnd
            return helper.state["allow"]
        helper.user.SetForegroundWindow.side_effect=set_front
        helper.user.GetForegroundWindow.side_effect=lambda:helper.state["front"]
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
        helper.state["allow"]=False
        self.assertFalse(helper.bring_forward())
        self.assertFalse(helper.raised)

    def test_manual_falls_back_to_a_new_browser_window(self):
        before={100:(42,"msedge.exe","Inbox - Outlook")}
        helper=self.handoff(before,{**before,200:(43,"msedge.exe","Microsoft Authenticator - Microsoft Edge")})
        self.assertFalse(helper.bring_forward())
        self.assertTrue(helper.bring_forward(manual=True))
        helper.user.SetForegroundWindow.assert_called_once_with(200)

    def test_refused_focus_retries_and_still_raises_the_window(self):
        helper=self.handoff({}, {100:(42,"chrome.exe","Sign in to your account")})
        # Windows reports success but leaves the old window in front; the Alt retry then works.
        attempts=iter([None,100])
        def set_front(hwnd):
            helper.state["front"]=next(attempts);return True
        helper.user.SetForegroundWindow.side_effect=set_front
        self.assertTrue(helper.bring_forward())
        self.assertEqual(helper.user.SetForegroundWindow.call_count,2)
        helper.user.keybd_event.assert_called()

    def test_account_picker_host_needs_a_signin_title(self):
        self.assertTrue(is_signin("ApplicationFrameHost.exe","Pick an account"))
        self.assertFalse(is_signin("ApplicationFrameHost.exe","Settings"))
