"""Read-only test runs of a draft rule's KQL against the connected Log Analytics workspace.

One query per run, through the app's Azure CLI session (``az rest`` to the Log Analytics query
API, as the other live reads do). The query gets a row cap appended and a time range no longer
than the rule's Look back, capped at MAX_PERIOD; nothing is saved, changed or deployed."""
import json
import re
import time
import uuid
from pathlib import Path

from .audit_evidence import LOGS
from .auth_recovery import AuthenticationRequired
from .desktop_backend import Stop, guid, require
from . import rule_schema

ROW_LIMIT = 50          # Rows shown. One more is asked for, to tell whether there are more.
MAX_PERIOD = 86400      # The test looks back at most this far (seconds) by default, whatever Look back says.
TIMEOUT = 120           # Seconds to wait for Log Analytics before giving up.
CELL_CHARS = 2000       # Longest cell text kept for the table (the full value is in Log Analytics).

NO_WORKSPACE = "Test query needs a workspace."
GOVERNMENT = "Test queries support Azure public cloud only."
TIMED_OUT = "Test query timed out"
# (words in Log Analytics' answer, lowercase; what this module calls the problem). advice.py explains
# each in plain words. First match wins.
PROBLEMS = (
    (("syntaxerror",), "Test query syntax error"),
    (("semanticerror",), "Test query semantic error"),
    (("insufficientaccess", "authorizationfailed", "forbidden"), "Test query access denied"),
    (("workspacenotfound",), "Test query workspace not found"),
    (("gatewaytimeout", "timed out", "timeout"), TIMED_OUT),
)


class QueryFailed(Stop):
    """Log Analytics didn't run the query. ``problem`` names the kind (one of PROBLEMS, or None),
    ``detail`` is Log Analytics' own explanation (the innermost message), ``raw`` the full answer."""

    def __init__(self, problem, detail, raw):
        self.problem, self.detail, self.raw = problem, detail, raw
        super().__init__((problem or "Test query failed") + ": " + (detail or "Log Analytics didn't run the query.")
                         + ("\n" + raw if raw and raw != detail else ""))


def test_window(kind, period, cap=MAX_PERIOD):
    """Seconds of data the test covers: the rule's Look back, at most ``cap``. A near real-time rule
    has no Look back, and an unreadable one is checked elsewhere, so both get ``cap``."""
    try: seconds = rule_schema.seconds(period) if kind != "NRT" else None
    except (ValueError, TypeError): seconds = None
    return cap if not seconds or seconds <= 0 else min(seconds, cap)


def iso_duration(seconds):
    """86400 -> 'P1D', 14400 -> 'PT4H', 5400 -> 'PT90M': the form the query API's timespan takes."""
    seconds = int(seconds)
    if seconds % 86400 == 0: return f"P{seconds // 86400}D"
    if seconds % 3600 == 0: return f"PT{seconds // 3600}H"
    if seconds % 60 == 0: return f"PT{seconds // 60}M"
    return f"PT{seconds}S"


def capped_query(query, limit=ROW_LIMIT):
    """The query with ``| take limit+1`` appended on a line of its own, so a trailing // comment can't
    swallow it. Comment-only lines and semicolons at the end are dropped first: after a final ';' the
    cap would start a statement of its own."""
    lines = str(query or "").replace("\r\n", "\n").split("\n")
    while True:
        while lines and (not lines[-1].strip() or lines[-1].strip().startswith("//")): lines.pop()
        if not lines or not lines[-1].rstrip().endswith(";"): break
        lines[-1] = lines[-1].rstrip()[:-1]
    text = "\n".join(lines).rstrip()
    require(text, "The query is empty. Paste the KQL query first.")
    require(not text.endswith("|"), "The query ends with |. Remove it or finish the last step.")
    return text + "\n| take " + str(limit + 1)


def innermost(error):
    """Log Analytics' most specific explanation in an error object: the deepest innererror message,
    with the line and position when it gives them."""
    detail = None
    while isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            detail = message.strip()
            if isinstance(error.get("line"), int) and "line" not in detail.lower():
                detail += f" (line {error['line']}" + (f", position {error['pos']})" if isinstance(error.get("pos"), int) else ")")
        error = error.get("innererror")
    return detail


def error_object(text):
    """The {"error": ...} object inside Azure CLI's message ('Bad Request({...})'), or None."""
    start = text.find("{")
    while start >= 0:
        try:
            value, _ = json.JSONDecoder().raw_decode(text, start)
            if isinstance(value, dict) and isinstance(value.get("error"), dict): return value["error"]
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return None


def failure(raw):
    """A QueryFailed for Azure's answer ``raw``, named by PROBLEMS."""
    raw = str(raw).strip()
    found = error_object(raw)
    flat = (json.dumps(found) if found else raw).lower() + " " + raw.lower()
    problem = next((name for words, name in PROBLEMS if any(word in flat for word in words)), None)
    detail = innermost(found) if found else None
    if detail is None:  # Azure CLI's own message, without its ERROR: prefix.
        detail = re.sub(r"^ERROR:\s*", "", raw.splitlines()[0] if raw else "").strip() or None
    return QueryFailed(problem, detail, raw)


def cell(value):
    """One result cell as table text: JSON for objects, true/false, nothing for null, one line."""
    if value is None: text = ""
    elif isinstance(value, bool): text = "true" if value else "false"
    elif isinstance(value, (dict, list)): text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    else: text = str(value)
    text = " ".join(text.split()) if "\n" in text or "\r" in text or "\t" in text else text
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS - 1] + "…"


def read_result(response, limit=ROW_LIMIT):
    """(columns, rows, more, partial) from a query API response: up to ``limit`` rows as cell text,
    whether there were more, and Log Analytics' note when it returned only part of the result."""
    require(isinstance(response, dict), "Log Analytics returned an unexpected answer.")
    tables = response.get("tables")
    primary = next((t for t in tables or () if isinstance(t, dict) and t.get("name") == "PrimaryResult"), None)
    if primary is None:
        if isinstance(response.get("error"), dict): raise failure(json.dumps({"error": response["error"]}))
        raise Stop("Log Analytics returned no results table.")
    columns = [str(c.get("name", "")) for c in primary.get("columns", []) if isinstance(c, dict)]
    rows = primary.get("rows", [])
    require(isinstance(rows, list) and all(isinstance(r, list) and len(r) == len(columns) for r in rows),
            "Log Analytics returned rows that don't match their columns.")
    partial = innermost(response["error"]) if isinstance(response.get("error"), dict) else None
    return columns, [[cell(v) for v in row] for row in rows[:limit]], len(rows) > limit, partial


def run_test_query(session, workspace, query, seconds, limit=ROW_LIMIT, timeout=TIMEOUT):
    """Run ``query`` read-only against ``workspace`` over the last ``seconds`` and return what came back:
    columns, rows (at most ``limit``), more, partial, elapsed (seconds), seconds and workspace_name.

    Raises QueryFailed when Log Analytics doesn't run it, Stop when there is no workspace or it is in
    Azure Government, and AuthenticationRequired when Microsoft wants a fresh sign-in."""
    require(session is not None and workspace and workspace.get("workspace_id"), NO_WORKSPACE)
    text = capped_query(query, limit)
    session.verify(workspace)
    require((session.az("cloud", "show") or {}).get("name") == "AzureCloud", GOVERNMENT)
    url = LOGS + "/v1/workspaces/" + guid(workspace["workspace_id"], "workspace ID") + "/query"
    # The query goes in a request body file: KQL's quotes and pipes never pass through a command line.
    body = Path(session.folder) / ("test-query-" + uuid.uuid4().hex + ".json")
    body.write_text(json.dumps({"query": text, "timespan": iso_duration(seconds)}), encoding="ascii")
    started = time.monotonic()
    try:
        response = session.az("rest", "--method", "post", "--url", url, "--body", "@" + str(body),
                              "--resource", LOGS, timeout=timeout)
    except AuthenticationRequired:
        raise
    except Stop as error:
        if str(error).startswith("The operation timed out"):  # Session.execute's own timeout.
            raise QueryFailed(TIMED_OUT, f"No answer from Log Analytics after {timeout} seconds.", str(error)) from None
        raise failure(error) from None
    finally:
        elapsed = time.monotonic() - started
        try: body.unlink()
        except OSError: pass
    columns, rows, more, partial = read_result(response, limit)
    return dict(columns=columns, rows=rows, more=more, partial=partial, elapsed=elapsed, seconds=seconds,
                workspace_name=workspace.get("workspace_name", ""))
