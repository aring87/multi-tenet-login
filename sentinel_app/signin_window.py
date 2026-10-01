"""Best-effort foreground handoff to Microsoft sign-in on Windows.

Only supported authentication hosts/browser windows with sign-in titles qualify.
No credentials, page contents or tokens are inspected or retained.
"""
import os

BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe"}
BROKERS = {"microsoft.aad.brokerplugin.exe", "accountscontrolhost.exe"}
SIGNIN_TITLES = ("sign in to your account", "sign in - microsoft", "microsoft sign in",
                 "pick an account", "work or school account", "sign in to microsoft azure")


def is_signin(process, title):
    process, title = process.casefold(), title.casefold()
    if process in BROKERS:
        return True
    return process in BROWSERS and any(text in title for text in SIGNIN_TITLES)


class WindowsSignIn:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        for name, args, result in (
            ("EnumWindows", [self.callback,w.LPARAM], w.BOOL),
            ("IsWindowVisible", [w.HWND], w.BOOL),
            ("GetWindowTextLengthW", [w.HWND], ctypes.c_int),
            ("GetWindowTextW", [w.HWND,w.LPWSTR,ctypes.c_int], ctypes.c_int),
            ("GetWindowThreadProcessId", [w.HWND,ctypes.POINTER(w.DWORD)], w.DWORD),
            ("IsIconic", [w.HWND], w.BOOL),
            ("ShowWindow", [w.HWND,ctypes.c_int], w.BOOL),
            ("SetForegroundWindow", [w.HWND], w.BOOL)):
            method=getattr(self.user,name);method.argtypes=args;method.restype=result
        self.kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        self.kernel.OpenProcess.restype=w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype=w.BOOL
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.kernel.CloseHandle.restype=w.BOOL
        self.before=self.snapshot()
        self.raised=set()

    def snapshot(self):
        result={}
        def visit(hwnd,_):
            if not self.user.IsWindowVisible(hwnd):return True
            size=self.user.GetWindowTextLengthW(hwnd)
            if not size:return True
            title=self.ctypes.create_unicode_buffer(size+1)
            self.user.GetWindowTextW(hwnd,title,size+1)
            pid=self.w.DWORD()
            self.user.GetWindowThreadProcessId(hwnd,self.ctypes.byref(pid))
            handle=self.kernel.OpenProcess(0x1000,False,pid.value)
            if not handle:return True
            try:
                path=self.ctypes.create_unicode_buffer(32768);length=self.w.DWORD(len(path))
                if self.kernel.QueryFullProcessImageNameW(handle,0,path,self.ctypes.byref(length)):
                    result[hwnd]=(pid.value,os.path.basename(path.value).casefold(),title.value)
            finally:self.kernel.CloseHandle(handle)
            return True
        self.user.EnumWindows(self.callback(visit),0)
        return result

    def bring_forward(self, manual=False):
        for hwnd, row in self.snapshot().items():
            pid, process, title=row
            if not is_signin(process,title):continue
            identity=(hwnd,pid)
            # Automatic handoff only for a newly opened or changed sign-in window.
            if not manual and (self.before.get(hwnd)==row or identity in self.raised):continue
            if self.user.IsIconic(hwnd):self.user.ShowWindow(hwnd,9)  # SW_RESTORE
            if self.user.SetForegroundWindow(hwnd):
                self.raised.add(identity)
                return True
        return False


def create_handoff():
    if os.name != "nt":return None
    try:return WindowsSignIn()
    except (OSError,AttributeError):return None
