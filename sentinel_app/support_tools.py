"""Support tools: read-only checks of this PC, the support info an analyst pastes into a ticket, and
the update notice read from a folder the team chooses. No Tk here, and nothing calls a web service:
the command-line tools are asked for their versions and sign-in state, and the update folder is read
from disk (or the team share it names)."""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from . import __version__

MIN_AZURE_CLI = (2, 76)
LATEST_FILE = "latest.json"
LATEST_LIMIT = 64 * 1024  # A latest.json larger than this isn't the small file the guide describes.
NOTES_CHARS = 240
LOG_LINES = 100
MESSAGE_CHARS = 160  # An error message in the support info is cut to this, after IDs and tokens are removed.
AZ_TIMEOUT, GH_TIMEOUT = 60, 30  # Azure CLI can take a while to start on a locked-down PC.

# Display order in Check this PC and in the support info.
CHECKS = (("azure_cli", "Azure CLI"), ("github_cli", "GitHub CLI"), ("github_signin", "GitHub sign-in"),
          ("data_folder", "Data folder"), ("app_version", "App version"))


class Check(NamedTuple):
    key: str
    title: str
    ok: bool
    detail: str  # What was found and, when it needs action, what to do about it.
    support: str  # The same, briefly, for the support info. Never an account name.
    action: str = ""  # A follow-up the dialog offers: github_login, set_update_folder or open_update_folder.


def version_tuple(text):
    """'2.76.0' as (2, 76, 0); None when it isn't a dotted version number."""
    match = re.fullmatch(r"\s*v?(\d+(?:\.\d+){0,3})\s*", str(text or ""))
    return tuple(int(part) for part in match[1].split(".")) if match else None


def newer(candidate, current=__version__):
    """True when version ``candidate`` is later than ``current`` ('2.2' and '2.2.0' are the same)."""
    a, b = version_tuple(candidate), version_tuple(current)
    if a is None or b is None: return False
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def _run(args, timeout, env=None):
    """Run a command-line tool without a console window or input; returns the CompletedProcess."""
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env, shell=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _first_line(text):
    return next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")


# ------------------------------------------------------------------ the checks
def check_azure_cli(exe):
    """Azure CLI installed, and 2.76 or later. ``az version`` runs with an empty, temporary config
    folder and telemetry off, so the analyst's own Azure CLI sign-ins and settings are never touched."""
    need = ".".join(map(str, MIN_AZURE_CLI))
    if not exe:
        return Check("azure_cli", "Azure CLI", False,
                     f"Not installed. Ask your administrator to install Azure CLI {need} or later, then close and "
                     "reopen this app.", "not installed")
    version, problem = None, ""
    try:
        with tempfile.TemporaryDirectory(prefix="sentinel-check-", ignore_cleanup_errors=True) as config:
            env = dict(os.environ, AZURE_CONFIG_DIR=config, AZURE_CORE_COLLECT_TELEMETRY="false",
                       AZURE_CORE_ONLY_SHOW_ERRORS="true", AZURE_CORE_NO_COLOR="true")
            result = _run([exe, "version", "--output", "json"], AZ_TIMEOUT, env)
        if result.returncode == 0:
            value = json.loads(result.stdout).get("azure-cli")
            version = value if version_tuple(value) else None
        problem = "" if version else _first_line(result.stderr) or "no version in its answer"
    except subprocess.TimeoutExpired:
        problem = "it didn't answer within " + str(AZ_TIMEOUT) + " seconds"
    except (OSError, ValueError, AttributeError) as error:
        problem = str(error) or type(error).__name__
    if version is None:
        return Check("azure_cli", "Azure CLI", False,
                     f"Installed, but it didn't report its version ({problem[:120]}). Ask your administrator to "
                     f"repair or update Azure CLI to {need} or later, then reopen this app.", "version unknown")
    if version_tuple(version)[:2] < MIN_AZURE_CLI:
        return Check("azure_cli", "Azure CLI", False,
                     f"{version} is installed, but {need} or later is needed. Ask your administrator to update Azure "
                     "CLI, then close and reopen this app.", version + " (too old)")
    return Check("azure_cli", "Azure CLI", True, f"{version} is installed. Version {need} or later is needed.", version)


def _github_env():
    # No update notice (which would ask GitHub for the latest release) and no prompts.
    return dict(os.environ, GH_NO_UPDATE_NOTIFIER="1", GH_PROMPT_DISABLED="1", NO_COLOR="1")


def check_github_cli(exe):
    if not exe:
        return Check("github_cli", "GitHub CLI", False,
                     "Not installed. It is needed for the rule library and for submitting rules. Ask your "
                     "administrator to install GitHub CLI, then close and reopen this app.", "not installed")
    try:
        result = _run([exe, "--version"], GH_TIMEOUT, _github_env())
        match = re.search(r"gh version (\d+(?:\.\d+)+)", result.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        match = None
    if not match:
        return Check("github_cli", "GitHub CLI", False,
                     "Installed, but it didn't report its version. Ask your administrator to repair or reinstall "
                     "GitHub CLI, then reopen this app.", "version unknown")
    return Check("github_cli", "GitHub CLI", True, match[1] + " is installed.", match[1])


def check_github_signin(exe):
    """Whether GitHub CLI has a signed-in github.com account. Its output is parsed for the account
    name only; nothing it prints (the masked token included) is kept."""
    title = "GitHub sign-in"
    if not exe:
        return Check("github_signin", title, False, "Install GitHub CLI first (see above), then sign in to GitHub.",
                     "GitHub CLI not installed")
    try:
        result = _run([exe, "auth", "status", "--hostname", "github.com"], GH_TIMEOUT, _github_env())
        code, text = result.returncode, (result.stdout or "") + "\n" + (result.stderr or "")
    except subprocess.TimeoutExpired:
        code, text = None, "timed out"
    except OSError as error:
        code, text = None, str(error)
    low = text.casefold()
    if code == 0:
        account = re.search(r"logged in to github\.com (?:account|as) ([A-Za-z0-9-]+)", text, re.IGNORECASE)
        return Check("github_signin", title, True, "Signed in as " + account[1] + "." if account else "Signed in.",
                     "signed in")
    if any(words in low for words in ("timed out", "timeout", "could not resolve", "no such host", "dial tcp",
                                      "connection", "network")):
        return Check("github_signin", title, False,
                     "GitHub CLI couldn't reach GitHub to confirm the sign-in. Check your network or VPN, then "
                     "select Check again.", "couldn't reach GitHub")
    if "not logged in" in low:
        return Check("github_signin", title, False,
                     "Not signed in. Select Sign in to GitHub and enter the one-time code it shows in GitHub.",
                     "not signed in", "github_login")
    return Check("github_signin", title, False,
                 "The saved GitHub sign-in no longer works. Select Sign in to GitHub to sign in again.",
                 "sign-in no longer valid", "github_login")


def check_data_folder(folder):
    """The app's data folder can be written: a tiny test file is created and removed again."""
    folder = Path(folder)
    probe = folder / (".write-test-" + uuid.uuid4().hex)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8"); probe.unlink()
    except OSError as error:
        reason = error.strerror or str(error)
        return Check("data_folder", "Data folder", False,
                     f"The app can't save to {folder} ({reason}). Move the app to a folder you can write to, such "
                     "as Documents, or ask your administrator for write access.", "can't be written (" + reason + ")")
    return Check("data_folder", "Data folder", True, f"{folder} can be written.", "writable")


def read_latest(folder):
    """The {"version", "notes"} in ``folder``/latest.json. Raises ValueError with a plain reason."""
    path = Path(folder) / LATEST_FILE
    try:
        with path.open("rb") as stream:
            data = stream.read(LATEST_LIMIT + 1)
    except FileNotFoundError:
        raise ValueError("the folder or its " + LATEST_FILE + " can't be found") from None
    except OSError as error:
        raise ValueError(LATEST_FILE + " couldn't be opened (" + (error.strerror or str(error)) + ")") from None
    if len(data) > LATEST_LIMIT: raise ValueError(LATEST_FILE + " is too large")
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError(LATEST_FILE + " isn't valid JSON") from None
    if not isinstance(value, dict) or not isinstance(value.get("version"), str) or version_tuple(value["version"]) is None:
        raise ValueError(LATEST_FILE + ' has no "version" such as "2.2.0"')
    notes = value.get("notes")
    notes = " ".join(notes.split()) if isinstance(notes, str) else ""
    if len(notes) > NOTES_CHARS: notes = notes[:NOTES_CHARS - 1].rstrip() + "…"
    return dict(version=value["version"].strip(), notes=notes)


def update_message(latest, folder):
    """The sentence the Connect banner and Check this PC use when ``latest`` is newer."""
    text = (f"Sentinel Workspace {latest['version']} is available (you have {__version__}). Get it from {folder}, "
            "then close this copy and start the new one.")
    return text + (" What's new: " + latest["notes"] if latest.get("notes") else "")


def check_app_version(update_folder):
    title, folder = "App version", (update_folder or "").strip()
    if not folder:
        return Check("app_version", title, True,
                     f"{__version__}. No update folder is set, so the app can't tell you when a newer version is "
                     "ready. Your team lead can give you the folder.", __version__ + "; no update folder set",
                     "set_update_folder")
    try:
        latest = read_latest(folder)
    except ValueError as error:
        return Check("app_version", title, False,
                     f"{__version__}. The update folder {folder} couldn't be read: {error}. Check that it opens in "
                     "File Explorer, or change it.", __version__ + "; update folder unreadable (" + str(error) + ")",
                     "set_update_folder")
    if newer(latest["version"]):
        return Check("app_version", title, False, update_message(latest, folder),
                     f"{__version__}; {latest['version']} is available in the update folder", "open_update_folder")
    return Check("app_version", title, True, f"{__version__}, the newest version in {folder}.",
                 __version__ + "; up to date with the update folder", "set_update_folder")


def run_checks(data, update_folder="", report=None):
    """Every check, in CHECKS order. The quick ones run first, and ``report(check)`` hears about each
    one as it finishes, so a dialog can fill in while Azure CLI is still starting."""
    gh = shutil.which("gh")
    steps = (("data_folder", lambda: check_data_folder(data)), ("app_version", lambda: check_app_version(update_folder)),
             ("github_cli", lambda: check_github_cli(gh)), ("github_signin", lambda: check_github_signin(gh)),
             ("azure_cli", lambda: check_azure_cli(shutil.which("az"))))
    done = {}
    for key, step in steps:
        done[key] = step()
        if report: report(done[key])
    return [done[key] for key, _ in CHECKS]


# ------------------------------------------------------------------ support info
def system_facts(tk_version, data):
    """What the support info says about the app and this PC."""
    build = "packaged exe" if getattr(sys, "frozen", False) else "run from source"
    if sys.platform == "win32":
        release, version, _, _ = platform.win32_ver()
        edition = getattr(platform, "win32_edition", lambda: "")() or ""
        windows = " ".join(part for part in ("Windows", release, edition, version) if part)
    else:
        windows = platform.platform()
    bits = "64-bit" if sys.maxsize > 2 ** 32 else "32-bit"
    return [("App version", f"{__version__} ({build})"), ("Windows", f"{windows} ({platform.machine() or 'unknown'})"),
            ("Python", f"{sys.version.split()[0]} ({bits})"), ("Tk", str(tk_version)), ("Data folder", str(data))]


_REDACTIONS = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+(?:\.[\w-]*)?"), "<token>"),  # A JSON web token.
    (re.compile(r"\b(?:gh[opsur]_|github_pat_)\w+"), "<token>"),
    (re.compile(r"\b(bearer\s+)[\w.~+/=-]+", re.I), r"\1<token>"),
    (re.compile(r"\b((?:access_|refresh_|id_)?token|password|(?:client_)?secret|api_?key)([\"']?\s*[=:]\s*[\"']?)"
                r"[^\s\"',;]+", re.I), r"\1\2<token>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<account>"),
    (re.compile(r"\b[\w-]+\.onmicrosoft\.(?:com|us)\b", re.I), "<tenant>"),
    (re.compile(r"[A-Za-z0-9+/_=-]{40,}"), "<token>"),  # Any other long opaque value (a key, a claims challenge).
)


def redact(text, clients=()):
    """``text`` without GUIDs (tenant, subscription and object IDs), tokens, accounts, tenant domains
    and the saved clients' names and tenants."""
    names = sorted({str(value).strip() for client in clients for value in (client.get("name"), client.get("tenant"))
                    if value and len(str(value).strip()) >= 2}, key=len, reverse=True)
    if names:
        text = re.sub(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)", "<client>", text, flags=re.I)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


_HEADER = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\s+Sentinel Workspace\b")
_FRAME = re.compile(r'\s+File "[^"]*", line \d+')
_CHAINS = ("Traceback (most recent call last):", "During handling of the above exception",
           "The above exception was the direct cause")
_EXCEPTION = re.compile(r"([A-Za-z_][\w.]*)(?::\s?(.*))?$")
# Quoted text in a message (a rule or workspace name, a value from Azure); "couldn't" is not a quote.
_QUOTED = re.compile(r"(?<!\w)([\"'])[^\"'\n]{1,300}\1(?!\w)")


def sanitize_log(lines, clients=()):
    """errors.log lines that are safe to share. Entry headings, traceback frames and the app's own
    source lines are kept; an error's message keeps its first line, without IDs, accounts, tokens,
    client names or quoted text, and cut short. Anything else (a message's further lines, which could quote a rule or
    an Azure answer) is left out and counted."""
    out, state, dropped = [], "unknown", 0

    def flush():
        nonlocal dropped
        if dropped: out.append(f"    [{dropped} line{'' if dropped == 1 else 's'} left out]")
        dropped = 0

    for line in lines:
        line = line.rstrip("\r\n")
        stripped = line.strip()
        if _HEADER.match(line) or stripped.startswith(_CHAINS) or _FRAME.match(line):
            flush(); out.append(redact(line, clients))
            state = "frame" if _FRAME.match(line) else "start"
        elif not stripped:
            flush(); out.append("")
        elif state in ("frame", "code") and line.startswith("    "):
            out.append(redact(line, clients)); state = "code"  # The app's source line, or its ^^^ marker.
        elif state in ("frame", "code") and not line[:1].isspace() and _EXCEPTION.match(line):
            name, message = _EXCEPTION.match(line).groups()
            message = " ".join(_QUOTED.sub(r"\1…\1", redact(message or "", clients)).split())
            if len(message) > MESSAGE_CHARS: message = message[:MESSAGE_CHARS - 1].rstrip() + "…"
            out.append(name + (": " + message if message else "")); state = "message"
        else:
            dropped += 1
    flush()
    return out


def log_tail(path, clients=(), lines=LOG_LINES):
    """The last ``lines`` lines of errors.log, made safe to share; [] when there is no log."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return sanitize_log(text.splitlines()[-lines:], clients)


def support_text(facts, checks, log_path, clients=(), now=None):
    """The support info: versions, this PC, each check and the recent errors. Client names, tenant
    IDs, accounts, tokens and rule content are never included."""
    now = now or datetime.now()
    lines = ["Sentinel Workspace support info", "Collected " + now.strftime("%Y-%m-%d %H:%M") + " (local time)", ""]
    lines += [f"{key}: {value}" for key, value in facts]
    lines += ["", "Checks"]
    lines += [f"- {check.title}: {'OK' if check.ok else 'needs action'} ({redact(check.support, clients)})"
              for check in checks]
    lines += ["", f"errors.log, last {LOG_LINES} lines (IDs, accounts, client names, tokens and message details "
                  "left out):"]
    tail = log_tail(log_path, clients)
    lines += tail if any(line.strip() for line in tail) else ["(empty: no unexpected errors have been recorded)"]
    return "\n".join(lines) + "\n"
