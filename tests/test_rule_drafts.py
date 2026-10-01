import copy
import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sentinel_app import rule_drafts as drafts
from sentinel_app import rule_schema
from sentinel_app.repository_catalog import parse_yaml


def valid_form():
    form = drafts.new_form()
    form.update(name="Synthetic test detection", description="Find synthetic events for an offline test.",
                owner="test-team", query="SyntheticEvents | where Example == true",
                tactics="DefenseEvasion", techniques="T1562", subTechniques="T1562.004")
    return form


class RuleDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rule.yml"

    def test_new_rule_is_disabled_and_has_distinct_identity(self):
        first, second = drafts.new_form(), drafts.new_form()
        self.assertFalse(first["enabled"]); self.assertFalse(first["createIncident"])
        self.assertTrue(rule_schema.guid(first["id"]))
        self.assertNotEqual(first["id"], second["id"])

    def test_scheduled_yaml_compiles_and_preserves_advanced_properties(self):
        form = valid_form()
        form["advanced"] = "entityMappings: [{entityType: Host, fieldMappings: [{identifier: FullName, columnName: HostName}]}]\ncustomDetails: {Example: ColumnName}\nsuppressionDuration: PT2H\nsuppressionEnabled: true\n"
        rule = drafts.to_rule(form)
        imported = drafts.from_rule(rule)
        self.assertEqual(drafts.to_rule(imported), rule)
        text, warnings = drafts.validated_yaml(imported)
        self.assertFalse(warnings)
        output = rule_schema.to_arm(parse_yaml(text.encode()))["properties"]
        self.assertEqual(output["entityMappings"], rule["entityMappings"])
        self.assertEqual(output["customDetails"], {"Example": "ColumnName"})
        self.assertEqual(output["suppressionDuration"], "PT2H")
        self.assertEqual(drafts.to_rule(imported)["id"], form["id"])

    def test_nrt_export_omits_schedule_and_threshold(self):
        form = valid_form(); form["kind"] = "NRT"; form["triggerThreshold"] = "unused"
        text, warnings = drafts.validated_yaml(form)
        rule = parse_yaml(text.encode())
        self.assertFalse(drafts.SCHEDULE_FIELDS & rule.keys())
        self.assertEqual(rule_schema.to_arm(rule)["kind"], "NRT")

    def test_invalid_exports_leave_existing_file_unchanged(self):
        self.path.write_text("original")
        cases = [("queryFrequency", "PT1M"), ("queryPeriod", "PT5M"),
                 ("triggerThreshold", "-1"), ("triggerThreshold", "1.5"),
                 ("query", "SyntheticEvents |"), ("owner", "Has Spaces"),
                 ("techniques", "T1562.004"), ("subTechniques", "T1003.001")]
        for key, value in cases:
            form = valid_form(); form[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                drafts.export_rule(self.path, form)
            self.assertEqual(self.path.read_text(), "original")

    def test_mitre_exception_is_explicit_and_does_not_leak_into_rule(self):
        form = valid_form(); form["techniques"] = ""; form["subTechniques"] = ""
        with self.assertRaises(ValueError): drafts.validated_yaml(form)
        form["allow_missing_mitre"] = True
        text, warnings = drafts.validated_yaml(form)
        self.assertTrue(warnings)
        self.assertNotIn("allow_missing_mitre", parse_yaml(text.encode()))

    def test_retired_enabled_is_refused(self):
        form = valid_form(); form.update(status="retired", enabled=True)
        with self.assertRaisesRegex(ValueError, "Retired"): drafts.validated_yaml(form)

    def test_unknown_fields_survive_import_but_block_export(self):
        rule = drafts.to_rule(valid_form()); rule["futureProperty"] = {"keep": [1, 2]}
        form = drafts.from_rule(rule)
        self.assertEqual(drafts.to_rule(form)["futureProperty"], rule["futureProperty"])
        with self.assertRaisesRegex(ValueError, "Unknown rule fields"): drafts.validated_yaml(form)

    def test_advanced_fields_cannot_override_guided_inputs(self):
        form = valid_form(); form["advanced"] = "enabled: true\n"
        with self.assertRaisesRegex(ValueError, "guided fields"): drafts.validated_yaml(form)

    def test_incomplete_draft_round_trip_retains_invalid_input_and_id(self):
        form = drafts.new_form(); form["triggerThreshold"] = "later"; form["advanced"] = "unfinished: ["
        drafts.save_draft(self.path, form)
        self.assertEqual(drafts.read_draft(self.path), form)

    def test_wrong_draft_types_or_version_are_refused(self):
        form = valid_form(); form["enabled"] = "false"
        drafts.save_draft(self.path, form)
        with self.assertRaises(ValueError): drafts.read_draft(self.path)
        self.path.write_text('{"version": 2, "format": "sentinel-rule-draft", "form": {}}')
        with self.assertRaises(ValueError): drafts.read_draft(self.path)

    def test_unsafe_or_duplicate_yaml_refused(self):
        for raw in ("name: a\nname: b", "foo: &foo {bar: *foo}", "!!python/object:builtins.object {}"):
            self.path.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError): drafts.read_rule(self.path)

    def test_invalid_import_types_not_silently_coerced(self):
        rule = drafts.to_rule(valid_form()); rule["enabled"] = "false"
        with self.assertRaises(ValueError): drafts.from_rule(rule)
        rule["enabled"] = False; rule["triggerThreshold"] = True
        with self.assertRaises(ValueError): drafts.from_rule(rule)

    def test_failed_atomic_replace_preserves_original(self):
        self.path.write_text("keep")
        with patch.object(drafts.os, "replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError): drafts.export_rule(self.path, valid_form())
        self.assertEqual(self.path.read_text(), "keep")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_oversized_input_refused(self):
        self.path.write_bytes(b"x" * (drafts.MAX_FILE + 1))
        with self.assertRaises(ValueError): drafts.read_rule(self.path)

    def test_reviewed_schema_digest_matches_documented_version(self):
        root = Path(drafts.__file__).parent
        expected = (root.parent / "docs" / "RULE-SCHEMA.md").read_text().split("SHA-256: `")[1].split("`")[0]
        source = (root / "rule_schema.py").read_text(encoding="utf-8").encode("utf-8")
        self.assertEqual(hashlib.sha256(source).hexdigest(), expected)
