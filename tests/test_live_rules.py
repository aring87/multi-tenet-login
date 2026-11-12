"""A live Sentinel rule as a new library rule, proven exact: offline, synthetic records only."""
import copy
import unittest

import yaml

from sentinel_app import live_rules, rule_drafts as drafts, rule_schema
from sentinel_app.rule_review_dialog import new_rule_path
from test_rule_drafts import valid_form

RULE_ID = "5d0c9a51-2e4b-4c3f-9a7e-1b2c3d4e5f60"
TEMPLATE = "0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"


def library_rule(**changes):
    """A valid library rule, as the Rule builder would write it."""
    form = valid_form()
    form.update(id=RULE_ID, owner="security-engineering", enabled=True, createIncident=True)
    rule = drafts.to_rule(form)
    rule.update(changes)
    return rule


def live(rule=None, kind=None, **props):
    """The REST record Sentinel returns at the pipeline's API version for ``rule`` deployed by the pipeline."""
    arm = rule_schema.to_arm(rule or library_rule())
    properties = dict(arm["properties"], lastModifiedUtc="2026-11-12T10:00:00Z")
    properties.update(props)
    return dict(id="/subscriptions/x/providers/Microsoft.SecurityInsights/alertRules/" + RULE_ID.upper(),
                name=RULE_ID.upper(), etag='"0"', type="Microsoft.SecurityInsights/alertRules",
                kind=kind or arm["kind"], properties=properties)


class ConvertTests(unittest.TestCase):
    def test_a_deployed_library_rule_converts_back_to_itself(self):
        original = library_rule(entityMappings=[dict(entityType="Account", fieldMappings=[dict(identifier="Name",
                                                                                                columnName="User")])],
                                alertRuleTemplateName=TEMPLATE, templateVersion="1.0.2",
                                groupingConfiguration=dict(enabled=True, reopenClosedIncident=False, lookbackDuration="PT5H",
                                                           matchingMethod="AllEntities", groupByEntities=[],
                                                           groupByAlertDetails=[], groupByCustomDetails=[]))
        result = live_rules.convert(live(original))
        rule = result["rule"]
        self.assertEqual(rule["id"], RULE_ID)  # The live GUID, in lower case: Drift matches by it.
        self.assertEqual((rule["owner"], rule["status"], rule["kind"]), ("security-engineering", "baseline", "Scheduled"))
        self.assertEqual(rule_schema.to_arm(rule), rule_schema.to_arm(original))
        self.assertEqual((result["notes"], result["fixes"]), ([], []))
        self.assertNotIn("lastModifiedUtc", rule)

    def test_a_record_read_at_a_newer_api_version_is_refused(self):
        record = live(); del record["properties"]["subTechniques"]  # As 2025-06-01 returns it.
        with self.assertRaises(live_rules.NotConvertible) as caught: live_rules.convert(record)
        self.assertIn("properties.subTechniques: added by pipeline", str(caught.exception))

    def test_anything_the_pipeline_would_change_is_refused(self):
        for change, expected in ((dict(displayNamesFilter=["x"]), "properties.displayNamesFilter: omitted by pipeline"),
                                 (dict(suppressionDuration=None), "properties.suppressionDuration: added by pipeline"),
                                 # It would deploy unchanged, but the Rule builder can't hold it.
                                 (dict(triggerThreshold="0"), "The Rule builder can't open it: triggerThreshold must be an integer")):
            with self.subTest(change=change):
                with self.assertRaises(live_rules.NotConvertible) as caught: live_rules.convert(live(**change))
                self.assertIn(expected, str(caught.exception))
        record = live(); record["properties"]["incidentConfiguration"]["extra"] = True
        with self.assertRaises(live_rules.NotConvertible) as caught: live_rules.convert(record)
        self.assertIn("properties.incidentConfiguration.extra: omitted by pipeline", str(caught.exception))

    def test_named_allowances_are_applied_to_both_sides_and_reported(self):
        record = live(customDetails={}, description="  Find synthetic events for an offline test.\n",
                      techniques=["T1562.004", "T1078"], subTechniques=[])
        result = live_rules.convert(record)
        rule = result["rule"]
        self.assertNotIn("customDetails", rule)
        self.assertEqual((rule["techniques"], rule["subTechniques"]), (["T1078", "T1562"], ["T1562.004"]))
        self.assertEqual(len(result["notes"]), 3)
        self.assertTrue(any("customDetails" in note for note in result["notes"]))
        self.assertTrue(any("T1562.004" in note for note in result["notes"]))
        self.assertTrue(any("description" in note for note in result["notes"]))

    def test_nrt_rules_convert_without_a_schedule(self):
        nrt = library_rule(kind="NRT")
        for key in live_rules.SCHEDULE: nrt.pop(key, None)
        rule = live_rules.convert(live(nrt))["rule"]
        self.assertEqual(rule["kind"], "NRT")
        self.assertFalse(set(live_rules.SCHEDULE) & set(rule))
        with self.assertRaises(live_rules.NotConvertible) as caught: live_rules.convert(live(nrt, queryFrequency="PT1H"))
        self.assertIn("NRT rule with schedule settings", str(caught.exception))

    def test_what_the_library_asks_for_comes_back_as_fixes(self):
        short = live_rules.convert(live(description="Too short"))
        self.assertEqual(short["fixes"], ["Description must be at least 20 characters."])
        untagged = live_rules.convert(live(library_rule(tactics=[], techniques=[], subTechniques=[])))
        self.assertEqual(len(untagged["fixes"]), 1); self.assertIn("MITRE", untagged["fixes"][0])

    def test_kinds_and_ids_the_library_cannot_hold(self):
        self.assertIn("Fusion rules have no query", live_rules.why_not(dict(name=RULE_ID, kind="Fusion")))
        self.assertIn("Microsoft incident-creation", live_rules.why_not(dict(name=RULE_ID, kind="MicrosoftSecurityIncidentCreation")))
        self.assertIn("Scheduled and NRT rules only", live_rules.why_not(dict(name=RULE_ID, kind="Something")))
        self.assertIn("isn't a GUID", live_rules.why_not(dict(name="BuiltInFusion", kind="Scheduled")))
        self.assertIsNone(live_rules.why_not(live()))
        record = live(); del record["properties"]["queryPeriod"]
        with self.assertRaises(live_rules.NotConvertible) as caught: live_rules.convert(record)
        self.assertIn("didn't return queryPeriod", str(caught.exception))

    def test_shared_objects_never_reach_the_builder_as_yaml_aliases(self):
        record = live()
        shared = []
        grouping = record["properties"]["incidentConfiguration"]["groupingConfiguration"]
        grouping["groupByEntities"] = grouping["groupByAlertDetails"] = shared
        rule = live_rules.convert(record)["rule"]
        form = drafts.from_rule(rule)
        self.assertNotIn("&id", form["advanced"])
        self.assertEqual(rule_schema.to_arm(drafts.to_rule(form)), rule_schema.to_arm(rule))  # What the builder submits.

    def test_a_character_yaml_cannot_keep_is_refused_by_the_proof_and_the_builder(self):
        # YAML reads a raw U+0085 back as a space, so the library file would deploy a different query.
        for key in ("query", "description", "displayName"):
            with self.subTest(key=key):
                value = live()["properties"][key] + " x\u0085y"
                with self.assertRaises(live_rules.NotConvertible) as caught: live_rules.convert(live(**{key: value}))
                self.assertIn("Saving it as a library file would change it", str(caught.exception))
        form = valid_form(); form["query"] += " | where Message has 'a\u0085b'"
        with self.assertRaises(ValueError) as caught: drafts.validated_yaml(form)
        self.assertIn("U+0085", str(caught.exception))
        clean = valid_form()  # Each form has its own rule ID, so one form for both sides.
        self.assertEqual(drafts.validated_yaml(clean)[0], drafts.dump(drafts.to_rule(clean)))

    def test_only_well_formed_sub_technique_ids_move(self):
        result = live_rules.convert(live(techniques=["T1078.001", "T1059.1"], subTechniques=[]))
        rule = result["rule"]
        self.assertEqual((rule["techniques"], rule["subTechniques"]), (["T1059.1", "T1078"], ["T1078.001"]))
        self.assertIn("T1078.001", result["notes"][0]); self.assertNotIn("T1059.1", result["notes"][0])
        self.assertEqual(len(result["fixes"]), 1)
        self.assertIn("Techniques accepts parent IDs", result["fixes"][0])  # The library asks for it to be fixed.

    def test_differences_is_strict_about_types_and_list_items(self):
        self.assertEqual(live_rules.differences([True], [1]), ["properties[0]: value/type differs"])
        self.assertEqual(live_rules.differences(dict(a=[1, 2]), dict(a=[1])), ["properties.a: value differs"])
        self.assertEqual(live_rules.differences(dict(a=1), dict(a=1)), [])


class NewRulePathTests(unittest.TestCase):
    def test_first_tactic_folder_or_uncategorized(self):
        form = dict(name="Brute force: Entra ID!", tactics="CommandAndControl, InitialAccess")
        self.assertEqual(new_rule_path(form), "rules/sentinel/command-and-control/brute-force-entra-id.yml")
        self.assertEqual(new_rule_path(dict(form, tactics="")), "rules/sentinel/uncategorized/brute-force-entra-id.yml")
        self.assertEqual(new_rule_path(dict(form, tactics="../etc")), "rules/sentinel/uncategorized/brute-force-entra-id.yml")
        self.assertEqual(new_rule_path(dict(name="!!!", tactics="Impact")), "rules/sentinel/impact/new-rule.yml")


if __name__ == "__main__":
    unittest.main()
