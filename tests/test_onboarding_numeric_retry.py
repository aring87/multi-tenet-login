"""Offline regression coverage for closed-PR retries after slug quoting changed."""
import base64
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import full_onboarding as full
import lighthouse_onboarding as lighthouse
from yaml_identifiers import matches_legacy_slug_manifest


class GitHub:
    def __init__(self, text):
        self.text = text
        self.main = None
        self.calls = []
        self.conflict = False

    def gh(self, method, path, body=None, missing=False):
        self.calls.append((method, path, body))
        if method == 'GET' and '/git/ref/heads/' in path:
            return {'object': {'sha': 'head'}}
        if method == 'GET' and '/contents/' in path:
            text = self.main if path.endswith('?ref=main') else self.text
            if text is None:
                return None
            return {'content': base64.b64encode(text.encode()).decode(), 'sha': 'original-blob'}
        if method == 'PUT':
            if self.conflict:
                raise full.Stop('GitHub 409: SHA conflict')
            self.text = base64.b64decode(body['content']).decode()
            return {}
        if method == 'POST' and path.endswith('/pulls'):
            return {'html_url': 'https://github.com/example/repo/pull/2'}
        raise AssertionError((method, path))

    def pages(self, path):
        assert 'state=open' in path
        return []  # Previous PR was closed.


class NumericRetryTests(unittest.TestCase):
    def make_run(self, module, client='413'):
        run = object.__new__(module.Onboard)
        run.c = dict(client=client, target=client+'-primary', workspace_label='primary',
                     tenant_id='client-tenant', managing_tenant_id='managing-tenant',
                     subscription_id='subscription', resource_group='rg', workspace_name='ws',
                     github_owner='example', initial_rule_path='rules/test.yml', allow_missing_mitre=False)
        run.workspace = {'customerId': 'workspace-guid'}
        run.repo_path = 'repos/example/repo'
        run.branch = ('onboard/' if module is lighthouse else 'codex/onboard-') + run.c['target']
        run.state = {'branch': run.branch}
        run.save = Mock()
        run.update_dropdowns = Mock(return_value=[])
        expected = run.manifest()
        old = expected.replace('client: "'+client+'"', 'client: '+client)
        old = old.replace('target: "'+run.c['target']+'"', 'target: '+run.c['target'])
        run.io = GitHub(old)
        return run, expected

    def inspect(self, run):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            run.inspect_target()
        return output.getvalue()

    def apply(self, run):
        with contextlib.redirect_stdout(io.StringIO()):
            run.create_target_pr()

    def test_preview_recognizes_legacy_numeric_manifest_without_writes(self):
        for module in (full, lighthouse):
            with self.subTest(module=module.__name__):
                run, _ = self.make_run(module)
                self.assertIn('repair legacy', self.inspect(run))
                self.assertTrue(all(call[0] == 'GET' for call in run.io.calls))
                run.save.assert_not_called()

    def test_apply_repairs_only_owned_branch_with_sha_and_opens_replacement_pr(self):
        for module in (full, lighthouse):
            for client in ('413', '0413', 'on'):
                with self.subTest(module=module.__name__, client=client):
                    run, expected = self.make_run(module, client)
                    self.inspect(run)
                    self.apply(run)
                    puts = [call for call in run.io.calls if call[0] == 'PUT']
                    self.assertEqual(len(puts), 1)
                    self.assertEqual(puts[0][2]['sha'], 'original-blob')
                    self.assertEqual(puts[0][2]['branch'], run.branch)
                    self.assertEqual(run.io.text, expected)
                    self.assertEqual(run.state['pull_request'], 'https://github.com/example/repo/pull/2')
                    self.assertEqual(sum(call[0] == 'POST' for call in run.io.calls), 1)

    def test_repaired_manifest_is_idempotent(self):
        for module in (full, lighthouse):
            run, expected = self.make_run(module)
            run.io.text = expected
            self.assertNotIn('repair legacy', self.inspect(run))
            self.apply(run)
            self.assertFalse(any(call[0] == 'PUT' for call in run.io.calls))

    def test_partially_manually_quoted_manifest_is_repaired(self):
        for module in (full, lighthouse):
            run, expected = self.make_run(module)
            run.io.text = run.io.text.replace('client: 413', "client: '413'")
            self.inspect(run)
            self.apply(run)
            self.assertEqual(run.io.text, expected)

    def test_changed_resource_or_rules_are_blocked(self):
        for module in (full, lighthouse):
            for old, new in [('subscription', 'other-subscription'), ('tenant', 'other-tenant'),
                             ('workspace-guid', 'other-guid'), ('rules/test.yml', 'rules/other.yml'),
                             ('      enabled: false', '      enabled: true'), ('client: 413', 'client: 414')]:
                with self.subTest(module=module.__name__, field=old):
                    run, _ = self.make_run(module)
                    run.io.text = run.io.text.replace(old, new)
                    with self.assertRaisesRegex(module.Stop, 'Closing a pull request'):
                        self.inspect(run)
                    self.assertTrue(all(call[0] == 'GET' for call in run.io.calls))

    def test_unowned_branch_is_not_adopted(self):
        for module in (full, lighthouse):
            run, _ = self.make_run(module)
            run.state = {}
            with self.assertRaisesRegex(module.Stop, 'outside this state file'):
                self.inspect(run)

    def test_changed_main_still_requires_review(self):
        for module in (full, lighthouse):
            run, _ = self.make_run(module)
            run.io.main = run.io.text
            with self.assertRaisesRegex(module.Stop, 'Target already exists with different content'):
                self.inspect(run)

    def test_edit_between_preview_and_apply_cannot_be_overwritten(self):
        for module in (full, lighthouse):
            run, _ = self.make_run(module)
            self.inspect(run)
            run.io.text = run.io.text.replace('workspace-guid', 'different-guid')
            with self.assertRaisesRegex(module.Stop, 'target changed'):
                self.apply(run)
            self.assertTrue(all(call[0] == 'GET' for call in run.io.calls))
            run.update_dropdowns.assert_not_called()

    def test_sha_conflict_does_not_open_pr(self):
        for module in (full, lighthouse):
            run, _ = self.make_run(module)
            self.inspect(run)
            run.io.conflict = True
            with self.assertRaisesRegex(full.Stop, '409'):
                self.apply(run)
            self.assertFalse(any(call[0] == 'POST' for call in run.io.calls))
            run.update_dropdowns.assert_not_called()

    def test_duplicate_fields_and_leading_zero_changes_do_not_match(self):
        run, expected = self.make_run(lighthouse, '0413')
        for altered in (run.io.text + 'client: 0413\n', run.io.text.replace('0413', '413')):
            self.assertFalse(matches_legacy_slug_manifest(altered, expected,
                             client='0413', target='0413-primary'))

    def test_ordinary_client_manifest_is_unchanged(self):
        for module in (full, lighthouse):
            run, expected = self.make_run(module, 'acme')
            self.assertNotIn('repair legacy', self.inspect(run))
            self.assertEqual(run.io.text, expected)
            self.apply(run)
            self.assertFalse(any(call[0] == 'PUT' for call in run.io.calls))


if __name__ == '__main__':
    unittest.main()
