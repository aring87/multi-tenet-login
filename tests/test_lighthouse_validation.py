"""Target naming boundaries: no Azure or GitHub calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import lighthouse_onboarding as lh

BASE = Path(__file__).resolve().parents[1]

class TargetValidationTests(unittest.TestCase):
    def config(self, client="example", label="primary"):
        config=json.loads((BASE/"lighthouse-onboarding.example.json").read_text(encoding="utf-8"))
        config.update(client=client,workspace_label=label)
        return config

    def test_budget_matches_actual_deployment_prefix(self):
        self.assertEqual(len(lh.DEPLOYMENT_PREFIX),19)
        self.assertEqual(lh.TARGET_MAXIMUM,45)
        self.assertEqual(lh.CLIENT_MAXIMUM,43)

    def test_exact_limit_and_one_character_other_half(self):
        for client,label in (("a"*43,"b"),("a","b"*43),("a"*22,"b"*22)):
            with self.subTest(client=client,label=label):
                target=lh.validate_config(self.config(client,label))["target"]
                self.assertEqual(len(target),45)
                self.assertEqual(len(lh.DEPLOYMENT_PREFIX+target),64)

    def test_combined_overflow_reports_actual_value_and_budget(self):
        for client,label in (("a"*23,"b"*22),("explosive-countermeasures-international","workspace"),
                             ("explosive-countermeasures-international","primary")):
            target=client+"-"+label
            with self.subTest(target=target),self.assertRaises(lh.Stop) as error:
                lh.validate_config(self.config(client,label))
            message=str(error.exception)
            self.assertIn(repr(target),message)
            self.assertIn(f"is {len(target)} characters",message)
            self.assertIn("limit is 45",message)
            self.assertIn(f"at least {len(target)-45} character(s)",message)
            self.assertIn("Azure deployment names",message)

    def test_existing_client_slug_can_keep_shorter_label(self):
        client="explosive-countermeasures-international"
        self.assertEqual(lh.validate_target(client,"main"),client+"-main")
        self.assertEqual(lh.validate_target("example-client","primary"),"example-client-primary")

    def test_field_overflow_is_distinct_from_character_error(self):
        with self.assertRaisesRegex(lh.Stop,"43 characters or fewer"):
            lh.validate_target("a"*44,"b")
        for value in ("Upper","double--dash","-first","last-","space here","",None,123):
            with self.subTest(value=value),self.assertRaisesRegex(lh.Stop,"lowercase hyphenated"):
                lh.validate_target(value,"primary")

    def test_deployment_validate_and_create_share_bounded_name(self):
        with tempfile.TemporaryDirectory() as folder:
            cli=MagicMock();cli.apply=True
            cli.az.return_value={"properties":{"provisioningState":"Succeeded"}}
            run=lh.Onboard(self.config("a"*43,"b"),cli,Path(folder)/"state.json",BASE/"lighthouse-onboard.json")
            with patch.object(run,"verify_delegation"):
                run.deploy_delegation()
            calls=cli.az.call_args_list
            self.assertEqual([c.args[:3] for c in calls],[("deployment","sub","validate"),("deployment","sub","create")])
            for call in calls:
                name=call.args[call.args.index("--name")+1]
                self.assertEqual(name,lh.DEPLOYMENT_PREFIX+run.c["target"])
                self.assertEqual(len(name),64)

    def test_overlong_target_stops_before_cloud_or_state_write(self):
        with tempfile.TemporaryDirectory() as folder:
            cli=MagicMock();state=Path(folder)/"state.json"
            with self.assertRaises(lh.Stop):
                lh.Onboard(self.config("a"*30,"b"*20),cli,state,BASE/"lighthouse-onboard.json")
            cli.az.assert_not_called();cli.gh.assert_not_called()
            self.assertFalse(state.exists())
