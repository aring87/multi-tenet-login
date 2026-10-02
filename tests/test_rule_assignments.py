import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

from sentinel_app import rule_assignments as assignments
from sentinel_app.repository_catalog import GitHubReader, parse_yaml
from sentinel_app.repository_reviews import ReviewError, fingerprint
from test_repository_reviews import FakeGitHub, REPO, EXISTING


class AssignmentGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.files={k:v for k,v in self.files.items() if not k.startswith('clients/')}
        self.add_client('example')
        self.add_client('413')

    def add_client(self,name,assigned=False):
        target=dict(version=1,target=name+'-workspace-commercial',client=name,enabled=True,
                    allow_missing_mitre=False,azure=dict(tenant_id='11111111-1111-4111-8111-111111111111',
                    subscription_id='22222222-2222-4222-8222-222222222222',resource_group='rg-example',
                    workspace_name='law-example',workspace_id='33333333-3333-4333-8333-333333333333'),
                    rules=[{'path':EXISTING,'overrides':{'enabled':True,'severity':'High','id':'44444444-4444-4444-8444-444444444444'}}] if assigned else [])
        self.files['clients/'+name+'/'+name+'-workspace-commercial.yml']=('# Keep this client comment\n'+yaml.safe_dump(target,sort_keys=False)).encode()
        return target

    def __call__(self,method,endpoint,body=None):
        if method=='GET' and endpoint=='repos/'+REPO+'/commits/'+'a'*40:
            self.calls.append((method,endpoint,body));return {'sha':'a'*40,'commit':{'tree':{'sha':'b'*40}}}
        return super().__call__(method,endpoint,body)


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.remote=AssignmentGitHub();self.service=assignments.AssignmentService(self.remote)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)

    def prepare(self,targets=None,enabled=False):
        return self.service.prepare(REPO,'a'*40,EXISTING,targets or ['413-workspace-commercial','example-workspace-commercial'],enabled)

    def test_prepare_reads_only_and_reviews_all_changed_files(self):
        record=self.prepare()
        self.assertEqual(len(record['plan']['files']),2);self.assertFalse(self.remote.writes())
        self.assertIn('overrides:',assignments.assignment_diff(record['plan']))
        for f in record['plan']['files']:
            old=parse_yaml(f['before'].encode());new=parse_yaml(f['content'].encode())
            self.assertEqual(old['azure'],new['azure']);self.assertEqual(old['enabled'],new['enabled'])
            self.assertIn('# Keep this client comment',f['content'])
            self.assertIs(new['rules'][0]['overrides']['enabled'],False)

    def test_only_selected_files_change_and_existing_overrides_are_preserved(self):
        original=self.remote.add_client('example',True)
        record=self.prepare(['example-workspace-commercial'])
        self.assertEqual(len(record['plan']['files']),1)
        updated=parse_yaml(record['plan']['files'][0]['content'].encode())
        self.assertEqual(updated['rules'][0]['overrides'],dict(original['rules'][0]['overrides'],enabled=False))
        self.assertNotIn('413',record['plan']['files'][0]['path'])

    def test_round_trip_preserves_bom_crlf_comments_and_numeric_quoting(self):
        text='\ufeff# Keep\r\nclient: "413"\r\nrules: []\r\n'
        result=assignments.update_assignment(text,EXISTING,False)
        self.assertTrue(result.startswith('\ufeff# Keep\r\n'));self.assertIn('client: "413"',result)
        self.assertNotIn('\n',result.replace('\r\n',''))

    def test_disabled_target_stays_disabled_and_is_explained(self):
        key=next(k for k in self.remote.files if k.startswith('clients/413/'))
        doc=parse_yaml(self.remote.files[key]);doc['enabled']=False;self.remote.files[key]=yaml.safe_dump(doc).encode()
        record=self.prepare(['413-workspace-commercial'],True)
        self.assertFalse(parse_yaml(record['plan']['files'][0]['content'].encode())['enabled'])
        self.assertTrue(any('target remains disabled' in w for w in record['plan']['warnings']))

    def test_no_op_assignments_cannot_create_empty_pr(self):
        self.remote.add_client('example',True)
        with self.assertRaisesRegex(ReviewError,'No changes'):self.prepare(['example-workspace-commercial'],True)
        self.assertFalse(self.remote.writes())

    def test_one_commit_and_pr_carries_all_client_files_only(self):
        record=self.prepare();url=self.service.submit(record,self.folder)
        writes=self.remote.writes();self.assertEqual(len(writes),4)
        self.assertEqual(len(writes[0][2]['tree']),2)
        self.assertTrue(all(f['path'].startswith('clients/') for f in writes[0][2]['tree']))
        self.assertTrue(writes[2][2]['ref'].startswith('refs/heads/codex/assign-'))
        self.assertTrue(writes[-1][2]['draft']);self.assertNotIn('/dispatches',str(writes))
        self.assertEqual(self.remote.head,'a'*40)
        saved=assignments.load_request(next(self.folder.glob('*.assignment.json')))
        self.assertEqual(saved['pull_request'],url)

    def test_stale_catalog_or_base_stops_before_writes(self):
        record=self.prepare();self.remote.head='e'*40
        with self.assertRaisesRegex(ReviewError,'changed'):self.prepare()
        with self.assertRaisesRegex(ReviewError,'changed after preview'):self.service.submit(record,self.folder)
        self.assertFalse(self.remote.writes())

    def test_recovery_after_lost_branch_or_pr_response_is_idempotent(self):
        for suffix in ('/git/refs','/pulls'):
            with self.subTest(suffix=suffix):
                self.remote=AssignmentGitHub();self.service=assignments.AssignmentService(self.remote)
                record=self.prepare();self.remote.fail_after=suffix
                with self.assertRaises(ReviewError):self.service.submit(record,self.folder)
                self.remote.head='e'*40  # Recover the reviewed branch, not a new revision.
                self.service.submit(record,self.folder)
                self.assertEqual(len(self.remote.refs),1);self.assertEqual(len(self.remote.pulls),1)

    def test_modified_branch_stops_recovery(self):
        record=self.prepare();self.service.submit(record,self.folder);self.remote.refs[0]['object']['sha']='f'*40
        with self.assertRaisesRegex(ReviewError,'changed outside'):self.service.submit(record,self.folder)

    def test_local_save_failure_prevents_remote_writes(self):
        record=self.prepare()
        with patch.object(self.service,'save_record',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.service.submit(record,self.folder)
        self.assertFalse(self.remote.writes())

    def test_tampered_destinations_rejected_even_with_recomputed_fingerprint(self):
        record=self.prepare();f=record['plan']['files'][0]
        f['content']=f['content'].replace('rg-example','rg-unreviewed')
        record['fingerprint']=fingerprint(record['plan'])
        with self.assertRaisesRegex(ReviewError,'differ from the reviewed base'):self.service.submit(record,self.folder)
        self.assertFalse(self.remote.writes())

    def test_retired_rule_cannot_be_enabled(self):
        doc=parse_yaml(self.remote.files[EXISTING]);doc['status']='retired';doc['enabled']=False
        self.remote.files[EXISTING]=yaml.safe_dump(doc).encode()
        with self.assertRaisesRegex(ValueError,'Retired'):self.prepare(enabled=True)

    def test_target_mitre_exception_is_not_implicitly_enabled(self):
        doc=parse_yaml(self.remote.files[EXISTING]);doc['tactics']=[];doc['techniques']=[];doc['subTechniques']=[]
        self.remote.files[EXISTING]=yaml.safe_dump(doc).encode()
        with self.assertRaisesRegex(ValueError,'Missing MITRE'):self.prepare()

    def test_duplicate_effective_rule_id_blocks_assignment(self):
        doc=parse_yaml(self.remote.files[EXISTING]);self.remote.files['rules/sentinel/other.yml']=yaml.safe_dump(doc).encode()
        key=next(k for k in self.remote.files if k.startswith('clients/example/'))
        client=parse_yaml(self.remote.files[key]);client['rules']=[{'path':'rules/sentinel/other.yml'}]
        self.remote.files[key]=yaml.safe_dump(client).encode()
        with self.assertRaisesRegex(ReviewError,'Duplicate effective rule ID'):self.prepare()

    def test_invalid_targets_count_and_visibility_are_rejected(self):
        for targets in ([],['unknown'],['example-workspace-commercial']*2):
            with self.assertRaises(ReviewError):self.service.prepare(REPO,'a'*40,EXISTING,targets)
        self.remote.metadata['private']=False
        with self.assertRaisesRegex(ReviewError,'private'):self.prepare()
        self.assertFalse(self.remote.writes())

    def test_identity_or_permission_change_blocks_submit(self):
        record=self.prepare();self.remote.identity='other'
        with self.assertRaisesRegex(ReviewError,'account changed'):self.service.submit(record,self.folder)
        self.assertFalse(self.remote.writes())

    def test_missing_rule_catalog_error_and_unsafe_yaml_stop(self):
        del self.remote.files[EXISTING]
        with self.assertRaises(ReviewError):self.prepare()
        for before in ('rules: &x [*x]','rules: []\nrules: []'):
            with self.assertRaises(ValueError):assignments.update_assignment(before,EXISTING,False)


if __name__=='__main__':unittest.main()
