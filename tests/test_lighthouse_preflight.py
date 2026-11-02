"""Preflight authorisation checks: no Azure or GitHub calls."""
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from sentinel_app import lighthouse_onboarding as lh

AUTHORIZATION_ERROR = ('{"code": "AuthorizationFailed", "message": "The client '
                       '\'a_jit@example.com\' with object id \'62651bcc\' does not have '
                       "authorization to perform action "
                       "'Microsoft.Resources/deployments/validate/action' over scope "
                       '\'/subscriptions/s\' or the scope is invalid."}')


class TokenAuthorizationTests(unittest.TestCase):
    """Validate permissions without diagnosing every denial as a stale token."""

    def onboard(self, az):
        instance = object.__new__(lh.Onboard)
        instance.io = MagicMock()
        instance.io.az = az
        instance.c = {"subscription_id": "s", "tenant_id": "t", "delegation_location": "eastus"}
        instance.state = {"caller_roles": ["Contributor", "User Access Administrator"]}
        return instance

    def test_probe_validates_a_template_that_deploys_nothing(self):
        seen = {}

        def az(*args, **kwargs):
            seen["args"] = args
            seen["template"] = json.loads(
                Path(args[args.index("--template-file") + 1]).read_text(encoding="utf-8"))

        self.onboard(az).check_token_authorization()
        self.assertEqual(seen["args"][:3], ("deployment", "sub", "validate"))
        self.assertEqual(seen["template"]["resources"], [])
        self.assertIn("subscriptionDeploymentTemplate", seen["template"]["$schema"])

    def test_probe_never_writes(self):
        az = MagicMock(return_value=None)
        self.onboard(az).check_token_authorization()
        self.assertNotIn("write", az.call_args.kwargs)

    def test_authorization_failure_preserves_evidence_and_explains_possible_causes(self):
        def az(*args, **kwargs):
            raise lh.Stop(AUTHORIZATION_ERROR)

        with self.assertRaises(lh.Stop) as error:
            self.onboard(az).check_token_authorization()
        message = str(error.exception)
        self.assertIn("Contributor, User Access Administrator", message)
        self.assertIn("Possible causes", message)
        self.assertIn("desktop app", message)
        self.assertIn("Active rather than Eligible", message)
        self.assertIn("Original Azure error: " + AUTHORIZATION_ERROR, message)


    def test_unrelated_failure_is_reported_unchanged(self):
        def az(*args, **kwargs):
            raise lh.Stop("InvalidTemplateDeployment: the template is malformed.")

        with self.assertRaises(lh.Stop) as error:
            self.onboard(az).check_token_authorization()
        self.assertIn("InvalidTemplateDeployment", str(error.exception))
        self.assertNotIn("az logout", str(error.exception))


class DelegationRightsTests(unittest.TestCase):
    """The caller's roles are read by object ID from the sign-in token, so the check
    works for guest and GCC High accounts whose Graph lookups may be refused."""

    OBJECT_ID = "62651bcc-1111-4111-8111-111111111111"

    def token(self):
        claims = base64.urlsafe_b64encode(json.dumps({"oid": self.OBJECT_ID}).encode()).decode().rstrip("=")
        return "header." + claims + ".signature"

    def rights(self, assignments):
        instance = object.__new__(lh.Onboard)
        instance.io = MagicMock()
        instance.io.az = MagicMock(side_effect=[{"accessToken": self.token()}, assignments])
        instance.c = {"tenant_id": "t"}
        instance.scope = "/subscriptions/s"
        instance.state = {}
        return instance

    def test_roles_are_read_by_object_id_without_a_graph_lookup(self):
        instance = self.rights([{"roleDefinitionName": "User Access Administrator"}])
        instance.check_delegation_rights()
        token_call, roles_call = [call[0] for call in instance.io.az.call_args_list]
        self.assertEqual(token_call[:2], ("account", "get-access-token"))
        self.assertIn("--include-inherited", roles_call)
        self.assertIn("--assignee-object-id", roles_call)
        self.assertIn(self.OBJECT_ID, roles_call)
        self.assertNotIn("--assignee", roles_call)
        self.assertEqual(instance.state["caller_roles"], ["User Access Administrator"])

    def test_contributor_alone_is_refused(self):
        with self.assertRaises(lh.Stop) as error:
            self.rights([{"roleDefinitionName": "Contributor"}]).check_delegation_rights()
        self.assertIn("Owner or User Access Administrator", str(error.exception))


if __name__ == "__main__":
    unittest.main()
