"""Run test query: the backend with a fake Azure CLI session, and the Rule builder's Query & schedule step.
Nothing here signs in or calls Azure."""
import json
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_app import advice
from sentinel_app import desktop_app as ui
from sentinel_app import desktop_backend as backend
from sentinel_app import query_runner as runner
from sentinel_app.auth_recovery import AuthenticationRequired
from sentinel_app.gov_onboarding import GOV_CLOUD
from sentinel_app.lighthouse_onboarding import Stop
from test_desktop import WORKSPACE, WS
from test_rule_drafts import valid_form

QUERY_URL = "https://api.loganalytics.io/v1/workspaces/" + WS + "/query"
COLUMNS = [{"name": "TimeGenerated", "type": "datetime"}, {"name": "UserPrincipalName", "type": "string"},
           {"name": "Attempts", "type": "long"}]


def response(count, error=None):
    rows = [["2026-11-09T10:%02d:00Z" % (i % 60), "user%d@example.com" % i, i] for i in range(count)]
    result = {"tables": [{"name": "PrimaryResult", "columns": COLUMNS, "rows": rows}]}
    if error: result["error"] = error
    return result


def bad_request(code, message, inner_code=None, inner=None):
    """Azure CLI's message for a 400 from the query API."""
    error = {"message": "The request had some invalid properties", "code": "BadArgumentError",
             "innererror": {"code": code, "message": message}}
    if inner: error["innererror"]["innererror"] = dict(inner, code=inner_code)
    return "ERROR: Bad Request(" + json.dumps({"error": error}) + ")"


SYNTAX = bad_request("SyntaxError", "A recognition error occurred in the query.", "SYN0002",
                     {"message": "Query could not be parsed at 'wher' on line [2,2]", "line": 2, "pos": 2})
SEMANTIC = bad_request("SemanticError", "A semantic error occurred.", "SEM0100",
                       {"message": "'where' operator: Failed to resolve column or scalar expression named 'Foo'"})
DENIED = ('ERROR: Forbidden({"error":{"message":"The provided credentials have insufficient access to perform the '
          'requested operation","code":"InsufficientAccessError","correlationId":"synthetic"}})')
MFA = "AADSTS50076: You must use multi-factor authentication to access 'https://api.loganalytics.io'. Correlation ID: synthetic"


class FakeSession:
    """Answers like a signed-in Azure CLI session and records every call; nothing is run."""

    def __init__(self, folder, answer=None, cloud="AzureCloud"):
        self.folder, self.answer, self.cloud = Path(folder), answer, cloud
        self.calls, self.bodies, self.timeouts, self.verified = [], [], [], []

    def verify(self, workspace):
        self.verified.append(workspace); return {}

    def az(self, *args, timeout=None):
        self.calls.append(args)
        if args[:2] == ("cloud", "show"): return {"name": self.cloud}
        self.timeouts.append(timeout)
        body = Path(args[args.index("--body") + 1][1:])
        self.bodies.append((body, json.loads(body.read_text(encoding="ascii"))))
        if isinstance(self.answer, Exception): raise self.answer
        return self.answer if self.answer is not None else response(3)


class QueryRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)

    def session(self, answer=None, cloud="AzureCloud"):
        return FakeSession(self.temp.name, answer, cloud)

    def test_row_cap_is_appended_on_a_line_of_its_own(self):
        self.assertEqual(runner.capped_query("SigninLogs | where ResultType != 0"),
                         "SigninLogs | where ResultType != 0\n| take 51")
        # A trailing comment can't swallow the cap, and a final ';' or comment-only lines don't break it.
        self.assertEqual(runner.capped_query("SigninLogs // failures"), "SigninLogs // failures\n| take 51")
        self.assertEqual(runner.capped_query("let x = 1;\nSigninLogs;\n// done\n\n  "),
                         "let x = 1;\nSigninLogs\n| take 51")
        self.assertEqual(runner.capped_query("T\r\n| take 5;;", limit=9), "T\n| take 5\n| take 10")
        for query, words in (("", "empty"), ("  \n// only a comment", "empty"), ("T |", "ends with |"),
                             ("T |\n// later", "ends with |")):
            with self.subTest(query=query), self.assertRaisesRegex(Stop, words):
                runner.capped_query(query)

    def test_window_is_the_look_back_capped_at_a_day(self):
        self.assertEqual(runner.test_window("Scheduled", "PT4H"), 4 * 3600)
        self.assertEqual(runner.test_window("Scheduled", "P5D"), 86400)
        self.assertEqual(runner.test_window("Scheduled", "P5D", cap=7 * 86400), 5 * 86400)
        self.assertEqual(runner.test_window("NRT", "PT1H"), 86400)  # No Look back.
        self.assertEqual(runner.test_window("Scheduled", "soon"), 86400)
        self.assertEqual([runner.iso_duration(s) for s in (86400, 4 * 3600, 5400, 90)], ["P1D", "PT4H", "PT90M", "PT90S"])

    def test_runs_read_only_against_the_workspace_with_the_query_in_a_body_file(self):
        session = self.session()
        query = 'SigninLogs\n| where UserPrincipalName has "admin" and ResultType != "0" // "quoted" | piped'
        result = runner.run_test_query(session, WORKSPACE, query, 4 * 3600)
        self.assertEqual(session.verified, [WORKSPACE])
        self.assertEqual(session.calls[0], ("cloud", "show"))
        self.assertEqual(len(session.calls), 2)  # The check, then one query: nothing else is read or changed.
        call = session.calls[1]
        self.assertEqual(call[:4], ("rest", "--method", "post", "--url"))
        self.assertEqual(call[4], QUERY_URL)
        self.assertEqual(call[call.index("--resource") + 1], "https://api.loganalytics.io")
        body_path, body = session.bodies[0]
        self.assertEqual(body, {"query": query + "\n| take 51", "timespan": "PT4H"})
        # KQL's quotes and pipes reach Azure only inside the file, never on the command line.
        self.assertTrue(all('"' not in str(arg) and "|" not in str(arg) for arg in call))
        self.assertFalse(body_path.exists())
        self.assertEqual(session.timeouts, [runner.TIMEOUT])
        self.assertEqual(result["columns"], ["TimeGenerated", "UserPrincipalName", "Attempts"])
        self.assertEqual(result["rows"][2], ["2026-11-09T10:02:00Z", "user2@example.com", "2"])
        self.assertFalse(result["more"]); self.assertIsNone(result["partial"])
        self.assertEqual(result["seconds"], 4 * 3600); self.assertGreaterEqual(result["elapsed"], 0)
        self.assertEqual(result["workspace_name"], "law-example-sentinel")

    def test_shows_at_most_fifty_rows_and_says_there_are_more(self):
        result = runner.run_test_query(self.session(response(51)), WORKSPACE, "T", 3600)
        self.assertEqual(len(result["rows"]), 50); self.assertTrue(result["more"])
        result = runner.run_test_query(self.session(response(50)), WORKSPACE, "T", 3600)
        self.assertEqual(len(result["rows"]), 50); self.assertFalse(result["more"])

    def test_partial_results_keep_log_analytics_reason(self):
        error = {"code": "PartialError", "message": "There were some errors when processing your query.",
                 "details": [], "innererror": {"code": "LimitsExceeded", "message": "Query exceeded memory limits."}}
        result = runner.run_test_query(self.session(response(2, error)), WORKSPACE, "T", 3600)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["partial"], "Query exceeded memory limits.")

    def test_cells_read_as_one_line_of_text(self):
        self.assertEqual([runner.cell(v) for v in (None, True, False, 3.5, {"a": [1, "b"]}, "two\nlines\tand tab")],
                         ["", "true", "false", "3.5", '{"a":[1,"b"]}', "two lines and tab"])
        long = runner.cell("x" * 5000)
        self.assertEqual(len(long), runner.CELL_CHARS); self.assertTrue(long.endswith("…"))

    def test_no_workspace_and_azure_government_are_refused_before_any_query(self):
        session = self.session()
        for workspace in (None, {}, dict(WORKSPACE, workspace_id="")):
            with self.assertRaises(Stop) as caught: runner.run_test_query(session, workspace, "T", 3600)
            self.assertEqual(str(caught.exception), runner.NO_WORKSPACE)
        with self.assertRaises(Stop): runner.run_test_query(None, WORKSPACE, "T", 3600)
        session = self.session(cloud=GOV_CLOUD)
        with self.assertRaises(Stop) as caught: runner.run_test_query(session, WORKSPACE, "T", 3600)
        self.assertEqual(str(caught.exception), runner.GOVERNMENT)
        self.assertEqual(session.calls, [("cloud", "show")])
        self.assertIn("Azure Government", advice.friendly(str(caught.exception)))
        self.assertIn("Sign in on Connect", advice.friendly(runner.NO_WORKSPACE))

    def test_problems_are_named_for_plain_advice_with_log_analytics_reason(self):
        for raw, problem, detail, words in (
                (SYNTAX, "Test query syntax error", "Query could not be parsed at 'wher' on line [2,2]", "syntax error"),
                (SEMANTIC, "Test query semantic error", "'where' operator: Failed to resolve column or scalar "
                                                        "expression named 'Foo'", "table, column or function"),
                (DENIED, "Test query access denied", "The provided credentials have insufficient access to perform "
                                                     "the requested operation", "Log Analytics Reader"),
                ('ERROR: Gateway Timeout({"error":{"code":"GatewayTimeout","message":"Gateway timeout"}})',
                 runner.TIMED_OUT, "Gateway timeout", "took too long"),
                ('ERROR: Not Found({"error":{"code":"WorkspaceNotFoundError","message":"The workspace could not be '
                 'found"}})', "Test query workspace not found", "The workspace could not be found", "Refresh"),
                ("ERROR: Something unexpected happened.", None, "Something unexpected happened.", None)):
            with self.subTest(problem=problem):
                with self.assertRaises(runner.QueryFailed) as caught:
                    runner.run_test_query(self.session(Stop(raw)), WORKSPACE, "T", 3600)
                error = caught.exception
                self.assertEqual((error.problem, error.detail, error.raw), (problem, detail, raw))
                tip = advice.lookup(problem or str(error), workspace="law-example-sentinel")
                if words is None: self.assertIsNone(tip)
                else: self.assertIn(words, tip)
                # Through the app's generic path (a task's failure), the advice is the same.
                if words: self.assertEqual(advice.lookup(str(error), workspace="law-example-sentinel"), tip)

    def test_the_request_body_file_is_removed_when_the_query_fails(self):
        session = self.session(Stop(SYNTAX))
        with self.assertRaises(runner.QueryFailed): runner.run_test_query(session, WORKSPACE, "T", 3600)
        self.assertFalse(session.bodies[0][0].exists())
        self.assertEqual(list(Path(self.temp.name).glob("test-query-*")), [])


class QueryRunnerSessionTests(unittest.TestCase):
    """Through the real Session.az, with only the Azure CLI process replaced."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = backend.Session(Path(self.temp.name))
        self.session.az_exe = "fake-az"
        self.verify = patch.object(self.session, "verify", return_value={}); self.verify.start()
        self.addCleanup(self.verify.stop)

    def answers(self, *results):
        replies = iter([subprocess.CompletedProcess([], 0, json.dumps({"name": "AzureCloud"}), "")] + list(results))
        calls = []
        def execute(executable, args, data=None, timeout=900):
            calls.append((list(args), timeout))
            return next(replies)
        return calls, patch.object(self.session, "execute", side_effect=execute)

    def test_query_reaches_azure_cli_as_a_read_with_a_short_timeout(self):
        calls, execute = self.answers(subprocess.CompletedProcess([], 0, json.dumps(response(1)), ""))
        with execute: result = runner.run_test_query(self.session, WORKSPACE, "T", 3600)
        self.assertEqual(result["rows"], [["2026-11-09T10:00:00Z", "user0@example.com", "0"]])
        (cloud, cloud_timeout), (args, timeout) = calls
        self.assertEqual((cloud, cloud_timeout), (["cloud", "show", "--output", "json"], 900))
        self.assertEqual(args[:5], ["rest", "--method", "post", "--url", QUERY_URL])
        self.assertEqual(args[-2:], ["--output", "json"]); self.assertEqual(timeout, runner.TIMEOUT)

    def test_a_syntax_error_from_azure_cli_is_named(self):
        _, execute = self.answers(subprocess.CompletedProcess([], 1, "", SYNTAX))
        with execute, self.assertRaises(runner.QueryFailed) as caught:
            runner.run_test_query(self.session, WORKSPACE, "T\n| wher x", 3600)
        self.assertEqual(caught.exception.problem, "Test query syntax error")
        self.assertEqual(caught.exception.detail, "Query could not be parsed at 'wher' on line [2,2]")

    def test_mfa_challenge_still_asks_for_a_fresh_sign_in(self):
        _, execute = self.answers(subprocess.CompletedProcess([], 1, "", MFA))
        with execute, self.assertRaises(AuthenticationRequired):
            runner.run_test_query(self.session, WORKSPACE, "T", 3600)

    def test_no_answer_in_time_is_a_timeout_not_a_cloud_change(self):
        replies = iter([subprocess.CompletedProcess([], 0, json.dumps({"name": "AzureCloud"}), "")])
        def run(command, **kw):
            if "rest" in command: raise subprocess.TimeoutExpired(command, kw["timeout"])
            return next(replies)
        with patch("subprocess.run", side_effect=run) as process, self.assertRaises(runner.QueryFailed) as caught:
            runner.run_test_query(self.session, WORKSPACE, "T", 3600)
        self.assertEqual(process.call_args.kwargs["timeout"], runner.TIMEOUT)
        self.assertEqual(caught.exception.problem, runner.TIMED_OUT)
        self.assertIn(str(runner.TIMEOUT) + " seconds", caught.exception.detail)
        self.assertIn("took too long", advice.lookup(caught.exception.problem))
        self.assertEqual(list(Path(self.temp.name).glob("test-query-*")), [])


class TestQueryPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        for module in (ui, backend):
            p = patch.object(module, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.page = self.app.rule_builder
        self.addCleanup(self.cleanup)
        self.page.load_form(valid_form()); self.page.steps.select(1)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def connect(self, answer=None, cloud="AzureCloud"):
        session = FakeSession(self.temp.name, answer, cloud)
        session.account = {"user": {"name": "analyst@example.com"}}
        self.app.session, self.app.workspace = session, dict(WORKSPACE)
        self.app.sync_connection()
        return session

    def run_now(self):
        """Run test query with App.work answered on this thread."""
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: done(task())) as work:
            self.page.run_test()
        self.root.update()
        return work

    def test_disabled_with_a_reason_until_a_public_cloud_workspace_is_chosen(self):
        page = self.page
        self.assertEqual(str(page.test_button.cget("state")), "disabled")
        self.assertIn("Sign in on Connect", page.test_hint.get())
        self.assertIn("Sign in on Connect", page.test_button.tooltip_text)
        session = self.connect()
        self.assertEqual(str(page.test_button.cget("state")), "normal")
        self.assertEqual(page.test_hint.get(), "Runs read-only against law-example-sentinel over the last hour and "
                                               "shows up to 50 rows. Nothing is saved or deployed.")
        session.cloud = GOV_CLOUD; self.app.sync_connection()
        self.assertEqual(str(page.test_button.cget("state")), "disabled")
        self.assertIn("Azure Government", page.test_hint.get())
        self.app.clear_selection(); self.root.update()
        self.assertEqual(str(page.test_button.cget("state")), "disabled")

    def test_the_hint_follows_look_back_and_its_one_day_cap(self):
        self.connect()
        self.page.vars["queryPeriod"].set("PT4H")
        self.assertIn("over the last 4 hours and", self.page.test_hint.get())
        self.page.vars["queryPeriod"].set("P5D")
        self.assertIn("over the last day (Look back is 5 days; tests stop at 1 day)", self.page.test_hint.get())
        self.page.vars["kind"].set("NRT")
        self.assertIn("over the last day (near real-time rules have no Look back)", self.page.test_hint.get())

    def test_shows_the_row_count_time_taken_and_first_rows(self):
        session = self.connect(response(12))
        self.page.vars["queryPeriod"].set("PT4H")
        work = self.run_now()
        self.assertEqual(work.call_args.kwargs["page"], "builder")
        self.assertEqual(work.call_args.kwargs["failed"], "Couldn't run the test query")
        self.assertEqual(session.bodies[0][1]["timespan"], "PT4H")
        page, tree = self.page, self.page.test_tree
        self.assertTrue(page.test_result.winfo_manager())
        self.assertEqual(page.test_pill.cget("text"), "●  12 rows")
        self.assertRegex(page.test_summary.get(), r"^From the last 4 hours in law-example-sentinel · took \d+\.\d s$")
        self.assertEqual([tree.heading(c, "text") for c in tree["columns"]], ["TimeGenerated", "UserPrincipalName", "Attempts"])
        self.assertEqual(len(tree.get_children()), 12)
        self.assertEqual(tree.full_values(tree.get_children()[3]), ("2026-11-09T10:03:00Z", "user3@example.com", "3"))
        self.assertIn("12 rows", self.app.status.get())

    def test_more_rows_than_shown_and_no_rows(self):
        self.connect(response(51)); self.run_now()
        self.assertEqual(self.page.test_pill.cget("text"), "●  50+ rows")
        self.assertTrue(self.page.test_summary.get().startswith("First 50 rows from the last hour"))
        self.assertEqual(len(self.page.test_tree.get_children()), 50)
        self.connect(response(0)); self.run_now()
        self.assertEqual(self.page.test_pill.cget("text"), "●  No rows")
        self.assertFalse(self.page.test_tree.get_children())
        self.assertEqual(len(self.page.test_tree["columns"]), 3)  # Its headings still show what came back.

    def test_errors_show_plain_advice_and_log_analytics_reason_in_place(self):
        self.connect(Stop(SYNTAX))
        with patch.object(ui.kit, "alert") as alert: self.run_now()
        alert.assert_not_called()  # A query problem is explained under the button, not in a dialog.
        page = self.page
        self.assertEqual(page.test_pill.cget("text"), "●  Didn't run")
        self.assertTrue(page.test_banner.winfo_manager())
        self.assertEqual(page.test_message.get(), advice.lookup("Test query syntax error") +
                         "\nLog Analytics: Query could not be parsed at 'wher' on line [2,2]")
        self.assertIsNone(page.test_tree)
        page.copy_test_details(); self.assertEqual(self.root.clipboard_get(), SYNTAX)
        self.connect(Stop(DENIED)); self.run_now()
        self.assertIn("can't run queries in law-example-sentinel", page.test_message.get())

    def test_local_problems_are_explained_without_asking_azure(self):
        session = self.connect()
        for query, words in (("", "The query is empty"), ("SigninLogs |", "ends with |")):
            with self.subTest(query=query):
                text = self.page.texts["query"]; text.delete("1.0", "end"); text.insert("1.0", query)
                work = self.run_now()
                work.assert_not_called()
                self.assertIn(words, self.page.test_message.get())
        self.assertEqual(session.calls, [])

    def test_shift_enter_in_the_query_box_runs_the_test(self):
        session = self.connect()
        text = self.page.texts["query"]
        before = text.get("1.0", "end-1c")
        with patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: done(task())):
            self.assertEqual(self.page.test_key(), "break")  # No new line is typed.
            self.root.deiconify(); self.app.show_page("builder"); self.root.update()
            text.focus_force(); self.root.update()  # Key events go to the focused widget.
            text.event_generate("<Shift-Return>"); self.root.update()
        self.assertEqual(text.get("1.0", "end-1c"), before)
        self.assertEqual(len(session.bodies), 2)

    def test_editing_marks_results_out_of_date_and_a_new_rule_clears_them(self):
        self.connect(response(2)); self.run_now()
        page, text = self.page, self.page.texts["query"]
        text.insert("end", "\n| where Attempts > 1"); self.root.update()
        self.assertEqual(page.test_pill.cget("text"), "●  Out of date")
        self.assertIn("changed after this test", page.test_summary.get())
        self.assertEqual(len(page.test_tree.get_children()), 2)  # Kept to compare with.
        text.delete("end-22c", "end-1c"); self.root.update()  # Undone: the results apply again.
        self.assertEqual(page.test_pill.cget("text"), "●  2 rows")
        page.vars["queryPeriod"].set("PT8H")
        self.assertEqual(page.test_pill.cget("text"), "●  Out of date")
        page.vars["queryPeriod"].set("PT1H"); self.assertEqual(page.test_pill.cget("text"), "●  2 rows")
        self.app.workspace = dict(WORKSPACE, workspace_id="66666666-6666-4666-8666-666666666666",
                                  workspace_name="law-other"); self.app.sync_connection()
        self.assertEqual(page.test_pill.cget("text"), "●  Out of date")  # Another workspace, same session.
        self.assertIn("workspace changed", page.test_summary.get())
        page.load_form(valid_form())
        self.assertFalse(page.test_result.winfo_manager()); self.assertIsNone(page.test_tree)

    def test_signing_out_removes_the_results(self):
        self.connect(response(2)); self.run_now()
        self.app.session = None; self.app.clear_selection(); self.root.update()
        self.assertFalse(self.page.test_result.winfo_manager())
        self.assertIsNone(self.page.test_tree)

    def test_runs_through_app_work_and_waits_while_busy(self):
        """The real App.work: the query runs on a worker thread while the window stays usable, the button
        waits while it runs, and nothing else can start meanwhile."""
        session = self.connect()
        release, started = threading.Event(), threading.Event()
        answer = session.az
        def slow(*args, timeout=None):
            if args[:2] != ("cloud", "show"): started.set(); release.wait(10)
            return answer(*args, timeout=timeout)
        session.az = slow
        self.page.run_test()
        self.assertTrue(self.app.busy)
        self.assertTrue(started.wait(10))
        deadline = time.monotonic() + 10  # The busy visuals follow after BUSY_DELAY_MS.
        while not self.app.busy_shown and time.monotonic() < deadline:
            self.root.update(); time.sleep(0.02)
        self.assertEqual(str(self.page.test_button.cget("state")), "disabled")
        self.assertEqual(str(self.page.texts["query"].cget("state")), "normal")  # Typing still works.
        self.page.run_test()  # Ignored: one task at a time.
        release.set()
        deadline = time.monotonic() + 10
        while self.app.busy and time.monotonic() < deadline:
            self.root.update(); time.sleep(0.02)
        self.assertFalse(self.app.busy)
        self.assertEqual(len(session.bodies), 1)
        self.assertEqual(self.page.test_pill.cget("text"), "●  3 rows")
        self.assertEqual(str(self.page.test_button.cget("state")), "normal")

    def test_mfa_challenge_goes_to_the_app_sign_in_recovery(self):
        self.connect(AuthenticationRequired(MFA))
        with patch.object(self.app, "recover_authentication") as recover:
            self.page.run_test()
            deadline = time.monotonic() + 10
            while self.app.busy and time.monotonic() < deadline:
                self.root.update(); time.sleep(0.02)
        recover.assert_called_once()
        self.assertFalse(self.page.test_result.winfo_manager())


if __name__ == "__main__":
    unittest.main()
