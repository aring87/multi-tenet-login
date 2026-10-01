"""No live dispatches: deployment permission is exercised against synthetic runs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deployment_workflow import DeploymentService, preview_ready
from preview_workflow import PreviewService, WORKFLOW_PATH
from repository_reviews import ReviewError, fingerprint
from test_preview_workflow import PreviewGitHub, REPO, EXISTING, workflow


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.remote = PreviewGitHub()
        self.preview_service = PreviewService(self.remote)
        self.service = DeploymentService(self.remote)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def preview(self, targets=None):
        record = self.preview_service.prepare(REPO, self.remote.head, EXISTING, targets or ["example-primary"])
        self.preview_service.dispatch(record, self.folder)
        self.preview_service.refresh(record, self.folder)
        return record

    def test_prepare_is_read_only_and_preserves_exact_targets_and_rule_overrides(self):
        client = json.loads(self.remote.files["clients/example/workspace.yml"])
        client["rules"][0]["overrides"] = {"enabled": False}
        self.remote.files["clients/example/workspace.yml"] = json.dumps(client).encode()
        preview = self.preview(); before = len(self.remote.writes())
        deployment = self.service.prepare(preview)
        self.assertEqual(len(self.remote.writes()), before)
        self.assertEqual(deployment["plan"]["clients"][0]["rule_state"], "Disabled")
        self.assertEqual(deployment["plan"]["clients"][0]["overrides"], {"enabled": False})
        self.assertEqual(deployment["plan"]["batches"], [{"mode": "deploy", "rule_path": EXISTING, "targets": "example-primary"}])
        self.assertNotIn("preview", preview["plan"])
        self.assertFalse(deployment["attempted"])

    def test_dispatch_records_proof_then_tracks_exact_deployment_run(self):
        preview = self.preview(); deployment = self.service.prepare(preview)
        self.service.dispatch(deployment, self.folder)
        self.assertEqual(self.remote.dispatched[-1]["inputs"]["mode"], "deploy")
        self.assertEqual(deployment["results"][0]["run_id"], 102)
        saved = json.loads(next(self.folder.glob("*.deployment.json")).read_text())
        self.assertEqual(saved["plan"]["preview"]["request_id"], preview["request_id"])
        self.assertTrue(saved["attempted"])
        self.service.refresh(deployment, self.folder)
        self.assertEqual(deployment["results"][0]["state"], "success")
        with self.assertRaisesRegex(ReviewError, "already attempted"):
            self.service.dispatch(deployment, self.folder)
        self.assertEqual(len(self.remote.dispatched), 2)

    def test_failed_pending_cancelled_and_rerunning_previews_block_deploy(self):
        preview = self.preview()
        for status, conclusion in (("completed", "failure"), ("completed", "cancelled"),
                                   ("queued", None), ("in_progress", "success"), ("completed", "skipped")):
            def request(method, endpoint, body=None):
                result = self.remote(method, endpoint, body)
                if "/actions/runs/" in endpoint: result.update(status=status, conclusion=conclusion)
                return result
            with self.subTest(status=status, conclusion=conclusion), self.assertRaisesRegex(ReviewError, "not completed successfully"):
                DeploymentService(request).prepare(preview)
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_missing_or_duplicate_run_ids_and_changed_batch_proof_block(self):
        targets = self.remote.clients(3)
        self.remote.files[WORKFLOW_PATH] = json.dumps(workflow(targets)).encode()
        preview = self.preview(targets)
        for change in ("missing", "duplicate", "inputs", "result_count", "fingerprint"):
            bad = copy.deepcopy(preview)
            if change == "missing":bad["results"][0].pop("run_id")
            elif change == "duplicate":bad["results"][1]["run_id"] = bad["results"][0]["run_id"]
            elif change == "inputs":bad["results"][0]["inputs"]["target"] = "different"
            elif change == "result_count":bad["results"].pop()
            else:bad["fingerprint"] = "wrong"
            with self.subTest(change=change), self.assertRaises(ReviewError):self.service.prepare(bad)
        self.assertEqual(len(self.remote.dispatched), 2)

    def test_preview_revision_and_changed_main_block(self):
        preview = self.preview()
        self.remote.run_sha = "f" * 40
        with self.assertRaisesRegex(ReviewError, "different revision"):self.service.prepare(preview)
        self.remote.run_sha = self.remote.head; self.remote.head = "e" * 40
        with self.assertRaisesRegex(ReviewError, "repository changed"):self.service.prepare(preview)
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_changed_account_and_assignment_block_review(self):
        preview = self.preview()
        self.remote.identity = "other"
        with self.assertRaisesRegex(ReviewError, "changed"):self.service.prepare(preview)
        self.remote.identity = preview["plan"]["identity"]
        client = json.loads(self.remote.files["clients/example/workspace.yml"])
        client["rules"] = []; self.remote.files["clients/example/workspace.yml"] = json.dumps(client).encode()
        with self.assertRaisesRegex(ReviewError, "assigned"):self.service.prepare(preview)

    def test_workflow_must_support_deploy_mode(self):
        w = workflow(); w["on"]["workflow_dispatch"]["inputs"]["mode"]["options"] = ["preview"]
        self.remote.files[WORKFLOW_PATH] = json.dumps(w).encode()
        preview = self.preview()
        with self.assertRaisesRegex(ReviewError, "deploy mode choice"):self.service.prepare(preview)

    def test_main_or_preview_failure_after_review_prevents_dispatch(self):
        deployment = self.service.prepare(self.preview())
        self.remote.head = "f" * 40
        with self.assertRaisesRegex(ReviewError, "Main changed"):self.service.dispatch(deployment, self.folder)
        self.remote.head = deployment["plan"]["revision"]
        def request(method, endpoint, body=None):
            value = self.remote(method, endpoint, body)
            if "/actions/runs/" in endpoint:value["conclusion"] = "failure"
            return value
        self.service.request = request
        with self.assertRaisesRegex(ReviewError, "not completed successfully"):self.service.dispatch(deployment, self.folder)
        self.assertFalse(deployment["attempted"])
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_cannot_change_selection_or_use_preview_service_to_deploy(self):
        deployment = self.service.prepare(self.preview())
        with self.assertRaisesRegex(ReviewError, "Only preview"):
            self.preview_service.dispatch(deployment, self.folder)
        deployment["plan"]["batches"][0]["targets"] = "different-primary"
        deployment["fingerprint"] = fingerprint(deployment["plan"])
        with self.assertRaisesRegex(ReviewError, "exactly the previewed"):
            self.service.dispatch(deployment, self.folder)
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_lost_deploy_response_stops_other_batches_without_retry(self):
        targets = self.remote.clients(3)
        self.remote.files[WORKFLOW_PATH] = json.dumps(workflow(targets)).encode()
        deployment = self.service.prepare(self.preview(targets)); self.remote.lose_response = True
        with self.assertRaisesRegex(ReviewError, "Lost"):self.service.dispatch(deployment, self.folder)
        self.assertEqual([r["state"] for r in deployment["results"]], ["Submission unconfirmed", "Not submitted"])
        with self.assertRaisesRegex(ReviewError, "already attempted"):self.service.dispatch(deployment, self.folder)
        self.assertEqual(len(self.remote.dispatched), 3)

    def test_main_change_between_deployment_batches_stops_remaining_targets(self):
        targets = self.remote.clients(3)
        self.remote.files[WORKFLOW_PATH] = json.dumps(workflow(targets)).encode()
        deployment = self.service.prepare(self.preview(targets))
        def request(method, endpoint, body=None):
            value = self.remote(method, endpoint, body)
            if method == "POST":self.remote.head = "e" * 40
            return value
        self.service.request = request
        with self.assertRaisesRegex(ReviewError, "Main changed"):self.service.dispatch(deployment, self.folder)
        self.assertEqual(len(self.remote.dispatched), 3)
        self.assertEqual(deployment["results"][1]["state"], "Not submitted")

    def test_receipt_write_failure_prevents_deployment(self):
        deployment = self.service.prepare(self.preview())
        with patch.object(self.service, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):self.service.dispatch(deployment, self.folder)
        self.assertEqual(len(self.remote.dispatched), 1)

    def test_ui_ready_requires_freshly_checked_success_at_reviewed_sha(self):
        record = self.preview()
        self.assertTrue(preview_ready(record))
        for field, value in (("run_status", "queued"), ("conclusion", "failure"), ("actual_revision", "other")):
            bad = copy.deepcopy(record); bad["results"][0][field] = value
            self.assertFalse(preview_ready(bad))
        self.assertFalse(preview_ready(None))

    def test_waiting_deployment_status_is_read_only_and_does_not_approve(self):
        deployment = self.service.prepare(self.preview())
        self.service.dispatch(deployment, self.folder)
        before = len(self.remote.writes())
        def request(method, endpoint, body=None):
            value = self.remote(method, endpoint, body)
            if "/actions/runs/" in endpoint:value.update(status="waiting", conclusion=None)
            return value
        self.service.request = request
        self.service.refresh(deployment, self.folder)
        self.assertEqual(deployment["results"][0]["state"], "waiting")
        self.assertEqual(len(self.remote.writes()), before)

    def test_rerunning_status_does_not_display_previous_success(self):
        deployment = self.service.prepare(self.preview())
        self.service.dispatch(deployment, self.folder)
        def request(method, endpoint, body=None):
            value = self.remote(method, endpoint, body)
            if "/actions/runs/" in endpoint:value.update(status="in_progress", conclusion="success")
            return value
        self.service.request = request
        self.service.refresh(deployment, self.folder)
        self.assertEqual(deployment["results"][0]["state"], "in_progress")
