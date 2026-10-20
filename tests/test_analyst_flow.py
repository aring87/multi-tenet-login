"""Offline checks for the git-free analyst path: upload, edit, submit."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app import rule_drafts as drafts
from sentinel_app.repository_catalog import GitHubReader
from sentinel_app.repository_reviews import ReviewService, save_request
from sentinel_app.review_history import ReviewHistoryDialog, pull_status
from sentinel_app.rule_builder_page import locate
from sentinel_app.rule_review_dialog import ReviewDialog, friendly
from test_repository_reviews import FakeGitHub, REPO, NEW
from test_rule_drafts import valid_form


class FakeReader:
    """Stands in for the GitHub catalog read; no network."""
    rules = []

    def __init__(self, *args): pass

    def load(self, repository, progress=None):
        if progress: progress(1, 1)
        return dict(rules=self.rules, revision="a" * 40)


class AnalystFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        p = patch.object(ui, "DATA", self.data); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.builder = self.app.rule_builder
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def work(self, title, task, done, **kwargs):
        done(task())

    def upload(self, rule):
        path = self.data / "uploaded.yml"
        drafts.export_rule(path, drafts.from_rule(rule))
        with patch("sentinel_app.rule_builder_page.filedialog.askopenfilename", return_value=str(path)), \
             patch.object(self.app, "work", side_effect=self.work):
            self.builder.open_yaml()

    def test_uploaded_yaml_for_existing_rule_becomes_an_update(self):
        self.app.vars["github_owner"].set("example"); self.app.vars["github_repo"].set("detections")
        rule = drafts.to_rule(valid_form())
        FakeReader.rules = [dict(id=rule["id"].upper(), name="Library copy", path="rules/sentinel/library.yml")]
        with patch("sentinel_app.rule_builder_page.GitHubReader", FakeReader): self.upload(rule)
        self.assertEqual(self.builder.source, {"repository": "example/detections", "revision": "a" * 40,
                                               "path": "rules/sentinel/library.yml"})
        self.assertTrue(self.builder.source_uploaded)
        self.assertIn("Matched", self.builder.note.get())
        self.builder.new()
        self.assertIsNone(self.builder.source); self.assertFalse(self.builder.source_uploaded)

    def test_uploaded_yaml_without_match_or_repository_stays_a_new_rule(self):
        rule = drafts.to_rule(valid_form())
        with patch("sentinel_app.rule_builder_page.GitHubReader", side_effect=AssertionError("no repository set")):
            self.upload(rule)
        self.assertIsNone(self.builder.source)
        self.assertIn("set the rule library", self.builder.note.get())
        self.app.vars["github_owner"].set("example"); self.app.vars["github_repo"].set("detections")
        FakeReader.rules = [dict(id="different", name="Other", path="rules/sentinel/other.yml")]
        with patch("sentinel_app.rule_builder_page.GitHubReader", FakeReader): self.upload(rule)
        self.assertIsNone(self.builder.source)
        self.assertIn("no rule with this ID", self.builder.note.get())

    def test_submit_opens_review_that_checks_automatically(self):
        self.builder.load_form(valid_form())
        with patch("sentinel_app.rule_builder_page.ReviewDialog") as dialog: self.builder.review()
        self.assertEqual(dialog.call_args.kwargs, {"auto": True})
        with patch.object(ReviewDialog, "prepare") as prepare:
            ReviewDialog(self.builder, auto=True)
            self.root.after(120, lambda: None); self.root.update(); self.root.after(80); self.root.update()
            prepare.assert_called_once_with()
            ReviewDialog(self.builder); self.root.update()
            prepare.assert_called_once_with()

    def test_review_shows_plain_summary_coloured_changes_and_upload_warning(self):
        self.builder.load_form(valid_form()); self.builder.source_uploaded = True
        dialog = ReviewDialog(self.builder)
        dialog.service = ReviewService(FakeGitHub()); dialog.repository.set(REPO); dialog.path.set(NEW)
        with patch.object(self.app, "work", side_effect=self.work): dialog.prepare()
        shown = [label.cget("text") for label in dialog.summary.values]
        self.assertIn(REPO, shown); self.assertIn(NEW, shown)
        self.assertIn("Not assigned to any client yet", shown)
        self.assertTrue(dialog.text.tag_ranges("added"))
        self.assertIn("opened file", dialog.notice.get())
        self.assertEqual(str(dialog.submit_button.cget("state")), "normal")

    def test_review_errors_are_explained_and_kept_for_engineers(self):
        self.assertIn("Edit existing rule", friendly("This rule ID already exists at rules/sentinel/x.yml. Start a new draft."))
        self.assertIn("write access", friendly("This GitHub account needs write access to the detection repository."))
        self.assertEqual(friendly("Something unexpected"), "Something unexpected")
        self.builder.load_form(valid_form())
        dialog = ReviewDialog(self.builder); dialog.repository.set(REPO); dialog.path.set(NEW)
        remote = FakeGitHub(); remote.metadata["permissions"]["push"] = False
        dialog.service = ReviewService(remote)
        with patch.object(self.app, "work", side_effect=self.work), patch.object(self.app, "log") as log:
            dialog.prepare()
        self.assertIn("Ask a repository admin for write access", dialog.notice.get())
        self.assertTrue(any("Rule submission:" in call.args[0] for call in log.call_args_list))
        self.assertEqual(str(dialog.submit_button.cget("state")), "disabled")

    def test_missing_repository_asks_for_it_without_contacting_github(self):
        self.builder.load_form(valid_form())
        dialog = ReviewDialog(self.builder); dialog.repository.set("")
        with patch.object(self.app, "work") as work: dialog.prepare()
        work.assert_not_called()
        self.assertIn("Where it will be saved", dialog.notice.get())
        self.assertTrue(dialog.where_body.winfo_manager())

    def test_edit_existing_rule_opens_github_library_rules(self):
        page = self.app.catalog_page
        with patch.object(page, "load") as load: self.app.open_rule_library(); self.root.update()
        load.assert_not_called()
        self.assertIn("Set the rule library once on Connect", page.note.get())
        self.app.vars["github_owner"].set("example"); self.app.vars["github_repo"].set("detections")
        with patch.object(page, "load") as load: self.app.open_rule_library(); self.root.update()
        load.assert_called()
        self.assertEqual(self.app.current_page(), "rules"); self.assertEqual(self.app.rule_view.get(), "repository")
        self.assertEqual(page.mode.get(), "GitHub repository"); self.assertEqual(page.view.get(), "Rules")
        self.assertEqual(page.source.get(), "example/detections")

    def test_saved_repository_makes_library_default_to_github_and_auto_load(self):
        (self.data / "settings.json").write_text('{"github_owner": "example", "github_repo": "detections"}')
        root = tk.Tk(); root.withdraw(); app = ui.App(root)
        self.addCleanup(lambda: ([root.after_cancel(t) for t in root.tk.call("after", "info")], root.destroy()))
        page = app.catalog_page
        self.assertEqual(page.mode.get(), "GitHub repository"); self.assertEqual(page.source.get(), "example/detections")
        with patch.object(page, "load") as load:
            app.rule_view.set("repository"); load.assert_called_once_with()
            page.snapshot = {"loaded": True}; app.rule_view.set("workspace"); app.rule_view.set("repository")
            load.assert_called_once_with()

    def test_github_read_reports_progress_for_every_file(self):
        files = {"clients/a/a.yml": b"x: 1", "rules/sentinel/r.yml": b"y: 2"}
        def request(endpoint):
            if endpoint == "user": return {"login": "analyst"}
            if endpoint == "repos/example/detections": return {"default_branch": "main", "id": 1}
            if endpoint.endswith("/commits/main"): return {"sha": "c" * 40, "commit": {"tree": {"sha": "t"}}}
            if "/git/trees/" in endpoint:
                return {"tree": [{"path": "clients", "type": "tree"}, {"path": "rules/sentinel", "type": "tree"}] +
                        [{"path": p, "type": "blob", "mode": "100644", "sha": p, "size": len(v)} for p, v in files.items()]}
            import base64
            return {"encoding": "base64", "content": base64.b64encode(files[endpoint.split("/git/blobs/")[1]]).decode()}
        seen = []
        GitHubReader(request).load("example/detections", lambda done, total: seen.append((done, total)))
        self.assertEqual(sorted(seen), [(1, 2), (2, 2)])


class SyncThread:
    """Runs a background check immediately so its result can be asserted."""
    def __init__(self, target, daemon=None): self.target = target
    def start(self): self.target()


class SubmissionStatusTests(unittest.TestCase):
    URL = "https://github.com/example/detections/pull/7"

    def status(self, pull, reviews=()):
        calls = []
        def request(method, endpoint):
            calls.append((method, endpoint))
            return list(reviews) if endpoint.endswith("/reviews?per_page=100") else pull
        result = pull_status(self.URL, request)
        self.assertTrue(all(method == "GET" for method, _ in calls))
        return result

    def test_plain_language_states(self):
        review = lambda user, state: {"user": {"login": user}, "state": state}
        self.assertEqual(self.status({"merged": True, "state": "closed"}), "Merged")
        self.assertEqual(self.status({"merged": False, "state": "closed"}), "Closed without merging")
        self.assertIn("Ready for review", self.status({"state": "open", "draft": True}))
        self.assertEqual(self.status({"state": "open"}), "Waiting for a teammate's review")
        self.assertEqual(self.status({"state": "open"}, [review("a", "APPROVED")]), "Approved — ready to merge")
        self.assertEqual(self.status({"state": "open"}, [review("a", "APPROVED"), review("b", "CHANGES_REQUESTED")]),
                         "Changes requested")
        # A reviewer's later approval replaces their earlier change request.
        self.assertEqual(self.status({"state": "open"}, [review("a", "CHANGES_REQUESTED"), review("a", "APPROVED")]),
                         "Approved — ready to merge")
        with self.assertRaises(ValueError): pull_status("https://example.com/pull/1", lambda *a: {})


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        p = patch.object(ui, "DATA", self.data); p.start(); self.addCleanup(p.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.addCleanup(lambda: ([self.root.after_cancel(t) for t in self.root.tk.call("after", "info")], self.root.destroy()))


class SubmissionsDialogTests(AppTestCase):
    def test_list_shows_github_status_and_opens_pull_request(self):
        form = valid_form()
        record = ReviewService(FakeGitHub()).prepare(REPO, NEW, drafts.validated_yaml(form)[0])
        record["pull_request"] = "https://github.com/example/detections/pull/7"
        save_request(self.data / "review-requests", record)
        unfinished = ReviewService(FakeGitHub()).prepare(REPO, NEW, drafts.validated_yaml(valid_form())[0])
        save_request(self.data / "review-requests", unfinished)
        with patch("sentinel_app.review_history.pull_status", return_value="Approved — ready to merge") as status, \
             patch.object(self.app, "work", side_effect=lambda title, task, done, **kw: done(task())):
            dialog = ReviewHistoryDialog(self.app.rule_builder)
        status.assert_called_once_with(record["pull_request"])
        statuses = {row["request_id"]: row["status"] for row in dialog.rows}
        self.assertEqual(statuses[record["plan"]["request_id"]], "Approved — ready to merge")
        self.assertEqual(statuses[unfinished["plan"]["request_id"]], "Not finished — open to resume")
        item = next(i for i, row in dialog.visible.items() if row.get("pull_request"))
        self.assertEqual(dialog.table.item(item, "values")[2], "✓  Approved — ready to merge")
        dialog.table.selection_set(item); dialog.selection_changed()
        self.assertEqual(str(dialog.github_button.cget("state")), "normal")
        with patch("sentinel_app.review_history.webbrowser.open") as browser: dialog.open_github()
        browser.assert_called_once_with(record["pull_request"])


class BuilderGuidanceTests(AppTestCase):
    def check(self, **changes):
        form = valid_form(); form.update(changes)
        builder = self.app.rule_builder; builder.load_form(form)
        with patch("sentinel_app.rule_builder_page.messagebox.showerror") as dialog:
            self.assertIsNone(builder.validate())
        dialog.assert_not_called()
        return builder

    def test_failed_check_opens_the_step_and_marks_the_field(self):
        builder = self.check(description="Too short")
        self.assertEqual(builder.steps.index("current"), 0); self.assertEqual(builder.flagged, "description")
        self.assertIn("Description (step 1, Basics)", builder.note.get())
        self.assertTrue(builder.widgets["description"].master.error)
        builder = self.check(queryFrequency="PT1M")
        self.assertEqual(builder.steps.index("current"), 1); self.assertEqual(builder.flagged, "queryFrequency")
        self.assertEqual(str(builder.widgets["queryFrequency"].cget("style")), "Error.TSpinbox")
        self.assertIn("5 minutes", builder.note.get())
        builder.vars["queryFrequency"].set("PT1H")
        self.assertIsNone(builder.flagged)
        self.assertEqual(str(builder.widgets["queryFrequency"].cget("style")), "TSpinbox")
        builder = self.check(queryFrequency="PT1H", queryPeriod="PT5M")
        self.assertEqual(builder.flagged, "queryPeriod")
        builder = self.check(subTechniques="T1562.004", techniques="T1003")
        self.assertEqual(builder.steps.index("current"), 2); self.assertEqual(builder.flagged, "techniques")
        self.assertEqual(locate(valid_form(), "Something new"), (None, "Something new"))

    def test_github_signin_is_checked_when_the_builder_opens(self):
        builder = self.app.rule_builder
        with patch.object(builder, "check_github") as check:
            self.app.show_page("builder"); self.root.update()
        check.assert_called_once_with()
        builder.check_github()
        self.assertTrue(builder.github_bar.winfo_manager())
        self.assertEqual(builder.github_action.cget("text"), "Go to Connect")
        self.app.vars["github_owner"].set("example"); self.app.vars["github_repo"].set("detections")
        with patch("sentinel_app.rule_builder_page.threading.Thread", SyncThread):
            builder.github_probe = lambda: (_ for _ in ()).throw(RuntimeError("GitHub read failed. Run gh auth login"))
            builder.check_github(force=True); self.app.poll()
            self.assertEqual(builder.github_action.cget("text"), "Sign in to GitHub")
            builder.github_probe = lambda: (_ for _ in ()).throw(RuntimeError("GitHub CLI is not installed."))
            builder.check_github(force=True); self.app.poll()
            self.assertIn("isn't installed", builder.github_message.get())
            builder.github_probe = lambda: "analyst"
            builder.check_github(force=True); self.app.poll()
        self.assertFalse(builder.github_bar.winfo_manager())
        self.assertEqual(builder.github_label.cget("text"), "Signed in to GitHub as analyst")
        with patch.object(builder, "github_probe", side_effect=AssertionError("cached")): builder.check_github()


if __name__ == "__main__":
    unittest.main()
