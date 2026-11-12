import unittest
from unittest.mock import patch
from sentinel_app.desktop_backend import Stop
from sentinel_app.workspace_tools import permits, access_plan, apply_contributor, rule_values, list_rules, read_rule

TARGET=dict(tenant_id="11111111-1111-1111-1111-111111111111",subscription_id="22222222-2222-2222-2222-222222222222")
USER="33333333-3333-3333-3333-333333333333"
class Session:
    def __init__(self,actions):self.actions=actions;self.calls=[];self.principal=USER
    def verify(self,target):return dict(user=dict(type="user",name="admin@example.com"))
    def log(self,text):pass
    def az(self,*args):
        self.calls.append(args)
        if args[:2]==("cloud","show"):return dict(name="AzureCloud")
        if args[:2]==("ad","signed-in-user"):return dict(id=self.principal)
        if args[0]=="rest":return dict(value=[dict(actions=self.actions,notActions=[])])
        return {}

class Tests(unittest.TestCase):
    def test_not_actions_and_union(self):
        blocks=[dict(actions=["*"],notActions=["Microsoft.Authorization/*/write"])]
        self.assertFalse(permits(blocks,"Microsoft.Authorization/roleAssignments/write"))
        self.assertTrue(permits(blocks,"Microsoft.ManagedServices/register/action"))
        blocks.append(dict(actions=["Microsoft.Authorization/*"],notActions=[]))
        self.assertTrue(permits(blocks,"Microsoft.Authorization/roleAssignments/write"))
    def test_reader_cannot_assign(self):
        with self.assertRaises(Stop):access_plan(Session(["*/read"]),TARGET)
    def test_owner_needs_no_assignment(self):
        s=Session(["*"]);plan=access_plan(s,TARGET)
        self.assertFalse(plan["needs_role"])
        apply_contributor(s,plan)
        self.assertFalse(any(c[:3]==("role","assignment","create") for c in s.calls))
    def test_reviewed_assignment_is_scoped_to_current_user(self):
        s=Session(["Microsoft.Authorization/roleAssignments/write"])
        plan=access_plan(s,TARGET);apply_contributor(s,plan)
        call=next(c for c in s.calls if c[:3]==("role","assignment","create"))
        self.assertEqual(call[call.index("--scope")+1],"/subscriptions/"+TARGET["subscription_id"])
        self.assertEqual(call[call.index("--assignee-object-id")+1],USER)
    def test_identity_change_blocks_write(self):
        s=Session(["Microsoft.Authorization/roleAssignments/write"]);plan=access_plan(s,TARGET)
        s.principal="44444444-4444-4444-4444-444444444444"
        with self.assertRaises(Stop):apply_contributor(s,plan)
        self.assertFalse(any(c[0]=="role" for c in s.calls))
    def test_expired_review_blocks_write(self):
        s=Session(["*"]);plan=access_plan(s,TARGET);plan["created"]=0
        with self.assertRaises(Stop):apply_contributor(s,plan)
    def test_rule_states_do_not_treat_missing_as_disabled(self):
        for value,expected in ((True,"Enabled"),(False,"Disabled"),(None,"Not specified")):
            self.assertEqual(rule_values(dict(properties=dict(enabled=value)))[1],expected)
    def test_rules_collection_verifies_workspace_and_keeps_disabled(self):
        result=dict(records=[dict(properties=dict(enabled=False))],status="collected")
        with patch("sentinel_app.workspace_tools.Collector") as cls:
            collector=cls.return_value;collector.base="https://management.azure.com/workspace"
            collector.collect.return_value=result
            self.assertIs(list_rules(object(),{}),result)
            collector.verify_workspace.assert_called_once()
            self.assertIn("/alertRules?",collector.collect.call_args.args[2])
    def test_one_rule_is_read_again_at_the_requested_api_version(self):
        rule_id="5D0C9A51-2E4B-4C3F-9A7E-1B2C3D4E5F60"
        with patch("sentinel_app.workspace_tools.Collector") as cls:
            collector=cls.return_value;collector.base="https://management.azure.com/workspace"
            collector.get.return_value=dict(name=rule_id,kind="Scheduled",properties={})
            self.assertEqual(read_rule(object(),{},rule_id,"2023-12-01-preview")["kind"],"Scheduled")
            collector.verify_workspace.assert_called_once()
            url=collector.get.call_args.args[0]
            self.assertTrue(url.endswith("/alertRules/"+rule_id.lower()+"?api-version=2023-12-01-preview"),url)
            collector.get.return_value=dict(name="another-rule")  # Sentinel answered for a different rule.
            with self.assertRaises(Stop):read_rule(object(),{},rule_id,"2023-12-01-preview")
        for bad_id,version in (("../x","2023-12-01-preview"),(rule_id,"latest&x=1")):
            with self.assertRaises(Stop):read_rule(object(),{},bad_id,version)
if __name__=="__main__":unittest.main()
