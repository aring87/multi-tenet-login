"""Client dropdown updates in the onboarding pull request: no Azure or GitHub calls."""
import base64
import json
import tempfile
import unittest
from pathlib import Path

import lighthouse_onboarding as lh

BASE = Path(__file__).resolve().parents[1]
# A real copy of detection-as-code's multi-workspace workflow, eight blocks. This repo's
# .gitattributes normalises *.yml to LF, so the CRLF form that detection-as-code actually
# serves is built explicitly here rather than relying on how Git stored the fixture.
_FIXTURE_TEXT = (BASE / "tests" / "fixtures" / "sentinel-multi-workspace.yml").open(
    "r", encoding="utf-8", newline="").read().replace("\r\n", "\n")
FIXTURE_LF = _FIXTURE_TEXT
FIXTURE = _FIXTURE_TEXT.replace("\n", "\r\n")

SINGLE = ("on:\n  workflow_dispatch:\n    inputs:\n      target:\n"
          "        type: choice\n"
          "        # BEGIN GENERATED TARGET OPTIONS\n"
          "        options:\n"
          '          - "Select a client"\n'
          '          - "All enabled clients"\n'
          '          - "bravo-workspace"\n'
          '          - "delta-workspace"\n'
          "        # END GENERATED TARGET OPTIONS\n"
          "      mode:\n        type: choice\n")


def options(text):
    """Every generated block's option list, in order."""
    body = text.replace("\r\n", "\n")
    blocks = []
    for chunk in body.split(lh.DROPDOWN_BEGIN)[1:]:
        inner = chunk.split(lh.DROPDOWN_END)[0][len(lh.DROPDOWN_HEADER):]
        blocks.append([json.loads(line[len(lh.DROPDOWN_ITEM):])
                       for line in inner.split("\n") if line])
    return blocks


class AddDropdownTargetTests(unittest.TestCase):
    def test_inserts_in_sorted_position_below_fixed_entries(self):
        for target, expected in (("alpha-workspace", 0), ("charlie-workspace", 1),
                                 ("echo-workspace", 2)):
            with self.subTest(target=target):
                block = options(lh.add_dropdown_target(SINGLE, target))[0]
                self.assertEqual(block[:2], ["Select a client", "All enabled clients"])
                self.assertEqual(block[2:].index(target), expected)
                self.assertEqual(block[2:], sorted(block[2:], key=lh.dropdown_sort_key))

    def test_existing_target_is_a_no_op(self):
        self.assertIsNone(lh.add_dropdown_target(SINGLE, "bravo-workspace"))

    def test_surrounding_content_is_untouched(self):
        updated = lh.add_dropdown_target(SINGLE, "charlie-workspace")
        self.assertTrue(updated.startswith(SINGLE.split(lh.DROPDOWN_BEGIN)[0]))
        self.assertTrue(updated.endswith(SINGLE.split(lh.DROPDOWN_END)[1]))

    def test_real_workflow_every_block_updated_and_crlf_preserved(self):
        updated = lh.add_dropdown_target(FIXTURE, "new-client-workspace")
        self.assertNotIn("\n", updated.replace("\r\n", ""), "a bare LF was introduced")
        before, after = options(FIXTURE), options(updated)
        self.assertEqual(len(after), 8)
        for old, new in zip(before, after):
            self.assertEqual(new, sorted_insert(old, "new-client-workspace"))
        # Only slot one offers All enabled clients; the insert must not change that.
        self.assertIn("All enabled clients", after[0])
        self.assertTrue(all("All enabled clients" not in block for block in after[1:]))

    def test_lf_input_stays_lf(self):
        updated = lh.add_dropdown_target(FIXTURE_LF, "new-client-workspace")
        self.assertNotIn("\r", updated)
        self.assertEqual(updated.count("\n") - FIXTURE_LF.count("\n"), 8)

    def test_real_workflow_line_count_grows_by_one_per_block(self):
        updated = lh.add_dropdown_target(FIXTURE, "new-client-workspace")
        self.assertEqual(updated.count("\r\n") - FIXTURE.count("\r\n"), 8)

    def test_unrecognised_structure_is_refused_not_guessed(self):
        broken = (SINGLE.replace('          - "delta-workspace"\n', "          delta\n"),
                  SINGLE.replace("        options:\n", ""),
                  SINGLE.replace(lh.DROPDOWN_END, ""),
                  "no markers here\n",
                  SINGLE.replace('          - "Select a client"\n', "")
                        .replace('          - "bravo-workspace"\n',
                                 '          - "bravo-workspace"\n          - "Select a client"\n'))
        for text in broken:
            with self.subTest(text=text[:60]), self.assertRaises(lh.Stop):
                lh.add_dropdown_target(text, "charlie-workspace")


def sorted_insert(values, target):
    fixed = [v for v in values if v in lh.DROPDOWN_FIXED]
    names = [v for v in values if v not in lh.DROPDOWN_FIXED] + [target]
    return fixed + sorted(names, key=lh.dropdown_sort_key)


class FakeGitHub:
    """Serves workflow files on the onboarding branch and records writes."""
    def __init__(self, files, refuse_workflow_writes=False):
        self.files = dict(files)
        self.refuse = refuse_workflow_writes
        self.calls = []
        self.apply = True

    def gh(self, method, path, body=None, missing=False):
        self.calls.append((method, path, body))
        name = path.split("/contents/.github/workflows/")[-1].split("?")[0]
        if method == "GET":
            if name not in self.files:
                return None
            text = self.files[name]
            return {"sha": "sha-" + name, "content": base64.b64encode(text.encode()).decode()}
        if method == "PUT":
            if self.refuse:
                raise lh.Stop("refusing to allow an OAuth App to create or update workflow "
                              "`.github/workflows/" + name + "` without `workflow` scope")
            self.files[name] = base64.b64decode(body["content"]).decode()
            return {}
        raise AssertionError("unexpected call " + method + " " + path)


class UpdateDropdownsTests(unittest.TestCase):
    def config(self, rule="rules/sentinel/testing/pipeline-connection-test.yml"):
        config = json.loads((BASE / "lighthouse-onboarding.example.json").read_text(encoding="utf-8"))
        config.update(client="new-client", workspace_label="workspace", initial_rule_path=rule)
        return config

    def onboard(self, github, **kw):
        folder = tempfile.mkdtemp()
        run = lh.Onboard(self.config(**kw), github, Path(folder) / "state.json",
                         BASE / "lighthouse-onboard.json")
        run.branch = "onboard/" + run.c["target"]
        return run

    def files(self):
        return {"sentinel.yml": SINGLE, "sentinel-multi-workspace.yml": FIXTURE}

    def test_both_workflows_updated_on_the_onboarding_branch(self):
        github = FakeGitHub(self.files())
        self.assertEqual(self.onboard(github).update_dropdowns(), [])
        puts = [c for c in github.calls if c[0] == "PUT"]
        self.assertEqual(len(puts), 2)
        for _, path, body in puts:
            self.assertEqual(body["branch"], "onboard/new-client-workspace")
            self.assertTrue(body["sha"].startswith("sha-"), "update must pass the blob sha")
            self.assertTrue(path.endswith(tuple(lh.DROPDOWN_WORKFLOWS)))
        for text in github.files.values():
            self.assertTrue(all("new-client-workspace" in block for block in options(text)))

    def test_reads_come_from_the_branch_not_main(self):
        github = FakeGitHub(self.files())
        self.onboard(github).update_dropdowns()
        for method, path, _ in github.calls:
            if method == "GET":
                self.assertIn("?ref=onboard%2Fnew-client-workspace", path)

    def test_rerun_is_idempotent(self):
        github = FakeGitHub(self.files())
        run = self.onboard(github)
        run.update_dropdowns()
        github.calls.clear()
        self.assertEqual(run.update_dropdowns(), [])
        self.assertFalse([c for c in github.calls if c[0] == "PUT"])

    def test_target_without_initial_rule_is_skipped_without_github_calls(self):
        github = FakeGitHub(self.files())
        problems = self.onboard(github, rule="").update_dropdowns()
        self.assertEqual(len(problems), 1)
        self.assertIn("committed disabled", problems[0])
        self.assertEqual(github.calls, [])

    def test_missing_workflow_scope_is_reported_not_fatal(self):
        github = FakeGitHub(self.files(), refuse_workflow_writes=True)
        problems = self.onboard(github).update_dropdowns()
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertIn("gh auth refresh -s workflow", problem)

    def test_missing_workflow_file_is_reported(self):
        github = FakeGitHub({"sentinel.yml": SINGLE})
        problems = self.onboard(github).update_dropdowns()
        self.assertEqual(problems, ["sentinel-multi-workspace.yml: not found on the onboarding branch"])
        self.assertIn("new-client-workspace", github.files["sentinel.yml"])


class FullPullRequestTests(UpdateDropdownsTests):
    """create_target_pr end to end: branch, target file, dropdowns, pull request."""
    def github(self, refuse=False):
        github = FakeGitHub(self.files(), refuse_workflow_writes=refuse)
        inner = github.gh

        def gh(method, path, body=None, missing=False):
            if "/git/ref/heads/main" in path:
                github.calls.append((method, path, body)); return {"object": {"sha": "main-sha"}}
            if "/git/ref" in path:
                github.calls.append((method, path, body))
                return None if method == "GET" else {}
            if "/contents/clients/" in path:
                github.calls.append((method, path, body))
                return None if method == "GET" else {}
            if path.endswith("/pulls"):
                github.calls.append((method, path, body))
                github.pr_body = body["body"]
                return {"html_url": "https://github.com/o/r/pull/1"}
            return inner(method, path, body, missing)
        github.gh = gh
        github.pages = lambda path, key=None: []
        return github

    def run_pr(self, github):
        run = self.onboard(github)
        run.target_exists = False
        run.target_path = "clients/new-client/workspace.yml"
        run.workspace = {"customerId": "00000000-0000-0000-0000-000000000001"}
        run.create_target_pr()
        return run

    def test_pr_carries_target_and_dropdowns_together(self):
        github = self.github()
        run = self.run_pr(github)
        puts = [c[1] for c in github.calls if c[0] == "PUT"]
        self.assertTrue(any("/contents/clients/" in p for p in puts))
        self.assertEqual(sum(p.endswith(tuple(lh.DROPDOWN_WORKFLOWS)) for p in puts), 2)
        self.assertIn("are updated in this PR", github.pr_body)
        self.assertEqual(run.state["pull_request"], "https://github.com/o/r/pull/1")

    def test_dropdown_failure_still_opens_the_pr_and_says_why(self):
        github = self.github(refuse=True)
        run = self.run_pr(github)
        self.assertEqual(run.state["pull_request"], "https://github.com/o/r/pull/1")
        self.assertIn("were NOT updated", github.pr_body)
        self.assertIn("gh auth refresh -s workflow", github.pr_body)
        self.assertIn("sync_target_choices.py", github.pr_body)


if __name__ == "__main__":
    unittest.main()
