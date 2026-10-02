import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentinel_app.workflow_history import load_receipt,list_receipts,summary,WorkflowHistoryService,workflow_url
from sentinel_app.preview_workflow import PreviewService
from sentinel_app.deployment_workflow import DeploymentService
from sentinel_app.repository_reviews import ReviewError, fingerprint
from test_preview_workflow import PreviewGitHub, REPO, EXISTING


class WorkflowHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.remote=PreviewGitHub();self.service=PreviewService(self.remote)
        self.record=self.service.prepare(REPO,self.remote.head,EXISTING,['example-primary'])
        self.service.dispatch(self.record,self.root/'preview-requests')
        self.path=next((self.root/'preview-requests').glob('*.preview.json'))
        self.remote.calls.clear()

    def save(self,record):self.path.write_text(json.dumps(record),encoding='utf-8')

    def test_listing_reopens_after_restart_without_network(self):
        rows,issues=list_receipts(self.root)
        self.assertFalse(issues);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['record']['plan']['targets'],['example-primary'])
        self.assertFalse(self.remote.calls)

    def test_refresh_is_get_only_and_preserves_review_after_main_moves(self):
        self.remote.head='e'*40
        record,mode,issues=WorkflowHistoryService(self.remote).refresh(self.path)
        self.assertEqual(record['plan'],self.record['plan']);self.assertEqual(mode,'preview');self.assertFalse(issues)
        self.assertEqual(summary(record),'All runs succeeded (last checked)')
        self.assertTrue(all(call[0]=='GET' for call in self.remote.calls))
        self.assertEqual(load_receipt(self.path)[0],record)

    def test_revision_mismatch_is_visible(self):
        self.remote.run_sha='f'*40
        record,_,_=WorkflowHistoryService(self.remote).refresh(self.path)
        self.assertIn('Revision mismatch',summary(record))
        self.assertIn('revision mismatch',record['results'][0]['state'])

    def test_unknown_run_is_not_guessed_or_resubmitted(self):
        self.record['results'][0].pop('run_id');self.record['results'][0]['state']='Submission unconfirmed'
        self.save(self.record)
        record,_,_=WorkflowHistoryService(self.remote).refresh(self.path)
        self.assertIn('unconfirmed',summary(record))
        self.assertFalse(any('/runs/' in c[1] for c in self.remote.calls))
        self.assertTrue(workflow_url(record,0).endswith('/sentinel-multi-workspace.yml'))
        self.assertTrue(all(c[0]=='GET' for c in self.remote.calls))

    def test_changed_run_identity_does_not_replace_cached_success(self):
        history=WorkflowHistoryService(self.remote);history.refresh(self.path)
        def request(method,endpoint,body=None):
            data=self.remote(method,endpoint,body)
            if '/runs/' in endpoint:data['repository']['id']=999
            return data
        record,_,issues=WorkflowHistoryService(request).refresh(self.path)
        self.assertTrue(issues);self.assertIn('Refresh incomplete',summary(record))
        self.assertEqual(record['results'][0]['conclusion'],'success')

    def test_changed_repository_or_workflow_stops_refresh(self):
        for change in ('repository','workflow'):
            before=self.path.read_bytes()
            def request(method,endpoint,body=None):
                data=self.remote(method,endpoint,body)
                if change=='repository' and endpoint=='repos/'+REPO:data['id']=999
                if change=='workflow' and '/workflows/' in endpoint:data['path']='other.yml'
                return data
            with self.assertRaises(ReviewError):WorkflowHistoryService(request).refresh(self.path)
            self.assertEqual(self.path.read_bytes(),before)

    def test_deployment_receipt_with_preview_proof_can_be_read(self):
        self.service.refresh(self.record,self.root/'preview-requests')
        service=DeploymentService(self.remote);record=service.prepare(self.record)
        service.dispatch(record,self.root/'deployment-requests')
        path=next((self.root/'deployment-requests').glob('*.deployment.json'))
        refreshed,mode,issues=WorkflowHistoryService(self.remote).refresh(path)
        self.assertEqual(mode,'deploy');self.assertFalse(issues)
        self.assertEqual(refreshed['plan']['preview'],record['plan']['preview'])
        self.assertEqual(len(list_receipts(self.root)[0]),2)

    def test_malformed_and_oversized_files_are_reported_without_writes(self):
        folder=self.root/'preview-requests';(folder/'broken.preview.json').write_text('{bad')
        (folder/'huge.preview.json').write_bytes(b'x'*(2*1024*1024+1))
        rows,issues=list_receipts(self.root)
        self.assertEqual(len(rows),1);self.assertEqual(len(issues),2)
        self.assertEqual((folder/'broken.preview.json').read_text(),'{bad')

    def test_tampered_receipt_and_invalid_run_ids_are_rejected(self):
        for mutate in (lambda r:r['plan'].update(repository='other/repo'),
                       lambda r:r['results'][0].update(run_id=True)):
            record=copy.deepcopy(self.record);mutate(record);self.save(record)
            with self.assertRaises(ReviewError):load_receipt(self.path)

    def test_stored_urls_are_never_opened(self):
        self.record['results'][0]['url']='https://evil.invalid'
        self.assertEqual(workflow_url(self.record,0),'https://github.com/'+REPO+'/actions/runs/101')

    def test_concurrent_receipt_update_is_not_overwritten(self):
        def request(method,endpoint,body=None):
            if '/runs/' in endpoint:
                changed=copy.deepcopy(self.record);changed['results'][0]['state']='Updated elsewhere';self.save(changed)
            return self.remote(method,endpoint,body)
        with self.assertRaisesRegex(ReviewError,'changed during refresh'):WorkflowHistoryService(request).refresh(self.path)
        self.assertIn('Updated elsewhere',self.path.read_text())

    def test_linked_files_are_skipped(self):
        with patch('sentinel_app.workflow_history.linked',side_effect=lambda path:Path(path)==self.path):
            rows,issues=list_receipts(self.root)
        self.assertFalse(rows);self.assertTrue(issues)


if __name__=='__main__':unittest.main()
