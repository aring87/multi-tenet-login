"""Synthetic catalog fixtures only; no Azure/GitHub writes or live calls."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repository_catalog import CatalogError, GitHubReader, catalog, load_local, parse_yaml


def fixtures():
    rule = dict(id="11111111-1111-4111-8111-111111111111", name="Example sign in", severity="Medium", enabled=True)
    target = dict(version=1, target="example-primary", client="example", enabled=True,
                  azure=dict(workspace_name="example-law"),
                  rules=[dict(path="rules/sentinel/example.yml", overrides=dict(enabled=False))])
    return {"clients/example/workspace.yml": json.dumps(target).encode(),
            "rules/sentinel/example.yml": json.dumps(rule).encode()}


class CatalogTests(unittest.TestCase):
    def test_all_workspace_manifests_and_alphabetical_order(self):
        files = fixtures()
        extra = json.loads(files["clients/example/workspace.yml"])
        extra.update(target="alpha-secondary", client="alpha", enabled=False)
        files["clients/alpha/workspace-secondary.yaml"] = json.dumps(extra).encode()
        result = catalog(files, "test", "revision")
        self.assertEqual([r["name"] for r in result["clients"]], ["alpha", "example"])
        self.assertEqual(result["clients"][0]["state"], "Disabled")
        self.assertFalse(result["issues"])

    def test_malformed_files_are_reported_without_hiding_valid_records(self):
        files = fixtures(); files["clients/broken/workspace.yml"] = b"enabled: ["
        result = catalog(files, "test", "revision")
        self.assertEqual(len(result["clients"]), 1)
        self.assertIn("clients/broken/workspace.yml", result["issues"][0])

    def test_duplicate_target_and_missing_rule_are_reported(self):
        files = fixtures(); files["clients/example/workspace-two.yml"] = files["clients/example/workspace.yml"]
        del files["rules/sentinel/example.yml"]
        result = catalog(files, "test", "revision")
        self.assertTrue(any("Duplicate target" in e for e in result["issues"]))
        self.assertTrue(any("unavailable" in e for e in result["issues"]))

    def test_unsafe_yaml_aliases_and_duplicates_are_rejected(self):
        for raw in (b"!!python/object/apply:os.system ['echo no']", b"a: 1\na: 2", b"a: &a [*a]"):
            with self.subTest(raw=raw), self.assertRaises(CatalogError): parse_yaml(raw)

    def test_traversal_assignment_and_unknown_schema_are_reported(self):
        for change in ({"version": 2}, {"rules": [{"path": "rules/sentinel/../../secret.yml"}]}):
            files = fixtures(); data = json.loads(files["clients/example/workspace.yml"]); data.update(change)
            files["clients/example/workspace.yml"] = json.dumps(data).encode()
            result = catalog(files, "test", "revision")
            self.assertFalse(result["clients"]); self.assertTrue(result["issues"])

    def test_local_reader_does_not_execute_scripts_or_require_git(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, raw in fixtures().items():
                file = root / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(raw)
            (root / "pipeline.py").write_text("raise RuntimeError('must not execute')")
            with patch("subprocess.run", side_effect=AssertionError("must not execute")):
                result = load_local(root)
            self.assertEqual(len(result["clients"]), 1)
            self.assertIn("unpublished", result["revision"])

    def test_wrong_folder_fails_instead_of_returning_empty_success(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(CatalogError): load_local(folder)

    def test_github_reads_pinned_blobs_and_identifies_user(self):
        calls = []
        files = fixtures()
        def request(endpoint):
            calls.append(endpoint)
            if endpoint == "user": return {"login": "synthetic-user"}
            if endpoint == "repos/example/detections": return {"default_branch": "main"}
            if endpoint.endswith("/commits/main"): return {"sha": "commit-one", "commit": {"tree": {"sha": "tree-one"}}}
            if "/git/trees/" in endpoint:
                return {"truncated": False, "tree": [
                    {"path": "clients", "type": "tree"}, {"path": "rules/sentinel", "type": "tree"},
                    *[dict(path=p, type="blob", mode="100644", size=len(raw), sha=str(i)) for i, (p, raw) in enumerate(files.items())]]}
            index = int(endpoint.rsplit("/", 1)[1])
            return {"encoding": "base64", "content": base64.b64encode(list(files.values())[index]).decode()}
        result = GitHubReader(request).load("example/detections")
        self.assertEqual(result["revision"], "commit-one")
        self.assertEqual(result["identity"], "synthetic-user")
        self.assertIn("repos/example/detections/git/trees/tree-one?recursive=1", calls)
        self.assertFalse(result["issues"])

    def test_truncated_remote_tree_never_looks_complete(self):
        def request(endpoint):
            if endpoint == "user": return {"login": "example"}
            if endpoint.endswith("detections"): return {"default_branch": "main"}
            if "/commits/" in endpoint: return {"sha": "a", "commit": {"tree": {"sha": "b"}}}
            return {"truncated": True}
        with self.assertRaisesRegex(CatalogError, "incomplete"):
            GitHubReader(request).load("example/detections")


if __name__ == "__main__": unittest.main()
