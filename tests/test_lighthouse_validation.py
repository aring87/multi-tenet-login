"""Target naming boundaries: no Azure or GitHub calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from sentinel_app import lighthouse_onboarding as lh

BASE = Path(__file__).resolve().parents[1]

class TargetValidationTests(unittest.TestCase):
    def config(self, client="example", label="primary"):
        config=json.loads((BASE/"examples/lighthouse-onboarding.example.json").read_text(encoding="utf-8"))
        config.update(client=client,workspace_label=label)
        return config

    def test_budget_matches_actual_deployment_prefix(self):
        self.assertEqual(len(lh.DEPLOYMENT_PREFIX),19)
        self.assertEqual(lh.TARGET_MAXIMUM,64)
        self.assertEqual(lh.CLIENT_MAXIMUM,62)

    def test_exact_limit_and_one_character_other_half(self):
        for client,label in (("a"*62,"b"),("a","b"*62),("a"*31,"b"*32)):
            with self.subTest(client=client,label=label):
                target=lh.validate_config(self.config(client,label))["target"]
                self.assertEqual(len(target),64)
                self.assertEqual(len(lh.deployment_name(target)),64)

    def test_combined_overflow_reports_actual_value_and_budget(self):
        for client,label in (("a"*32,"b"*32),("a"*62,"bb")):
            target=client+"-"+label
            with self.subTest(target=target),self.assertRaises(lh.Stop) as error:
                lh.validate_config(self.config(client,label))
            message=str(error.exception)
            self.assertIn(repr(target),message)
            self.assertIn(f"is {len(target)} characters",message)
            self.assertIn("limit is 64",message)
            self.assertIn(f"at least {len(target)-64} character(s)",message)
            self.assertIn("including the separating hyphen",message)

    def test_existing_client_slug_can_keep_full_label(self):
        client="explosive-countermeasures-international"
        for label in ("main","primary","workspace"):
            self.assertEqual(lh.validate_target(client,label),client+"-"+label)
        self.assertEqual(lh.validate_target("example-client","primary"),"example-client-primary")

    def test_field_overflow_is_distinct_from_character_error(self):
        with self.assertRaisesRegex(lh.Stop,"62 characters or fewer"):
            lh.validate_target("a"*63,"b")
        for value in ("Upper","double--dash","-first","last-","space here","",None,123):
            with self.subTest(value=value),self.assertRaisesRegex(lh.Stop,"lowercase hyphenated"):
                lh.validate_target(value,"primary")

    def test_deployment_validate_and_create_share_bounded_name(self):
        with tempfile.TemporaryDirectory() as folder:
            cli=MagicMock();cli.apply=True
            cli.az.return_value={"properties":{"provisioningState":"Succeeded"}}
            run=lh.Onboard(self.config("a"*62,"b"),cli,Path(folder)/"state.json",BASE/"templates/lighthouse-onboard.json")
            with patch.object(run,"verify_delegation"):
                run.deploy_delegation()
            calls=cli.az.call_args_list
            self.assertEqual([c.args[:3] for c in calls],[("deployment","sub","validate"),("deployment","sub","create")])
            for call in calls:
                name=call.args[call.args.index("--name")+1]
                self.assertEqual(name,lh.deployment_name(run.c["target"]))
                self.assertEqual(len(name),64)

    def test_overlong_target_stops_before_cloud_or_state_write(self):
        with tempfile.TemporaryDirectory() as folder:
            cli=MagicMock();state=Path(folder)/"state.json"
            with self.assertRaises(lh.Stop):
                lh.Onboard(self.config("a"*40,"b"*30),cli,state,BASE/"templates/lighthouse-onboard.json")
            cli.az.assert_not_called();cli.gh.assert_not_called()
            self.assertFalse(state.exists())

    def test_short_deployment_names_are_unchanged(self):
        for target in ("example-primary", "a"*45):
            self.assertEqual(lh.deployment_name(target),lh.DEPLOYMENT_PREFIX+target)

    def test_long_deployment_names_are_stable_and_distinguish_suffixes(self):
        first="a"*62+"-b"
        second="a"*62+"-c"
        self.assertEqual(lh.deployment_name(first),lh.deployment_name(first))
        self.assertNotEqual(lh.deployment_name(first),lh.deployment_name(second))
        for length in range(46,65):
            name=lh.deployment_name("a"*length)
            self.assertLessEqual(len(name),64)
            self.assertRegex(name,r"^lighthouse-onboard-[a-z0-9-]+-[a-f0-9]{16}$")

    def test_manifest_and_client_path_keep_full_target(self):
        with tempfile.TemporaryDirectory() as folder:
            config=self.config("explosive-countermeasures-international","workspace")
            cli=MagicMock();cli.apply=False
            run=lh.Onboard(config,cli,Path(folder)/"state.json",BASE/"templates/lighthouse-onboard.json")
            run.workspace={"customerId":"77777777-7777-4777-8777-777777777777"}
            self.assertIn("target: "+config["client"]+"-workspace",run.manifest())
            self.assertEqual(run.c["client"],config["client"])
