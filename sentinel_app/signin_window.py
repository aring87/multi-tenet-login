"""Best-effort foreground handoff to Microsoft sign-in on Windows.

Only supported authentication hosts/browser windows with sign-in titles qualify.
No credentials, page contents or tokens are inspected or retained.
"""
import os

BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe"}
BROKERS = {"microsoft.aad.brokerplugin.exe", "accountscontrolhost.exe"}
# Hosts that show the Windows account picker; they qualify only with a sign-in title.
ACCOUNT_HOSTS = {"applicationframehost.exe", "credentialuibroker.exe"}
SIGNIN_TITLES = ("sign in to your account", "sign in - microsoft", "microsoft sign in",
                 "pick an account", "work or school account", "sign in to microsoft azure",
                 "microsoft account", "verify your identity", "approve sign in request",
                 "stay signed in", "enter password", "login.microsoftonline.com")
ASFW_ANY = 0xFFFFFFFF
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
SW_RESTORE, SW_SHOW = 9, 5
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040


def is_signin(process, title):
    process, title = process.casefold(), title.casefold()
    if process in BROKERS:
        return True
    return (process in BROWSERS or process in ACCOUNT_HOSTS) and any(text in title for text in SIGNIN_TITLES)


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
            ("SetForegroundWindow", [w.HWND], w.BOOL),
            ("GetForegroundWindow", [], w.HWND),
            ("BringWindowToTop", [w.HWND], w.BOOL),
            ("AttachThreadInput", [w.DWORD,w.DWORD,w.BOOL], w.BOOL),
            ("AllowSetForegroundWindow", [w.DWORD], w.BOOL),
            ("SetWindowPos", [w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT], w.BOOL),
            ("keybd_event", [w.BYTE,w.BYTE,w.DWORD,ctypes.c_size_t], None)):
            method=getattr(self.user,name);method.argtypes=args;method.restype=result
        self.kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        self.kernel.OpenProcess.restype=w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype=w.BOOL
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.kernel.CloseHandle.restype=w.BOOL
        self.kernel.GetCurrentThreadId.argtypes=[];self.kernel.GetCurrentThreadId.restype=w.DWORD
        # Created while the analyst is clicking Sign in, so this app owns the foreground and
        # may pass that right on: the browser or account window it opens can then come forward itself.
        self.user.AllowSetForegroundWindow(ASFW_ANY)
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

    def focus(self, hwnd):
        """Bring ``hwnd`` to the front and report whether it really is in front.

        Windows' foreground lock makes SetForegroundWindow from a background app report
        success while only flashing the taskbar button, so success is checked with
        GetForegroundWindow. On failure the documented workaround is used: a harmless Alt
        press (which lifts the lock for this process) while attached to the current
        foreground window's input queue."""
        user=self.user
        if user.IsIconic(hwnd):user.ShowWindow(hwnd,SW_RESTORE)
        else:user.ShowWindow(hwnd,SW_SHOW)
        if user.SetForegroundWindow(hwnd) and user.GetForegroundWindow()==hwnd:return True
        kernel=getattr(self,"kernel",None)
        current=kernel.GetCurrentThreadId() if kernel else 0
        foreground=user.GetWindowThreadProcessId(user.GetForegroundWindow(),None)
        attached=bool(current and foreground and foreground!=current and user.AttachThreadInput(current,foreground,True))
        try:
            user.keybd_event(VK_MENU,0,0,0);user.keybd_event(VK_MENU,0,KEYEVENTF_KEYUP,0)
            user.BringWindowToTop(hwnd)
            user.SetForegroundWindow(hwnd)
        finally:
            if attached:user.AttachThreadInput(current,foreground,False)
        if user.GetForegroundWindow()==hwnd:return True
        # Still refused: at least lift it above other windows so it can be seen.
        flags=SWP_NOSIZE|SWP_NOMOVE|SWP_SHOWWINDOW
        user.SetWindowPos(hwnd,HWND_TOPMOST,0,0,0,0,flags);user.SetWindowPos(hwnd,HWND_NOTOPMOST,0,0,0,0,flags)
        return False

    def bring_forward(self, manual=False):
        current=self.snapshot()
        candidates=[]
        for hwnd, row in current.items():
            pid, process, title=row
            if not is_signin(process,title):continue
            identity=(hwnd,pid)
            # Automatic handoff only for a newly opened or changed sign-in window.
            if not manual and (self.before.get(hwnd)==row or identity in self.raised):continue
            candidates.append((hwnd,identity))
        if manual and not candidates:
            # The analyst asked explicitly: fall back to a browser window that opened or
            # changed since sign-in started (its tab title may not be a recognised one).
            candidates=[(hwnd,(hwnd,row[0])) for hwnd,row in current.items()
                        if row[1] in BROWSERS and self.before.get(hwnd)!=row]
        for hwnd, identity in candidates:
            if self.focus(hwnd):
                self.raised.add(identity)
                return True
        return False


def create_handoff():
    if os.name != "nt":return None
    try:return WindowsSignIn()
    except (OSError,AttributeError):return None
