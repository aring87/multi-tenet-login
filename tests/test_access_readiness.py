import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentinel_app.access_readiness import AccessHistory, check_access, history_text
from sentinel_app.workspace_tools import access_plan, apply_contributor
from sentinel_app.auth_recovery import AuthenticationRequired, azure_error
from sentinel_app.desktop_backend import Stop
from test_workspace_tools import Session, TARGET


class AccessReadinessTests(unittest.TestCase):
    def test_provider_registration_alone_is_not_enough(self):
        s=Session(['Microsoft.ManagedServices/register/action','Microsoft.Authorization/roleAssignments/write'])
        self.assertTrue(access_plan(s,TARGET)['needs_role'])

    def test_ready_probe_checks_actual_validation_without_writes(self):
        s=Session(['*']); original=s.az
        def az(*args):
            if args[:3]==('deployment','sub','validate'):
                doc=json.loads(Path(args[args.index('--template-file')+1]).read_text())
                self.assertEqual(doc['resources'],[])
            return original(*args)
        s.az=az
        result=check_access(s,TARGET,'eastus')
        self.assertTrue(result['ready'])
        self.assertTrue(any(c[:3]==('deployment','sub','validate') for c in s.calls))
        self.assertFalse(any(c[0] in ('login','logout','role') for c in s.calls))

    def test_role_permissions_do_not_override_arm_denial(self):
        s=Session(['*']);original=s.az
        def az(*args):
            if args[0]=='deployment':raise Stop('AuthorizationFailed: validation denied')
            return original(*args)
        s.az=az
        result=check_access(s,TARGET,'eastus')
        self.assertFalse(result['ready']);self.assertIn('Azure still denies',result['detail'])

    def test_unrelated_errors_and_mfa_are_not_called_propagation(self):
        for error in (Stop('InvalidTemplate'),AuthenticationRequired('AADSTS50076')):
            s=Session(['*']);original=s.az
            def az(*args):
                if args[0]=='deployment':raise error
                return original(*args)
            s.az=az
            with self.assertRaises(type(error)):check_access(s,TARGET,'eastus')

    def test_missing_permission_still_blocks_after_probe_passes(self):
        result=check_access(Session(['Microsoft.Resources/deployments/validate/action']),TARGET,'eastus')
        self.assertFalse(result['ready']);self.assertIn('Microsoft.Resources/deployments/write',result['missing'])

    def test_permission_lookup_preserves_interactive_authentication(self):
        s=Session(['*']);original=s.az
        def az(*args):
            if args[0]=='rest':raise AuthenticationRequired('AADSTS50076')
            return original(*args)
        s.az=az
        with self.assertRaises(AuthenticationRequired):check_access(s,TARGET,'eastus')

    def test_assignment_details_distinguish_no_op_and_accepted(self):
        for actions,assigned in ((['*'],False),(['Microsoft.Authorization/roleAssignments/write'],True)):
            session=Session(actions)
            result=apply_contributor(session,access_plan(session,TARGET),details=True)
            self.assertEqual(result['assigned'],assigned)

    def test_timestamps_persist_and_first_click_is_not_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'access-history.json';history=AccessHistory(path)
            with patch('sentinel_app.access_readiness.time.time',return_value=1000):history.record(TARGET,'clicked')
            with patch('sentinel_app.access_readiness.time.time',return_value=1100):history.record(TARGET,'clicked')
            row=AccessHistory(path).get(TARGET)
            self.assertEqual(row['first_clicked'],1000);self.assertEqual(row['last_clicked'],1100)
            other=dict(TARGET,subscription_id='44444444-4444-4444-4444-444444444444')
            self.assertEqual(history.get(other),{})
            self.assertNotIn('propagation is possible',history_text(row,1100))
            with patch('sentinel_app.access_readiness.time.time',return_value=1100):history.record(TARGET,'accepted',ready=False)
            self.assertIn('not confirmed',history_text(history.get(TARGET),1200))
            self.assertNotIn('propagation is possible',history_text(history.get(TARGET),1800))

    def test_corrupt_history_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json';path.write_text('{broken')
            with self.assertRaises(Stop):AccessHistory(path).record(TARGET,'clicked')
            self.assertEqual(path.read_text(),'{broken')

    def test_missing_account_errors_have_tenant_guidance_and_original_code(self):
        for code in ('AADSTS50020','AADSTS50034','AADSTS51004'):
            error=azure_error(code+': user does not exist',TARGET['tenant_id'])
            self.assertNotIsInstance(error,AuthenticationRequired)
            self.assertIn('not Azure role propagation',str(error))
            self.assertIn(TARGET['tenant_id'],str(error));self.assertIn(code,str(error))


if __name__=='__main__':unittest.main()
