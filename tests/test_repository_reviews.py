import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentinel_app.repository_reviews import ReviewService, ReviewError, load_request, diff
from sentinel_app.repository_catalog import CatalogError
from sentinel_app import rule_drafts as drafts
from test_rule_drafts import valid_form
from test_repository_catalog import fixtures

REPO = "example/detections"
EXISTING = "rules/sentinel/example.yml"
NEW = "rules/sentinel/new-rule.yml"


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.files = fixtures()
        self.original = valid_form()
        self.files[EXISTING] = drafts.validated_yaml(self.original)[0].encode()
        self.metadata = {"id": 42, "private": True, "default_branch": "main", "permissions": {"push": True}}
        self.identity = "test-analyst"
        self.head = "a" * 40
        self.refs, self.pulls = [], []
        self.fail_after = None
        self.truncated = False
        self.extra_entries = []
        self.saved_tree = "c" * 40

    def __call__(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, copy.deepcopy(body)))
        if method == "GET":
            if endpoint == "user": return {"login": self.identity}
            if endpoint == "repos/" + REPO: return copy.deepcopy(self.metadata)
            if "/git/commits/" in endpoint:
                if endpoint.endswith("a" * 40): return {"tree": {"sha": "b" * 40}, "parents": []}
                return {"tree": {"sha": self.saved_tree}, "parents": [{"sha": "a" * 40}]}
            if "/commits/" in endpoint: return {"sha": self.head, "commit": {"tree": {"sha": "b" * 40}}}
            if "/git/trees/" in endpoint:
                return {"truncated": self.truncated, "tree": [
                    *[dict(path=p, mode="040000", type="tree") for p in ("clients", "clients/example", "rules", "rules/sentinel")],
                    *[dict(path=p, mode="100644", type="blob", sha=str(i), size=len(raw)) for i, (p, raw) in enumerate(self.files.items())],
                    *self.extra_entries]}
            if "/git/blobs/" in endpoint:
                raw = list(self.files.values())[int(endpoint.rsplit("/", 1)[1])]
                return {"encoding": "base64", "content": base64.b64encode(raw).decode()}
            if "/git/matching-refs/" in endpoint: return copy.deepcopy(self.refs)
            if "/pulls?" in endpoint: return copy.deepcopy(self.pulls)
        if method == "POST":
            if endpoint.endswith("/git/trees"):
                result = {"sha": "c" * 40}
            elif endpoint.endswith("/git/commits"):
                result = {"sha": "d" * 40}
            elif endpoint.endswith("/git/refs"):
                self.refs.append({"ref": body["ref"], "object": {"sha": body["sha"]}})
                result = copy.deepcopy(self.refs[-1])
            elif endpoint.endswith("/pulls"):
                result = {"html_url": "https://github.com/" + REPO + "/pull/12", "state": "open",
                          "head": {"sha": "d" * 40, "ref": body["head"], "repo": {"id": 42}},
                          "base": {"ref": body["base"]}}
                self.pulls.append(copy.deepcopy(result))
            else: raise AssertionError(endpoint)
            if self.fail_after and endpoint.endswith(self.fail_after):
                self.fail_after = None
                raise ReviewError("Simulated response lost after GitHub accepted the write")
            return result
        raise AssertionError((method, endpoint))

    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.remote = FakeGitHub(); self.service = ReviewService(self.remote)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def new(self):
        return self.service.prepare(REPO, NEW, drafts.validated_yaml(valid_form())[0])

    def edit(self, form=None):
        form = form or copy.deepcopy(self.remote.original)
        form["name"] = "Updated synthetic detection"
        return self.service.prepare(REPO, EXISTING, drafts.validated_yaml(form)[0], form["allow_missing_mitre"],
            {"repository": REPO, "revision": self.remote.head, "path": EXISTING})

    def test_preparation_reads_only_and_reports_diff(self):
        record = self.new()
        self.assertEqual(self.remote.writes(), [])
        self.assertIn("+name: Synthetic test detection", diff(record["plan"]))
        self.assertEqual(record["plan"]["identity"], "test-analyst")
        self.assertEqual(record["plan"]["impact"], [])

    def test_existing_references_preserve_per_target_override(self):
        record = self.edit()
        self.assertEqual(record["plan"]["impact"], [{"target": "example-primary", "target_enabled": True,
                          "rule_enabled": False, "overrides": {"enabled": False}}])

    def test_submit_changes_one_rule_on_new_branch_and_creates_draft(self):
        record = self.new(); url = self.service.submit(record, self.folder)
        writes = self.remote.writes()
        self.assertEqual(len(writes), 4)
        tree, commit, ref, pull = [call[2] for call in writes]
        self.assertEqual(tree["base_tree"], "b" * 40)
        self.assertEqual([entry["path"] for entry in tree["tree"]], [NEW])
        self.assertEqual(commit["parents"], ["a" * 40])
        self.assertTrue(ref["ref"].startswith("refs/heads/codex/rule-"))
        self.assertTrue(pull["draft"]); self.assertEqual(pull["base"], "main")
        self.assertEqual(url, "https://github.com/example/detections/pull/12")
        saved = load_request(next(self.folder.glob("*.review.json")))
        self.assertEqual(saved["pull_request"], url)
        self.assertEqual(self.remote.head, "a" * 40)

    def test_stale_base_prevents_all_writes(self):
        record = self.new(); self.remote.head = "e" * 40
        with self.assertRaisesRegex(ReviewError, "changed after preview"): self.service.submit(record, self.folder)
        self.assertFalse(self.remote.writes())

    def test_identity_permission_or_visibility_change_prevents_writes(self):
        for change in ("identity", "private", "id", "push", "branch"):
            with self.subTest(change=change):
                self.remote = FakeGitHub(); self.service = ReviewService(self.remote); record = self.new()
                if change == "identity": self.remote.identity = "another-user"
                if change == "private": self.remote.metadata["private"] = False
                if change == "id": self.remote.metadata["id"] = 99
                if change == "push": self.remote.metadata["permissions"]["push"] = False
                if change == "branch": self.remote.metadata["default_branch"] = "trunk"
                with self.assertRaises(ReviewError): self.service.submit(record, self.folder)
                self.assertFalse(self.remote.writes())

    def test_existing_file_requires_fresh_source_and_same_id(self):
        for source in (None, {"repository": REPO, "revision": "old", "path": EXISTING}):
            with self.assertRaises(ReviewError):
                self.service.prepare(REPO, EXISTING, drafts.validated_yaml(valid_form())[0], source=source)
        with self.assertRaisesRegex(ReviewError, "preserve its rule ID"): self.edit(valid_form())
        self.assertFalse(self.remote.writes())

    def test_duplicate_id_at_another_path_is_refused(self):
        content = drafts.validated_yaml(self.remote.original)[0]
        with self.assertRaisesRegex(ReviewError, "already exists"): self.service.prepare(REPO, NEW, content)

    def test_public_repository_and_incomplete_catalog_are_refused(self):
        self.remote.metadata["private"] = False
        with self.assertRaisesRegex(ReviewError, "private detection"): self.new()
        self.remote.metadata["private"] = True; self.remote.files["clients/broken/workspace.yml"] = b"version: 2"
        with self.assertRaisesRegex(ReviewError, "incomplete"): self.new()
        self.remote.truncated = True
        with self.assertRaises(CatalogError): self.new()
        self.assertFalse(self.remote.writes())

    def test_traversal_workflows_and_linked_parent_are_refused(self):
        for path in (".github/workflows/ci.yml", "rules/sentinel/../other.yml", "rules/sentinel//bad.yml", "rules/sentinel/a b.yml", "rules/sentinel/upper.YML"):
            with self.assertRaises(ReviewError): self.service.prepare(REPO, path, drafts.validated_yaml(valid_form())[0])
        self.remote.extra_entries.append(dict(path="rules/sentinel/linked", type="blob", mode="120000", sha="linked"))
        with self.assertRaisesRegex(ReviewError, "non-directory"):
            self.service.prepare(REPO, "rules/sentinel/linked/rule.yml", drafts.validated_yaml(valid_form())[0])

    def test_target_migration_exception_cannot_be_overridden_by_editor(self):
        form = copy.deepcopy(self.remote.original)
        form.update(tactics="", techniques="", subTechniques="", allow_missing_mitre=True)
        with self.assertRaisesRegex(ValueError, "Missing MITRE"): self.edit(form)
        target = json.loads(self.remote.files["clients/example/workspace.yml"])
        target["allow_missing_mitre"] = True
        self.remote.files["clients/example/workspace.yml"] = json.dumps(target).encode()
        self.assertTrue(self.edit(form)["plan"]["warnings"])

    def test_invalid_effective_override_blocks_review(self):
        target = json.loads(self.remote.files["clients/example/workspace.yml"])
        target["rules"][0]["overrides"]["queryFrequency"] = "PT1M"
        self.remote.files["clients/example/workspace.yml"] = json.dumps(target).encode()
        with self.assertRaises(ValueError): self.edit()

    def test_lost_branch_response_recovers_same_branch(self):
        record = self.new(); self.remote.fail_after = "/git/refs"
        with self.assertRaises(ReviewError): self.service.submit(record, self.folder)
        recovered = load_request(next(self.folder.glob("*.review.json")))
        self.service.submit(recovered, self.folder)
        self.assertEqual(sum(call[1].endswith("/git/refs") for call in self.remote.writes()), 1)
        self.assertEqual(len(self.remote.pulls), 1)

    def test_lost_pr_response_recovers_without_duplicate(self):
        record = self.new(); self.remote.fail_after = "/pulls"
        with self.assertRaises(ReviewError): self.service.submit(record, self.folder)
        recovered = load_request(next(self.folder.glob("*.review.json")))
        writes = len(self.remote.writes())
        self.service.submit(recovered, self.folder)
        self.assertEqual(len(self.remote.writes()), writes + 1)  # only deterministic tree verification
        self.assertTrue(self.remote.writes()[-1][1].endswith("/git/trees"))
        self.assertEqual(len(self.remote.pulls), 1)

    def test_modified_review_branch_is_never_overwritten(self):
        record = self.new(); self.service.submit(record, self.folder)
        self.remote.refs[0]["object"]["sha"] = "changed"
        writes = len(self.remote.writes())
        with self.assertRaisesRegex(ReviewError, "changed outside"): self.service.submit(record, self.folder)
        self.assertEqual(len(self.remote.writes()), writes)

    def test_request_changes_are_rejected(self):
        record = self.new(); record["plan"]["path"] = "rules/sentinel/another.yml"
        with self.assertRaisesRegex(ReviewError, "changed"): self.service.submit(record, self.folder)
        self.assertFalse(self.remote.writes())

    def test_failed_local_journal_prevents_remote_writes(self):
        record = self.new()
        with patch("sentinel_app.repository_reviews.save_request", side_effect=OSError("read only")):
            with self.assertRaises(OSError): self.service.submit(record, self.folder)
        self.assertFalse(self.remote.writes())

    def test_closed_existing_pr_is_returned_not_reopened(self):
        record = self.new(); url = self.service.submit(record, self.folder)
        self.remote.pulls[0]["state"] = "closed"
        writes = len(self.remote.writes())
        self.assertEqual(self.service.submit(record, self.folder), url)
        self.assertEqual(len(self.remote.writes()), writes + 1)
        self.assertEqual(len(self.remote.pulls), 1)

    def test_saved_commit_with_other_changes_cannot_be_published(self):
        record = self.new(); record["commit_sha"] = "d" * 40
        self.remote.saved_tree = "f" * 40
        with self.assertRaisesRegex(ReviewError, "outside the reviewed diff"):
            self.service.submit(record, self.folder)
        self.assertFalse(self.remote.refs); self.assertFalse(self.remote.pulls)
