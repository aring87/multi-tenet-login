"""No real tokens, Azure requests, or permission changes are used by these tests."""
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from sentinel_app import tenant_access as access
from sentinel_app import desktop_backend as backend
from sentinel_app.auth_recovery import AuthenticationRequired, SignInCancelled
from sentinel_app.lighthouse_onboarding import Stop
from test_desktop import TENANT, SUB, P1, P2


def token(**overrides):
    claims = dict(tid=TENANT, oid=P1, aud=access.RESOURCE, exp=time.time()+3600,
                  upn='admin@example.test', scp='user_impersonation')
    claims.update(overrides)
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return 'synthetic-header.' + encoded + '.synthetic-signature'


class Session:
    signed_in = True
    def __init__(self):
        self.calls = []
        self.cloud = 'AzureCloud'
        self.account = dict(tenantId=TENANT, user=dict(type='user', name='admin@example.test'))
        self.token = token()
    def az(self, *args):
        self.calls.append(args)
        if args == ('cloud', 'show'):return dict(name=self.cloud)
        if args == ('account', 'show'):return self.account
        if args[:2] == ('account', 'get-access-token'):return dict(accessToken=self.token)
        raise AssertionError(args)


class TenantAccessTests(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.transport = patch.object(access, 'build_opener').start()
        self.addCleanup(patch.stopall)
        self.opener = self.transport.return_value
        self.opener.open.return_value.__enter__.return_value.status = 200

    def test_read_only_review_without_subscriptions_never_posts_or_retains_token(self):
        plan = access.access_management_plan(self.session, TENANT)
        self.assertEqual(plan['principal'], P1)
        self.assertEqual(plan['scope'], '/')
        self.assertNotIn(self.session.token, json.dumps(plan))
        self.transport.assert_not_called()
        self.assertEqual(self.session.calls[-1], ('account', 'get-access-token', '--tenant', TENANT,
                                                 '--resource', access.RESOURCE))

    def test_confirmed_apply_rechecks_identity_and_posts_only_to_arm(self):
        plan = access.access_management_plan(self.session, TENANT)
        result = access.enable_access_management(self.session, plan)
        self.assertEqual(sum(call[:2] == ('account', 'get-access-token') for call in self.session.calls), 2)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, access.ENDPOINT)
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.session.token)
        self.assertIn('remains assigned', result)
        self.assertNotIn(self.session.token, result)
        self.assertIsInstance(self.transport.call_args.args[0], access.NoRedirect)

    def test_expired_or_changed_scope_review_prevents_post(self):
        for changes in ({'created': time.time()-901}, {'created': time.time()+30}, {'scope':'/subscriptions/'+SUB}):
            plan = dict(access.access_management_plan(self.session, TENANT), **changes)
            with self.assertRaises(Stop):access.enable_access_management(self.session, plan)
        self.transport.assert_not_called()

    def test_identity_change_between_review_and_apply_prevents_post(self):
        plan = access.access_management_plan(self.session, TENANT)
        self.session.token = token(oid=P2)
        with self.assertRaisesRegex(Stop, 'identity changed'):
            access.enable_access_management(self.session, plan)
        self.transport.assert_not_called()

    def test_wrong_tenant_cloud_account_type_and_signed_out_are_blocked(self):
        for field, value in [('cloud', 'AzureUSGovernment'), ('signed_in', False),
                             ('account', dict(tenantId=SUB, user=dict(type='user'))),
                             ('account', dict(tenantId=TENANT, user=dict(type='servicePrincipal')))]:
            session = Session(); setattr(session, field, value)
            with self.subTest(field=field), self.assertRaises(Stop):
                access.access_management_plan(session, TENANT)
        self.transport.assert_not_called()

    def test_wrong_token_tenant_audience_identity_and_expiry_are_blocked(self):
        for changes in ({'tid':SUB}, {'aud':'https://graph.microsoft.com'}, {'idtyp':'app'},
                        {'oid':''}, {'exp':time.time()-1}):
            self.session.token = token(**changes)
            with self.subTest(changes=changes), self.assertRaises(Stop):
                access.access_management_plan(self.session, TENANT)
        self.transport.assert_not_called()

    def test_unreadable_token_fails_closed(self):
        self.session.token = 'opaque-synthetic-token'
        with self.assertRaisesRegex(Stop, 'confirm the ARM token identity'):
            access.access_management_plan(self.session, TENANT)
        self.transport.assert_not_called()

    def test_permission_denied_explains_global_admin_requirement(self):
        plan = access.access_management_plan(self.session, TENANT)
        self.opener.open.side_effect = HTTPError(access.ENDPOINT, 403, 'Forbidden', {}, io.BytesIO(b'{}'))
        with self.assertRaisesRegex(Stop, 'Global Administrator'):
            access.enable_access_management(self.session, plan)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_unauthorized_requests_mfa_without_replaying_post_or_exposing_token(self):
        plan = access.access_management_plan(self.session, TENANT)
        self.opener.open.side_effect = HTTPError(access.ENDPOINT, 401, 'Unauthorized', {},
                                                 io.BytesIO(self.session.token.encode()))
        with self.assertRaises(AuthenticationRequired) as caught:
            access.enable_access_management(self.session, plan)
        self.assertEqual(caught.exception.tenant, TENANT)
        self.assertNotIn(self.session.token, str(caught.exception))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_timeout_has_unknown_outcome_and_no_automatic_retry(self):
        plan = access.access_management_plan(self.session, TENANT)
        self.opener.open.side_effect = URLError('timed out')
        with self.assertRaisesRegex(Stop, 'may have succeeded'):
            access.enable_access_management(self.session, plan)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_redirects_cannot_forward_authorization(self):
        self.assertIsNone(access.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.test'))


class SubscriptionlessLoginTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.session = backend.Session(Path(folder.name))
        self.account = dict(id=TENANT, tenantId=TENANT, name='Tenant account', user=dict(type='user'))

    def test_opt_in_allows_tenant_account_but_normal_login_keeps_diagnostic(self):
        with patch.object(self.session, 'az', return_value=[self.account]):
            self.assertEqual(self.session.login(TENANT, allow_empty=True), [])
            with self.assertRaisesRegex(Stop, 'no accessible Azure subscriptions'):
                self.session.login(TENANT)

    def test_empty_login_or_wrong_tenant_never_reaches_review(self):
        for rows in ([], [dict(self.account, tenantId=SUB)]):
            with patch.object(self.session, 'az', return_value=rows), self.assertRaises(Stop):
                self.session.login(TENANT, allow_empty=True)

    def test_mfa_and_cancellation_warnings_are_not_suppressed(self):
        for warning, error in [('AADSTS50076', AuthenticationRequired), ('user_cancelled', SignInCancelled)]:
            def login(*args):
                self.session.login_diagnostics = warning
                return [self.account]
            with patch.object(self.session, 'az', side_effect=login), self.assertRaises(error):
                self.session.login(TENANT, allow_empty=True)
