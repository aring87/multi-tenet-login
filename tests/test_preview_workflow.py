import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sentinel_app.repository_catalog import GitHubReader, parse_yaml, CatalogError
from sentinel_app.repository_reviews import ReviewError
from sentinel_app.preview_workflow import PreviewService, WORKFLOW_PATH, input_batches, eligible_clients, api
from test_repository_reviews import FakeGitHub, REPO, EXISTING


def workflow(targets=None):
    inputs = dict(mode=dict(type="choice", options=["preview", "deploy"]), rule_path=dict(type="string"))
    if targets is None: inputs["targets"] = dict(type="string")
    else:
        for key in ("target", "target_2"):
            inputs[key] = dict(type="choice", options=["Select a client", "All enabled clients", *targets])
    return {"on": {"workflow_dispatch": {"inputs": inputs}}}


class PreviewGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.files[WORKFLOW_PATH] = json.dumps(workflow()).encode()
        self.workflow = dict(id=77, path=WORKFLOW_PATH, state="active")
        self.dispatched = []; self.lose_response = False; self.run_sha = self.head

    def __call__(self, method, endpoint, body=None):
        if "/actions/" not in endpoint: return super().__call__(method, endpoint, body)
        self.calls.append((method, endpoint, copy.deepcopy(body)))
        if method == "GET" and "/workflows/" in endpoint: return copy.deepcopy(self.workflow)
        if method == "GET" and "/runs/" in endpoint:
            return dict(id=int(endpoint.rsplit("/", 1)[1]), workflow_id=77, event="workflow_dispatch",
                        head_branch="main", head_sha=self.run_sha, repository={"id": 42}, status="completed", conclusion="success")
        if method == "POST" and endpoint.endswith("/dispatches"):
            self.dispatched.append(copy.deepcopy(body))
            if self.lose_response: raise ReviewError("Lost dispatch response")
            return {"workflow_run_id": 100 + len(self.dispatched)}
        raise AssertionError((method, endpoint))

    def clients(self, count):
        template = json.loads(self.files["clients/example/workspace.yml"])
        targets = ["example-primary"]
        for i in range(count - 1):
            item = copy.deepcopy(template); item.update(client="client-" + str(i), target="client-" + str(i) + "-primary")
            self.files["clients/" + item["client"] + "/workspace.yml"] = json.dumps(item).encode()
            targets.append(item["target"])
        return targets


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.remote = PreviewGitHub(); self.service = PreviewService(self.remote)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.folder = Path(self.temp.name)

    def prepare(self, targets=None):
        return self.service.prepare(REPO, self.remote.head, EXISTING, targets or ["example-primary"])

    def test_unquoted_on_parses_without_changing_catalog_yaml_behavior(self):
        value = parse_yaml(b"on:\n  workflow_dispatch:\n    inputs: {}\nenabled: true\n", yaml12=True)
        self.assertIn("on", value); self.assertIs(value["enabled"], True)
        self.assertIs(parse_yaml(b"enabled: yes")["enabled"], True)
        for raw in (b"on: {}\non: {}", b"on: &a {}\nother: *a"):
            with self.assertRaises(CatalogError): parse_yaml(raw, yaml12=True)

    def test_prepare_only_reads_and_names_exact_clients(self):
        record = self.prepare()
        self.assertFalse(self.remote.writes())
        self.assertEqual(record["plan"]["targets"], ["example-primary"])
        self.assertEqual(record["plan"]["batches"], [{"mode": "preview", "rule_path": EXISTING, "targets": "example-primary"}])

    def test_dropdown_batches_never_use_all_clients_sentinel(self):
        targets = self.remote.clients(5)
        self.remote.files[WORKFLOW_PATH] = json.dumps(workflow(targets)).encode()
        record = self.prepare(targets)
        batches = record["plan"]["batches"]
        self.assertEqual(len(batches), 3)
        self.assertNotIn("All enabled clients", json.dumps(batches))
        actual = [v for b in batches for k, v in b.items() if k.startswith("target") and v != "Select a client"]
        self.assertEqual(sorted(actual), sorted(targets))

    def test_disabled_and_unassigned_targets_are_rejected(self):
        for field, value in (("enabled", False), ("rules", [])):
            remote = PreviewGitHub(); client = json.loads(remote.files["clients/example/workspace.yml"])
            client[field] = value; remote.files["clients/example/workspace.yml"] = json.dumps(client).encode()
            with self.assertRaisesRegex(ReviewError, "enabled and already assigned"):
                PreviewService(remote).prepare(REPO, remote.head, EXISTING, ["example-primary"])
            self.assertFalse(remote.writes())

    def test_stale_catalog_and_incomplete_catalog_stop_before_dispatch(self):
        with self.assertRaisesRegex(ReviewError, "repository changed"):
            self.service.prepare(REPO, "f" * 40, EXISTING, ["example-primary"])
        self.remote.files["clients/broken/workspace.yml"] = b"bad: ["
        with self.assertRaisesRegex(ReviewError, "catalog issues"): self.prepare()
        self.assertFalse(self.remote.writes())

    def test_missing_dropdown_option_and_unknown_input_fail_closed(self):
        with self.assertRaisesRegex(ReviewError, "dropdown is missing"):
            input_batches(workflow([]), ["example-primary"], EXISTING)
        other = workflow(); other["on"]["workflow_dispatch"]["inputs"]["deploy_all"] = {"type": "boolean", "default": True}
        with self.assertRaisesRegex(ReviewError, "inputs changed"): input_batches(other, ["example-primary"], EXISTING)

    def test_public_repo_inactive_workflow_and_nonmain_default_rejected(self):
        self.remote.metadata["private"] = False
        with self.assertRaises(ReviewError): self.prepare()
        self.remote.metadata["private"] = True; self.remote.metadata["default_branch"] = "develop"
        with self.assertRaises(ReviewError): self.prepare()
        self.remote.metadata["default_branch"] = "main"; self.remote.workflow["state"] = "disabled_manually"
        with self.assertRaises(ReviewError): self.prepare()

    def test_dispatch_records_receipt_and_returned_run_never_deploys(self):
        record = self.prepare(); self.service.dispatch(record, self.folder)
        self.assertEqual(self.remote.dispatched, [{"ref": "main", "inputs": record["plan"]["batches"][0]}])
        self.assertEqual(record["results"][0]["run_id"], 101)
        saved = json.loads(next(self.folder.glob("*.preview.json")).read_text())
        self.assertTrue(saved["attempted"]); self.assertEqual(saved["results"][0]["run_id"], 101)
        with self.assertRaises(ReviewError): self.service.dispatch(record, self.folder)
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_changed_main_account_and_mutated_plan_cannot_dispatch(self):
        record = self.prepare(); self.remote.head = "e" * 40
        with self.assertRaisesRegex(ReviewError, "Main changed"): self.service.dispatch(record, self.folder)
        self.remote.head = "a" * 40; self.remote.identity = "different-account"
        with self.assertRaisesRegex(ReviewError, "account changed"): self.service.dispatch(record, self.folder)
        record["plan"]["batches"][0]["mode"] = "deploy"
        with self.assertRaises(ReviewError): self.service.dispatch(record, self.folder)
        self.assertFalse(self.remote.dispatched)

    def test_lost_response_stops_batching_without_retry(self):
        targets = self.remote.clients(3); self.remote.files[WORKFLOW_PATH] = json.dumps(workflow(targets)).encode()
        record = self.prepare(targets); self.remote.lose_response = True
        with self.assertRaisesRegex(ReviewError, "Lost"): self.service.dispatch(record, self.folder)
        self.assertEqual([r["state"] for r in record["results"]], ["Submission unconfirmed", "Not submitted"])
        with self.assertRaises(ReviewError): self.service.dispatch(record, self.folder)
        self.assertEqual(len(self.remote.dispatched), 1)
        self.assertIn("Submission unconfirmed", next(self.folder.glob("*.preview.json")).read_text())

    def test_main_change_between_batches_stops_remaining_submissions(self):
        targets = self.remote.clients(3); self.remote.files[WORKFLOW_PATH] = json.dumps(workflow(targets)).encode()
        record = self.prepare(targets)
        def request(method, endpoint, body=None):
            result = self.remote(method, endpoint, body)
            if method == "POST": self.remote.head = "e" * 40
            return result
        self.service.request = request
        with self.assertRaisesRegex(ReviewError, "Main changed"): self.service.dispatch(record, self.folder)
        self.assertEqual(len(self.remote.dispatched), 1)
        self.assertEqual(record["results"][1]["state"], "Not submitted")

    def test_status_rejects_wrong_run_identity(self):
        record = self.prepare(); self.service.dispatch(record, self.folder)
        def request(method, endpoint, body=None):
            result = self.remote(method, endpoint, body)
            if "/actions/runs/" in endpoint: result["repository"]["id"] = 99
            return result
        self.service.request = request
        with self.assertRaisesRegex(ReviewError, "does not match"): self.service.refresh(record, self.folder)
        self.assertNotEqual(record["results"][0]["state"], "success")

    def test_unwritable_receipt_prevents_network_write(self):
        record = self.prepare()
        with patch.object(self.service, "save", side_effect=OSError("Read only")):
            with self.assertRaises(OSError): self.service.dispatch(record, self.folder)
        self.assertFalse(self.remote.dispatched)

    def test_status_is_for_exact_run_and_flags_revision_race(self):
        record = self.prepare(); self.service.dispatch(record, self.folder)
        self.remote.run_sha = "e" * 40; before = len(self.remote.writes())
        self.service.refresh(record, self.folder)
        self.assertIn("main changed", record["results"][0]["state"])
        self.assertEqual(len(self.remote.writes()), before)
        self.assertTrue(any(endpoint.endswith("/actions/runs/101") for method, endpoint, body in self.remote.calls))

    def test_old_no_content_response_is_accepted_without_guessing_run(self):
        record = self.prepare(); request = self.remote
        def no_content(method, endpoint, body=None):
            result = request(method, endpoint, body)
            return None if method == "POST" else result
        self.service.request = no_content; self.service.dispatch(record, self.folder)
        self.assertNotIn("run_id", record["results"][0]); self.assertIn("unavailable", record["results"][0]["state"])

    def test_api_pins_version_and_preserves_json_arguments(self):
        with patch("sentinel_app.preview_workflow.shutil.which", return_value="gh"), patch("sentinel_app.preview_workflow.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"workflow_run_id":8}', '')) as run:
            self.assertEqual(api("POST", "repos/example/detections/actions/workflows/a/dispatches", {"ref": "main"})["workflow_run_id"], 8)
        self.assertIn("X-GitHub-Api-Version: 2026-03-10", run.call_args.args[0])
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"ref": "main"})


if __name__ == "__main__": unittest.main()
