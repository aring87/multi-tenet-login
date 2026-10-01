import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sentinel_app.rule_export import export_rules


class RuleExportTests(unittest.TestCase):
    def test_zip_preserves_all_raw_data_and_provenance_and_escapes_csv(self):
        rows = [dict(name="a", kind="Scheduled", properties=dict(displayName="=unsafe()", enabled=True,
                query="SecurityEvent\n| take 5", tactics=["Discovery"], extra=dict(value="café"))),
                dict(name="b", properties=dict(displayName="Disabled", enabled=False)),
                dict(name="c", kind="Fusion", properties={})]
        workspace = dict(tenant_id="tenant", subscription_id="sub", workspace_id="workspace")
        collection = dict(records=rows, status="collected", collected_at_utc="2026-10-01T01:02:03Z",
                          source="https://management.azure.com/example", requests=[dict(url="page2")])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.zip"
            manifest = export_rules(path, workspace, collection)
            with zipfile.ZipFile(path) as z:
                self.assertEqual(set(z.namelist()), {"rules.json", "rules.csv", "manifest.json"})
                self.assertEqual(json.loads(z.read("rules.json")), dict(workspace=workspace, collection=collection))
                self.assertEqual(json.loads(z.read("manifest.json")), manifest)
                for name, digest in manifest["files"].items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), digest)
                records = list(csv.DictReader(io.StringIO(z.read("rules.csv").decode("utf-8-sig"))))
                self.assertEqual([r["state"] for r in records], ["Enabled", "Disabled", "Not specified"])
                self.assertEqual(records[0]["rule_name"], "'=unsafe()")
                self.assertEqual(records[0]["raw.properties.query"], rows[0]["properties"]["query"])
                self.assertEqual(records[0]["raw.properties.extra.value"], "café")
            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["record_count"], 3)
            self.assertEqual(manifest["collection"]["collected_at_utc"], collection["collected_at_utc"])

    def test_partial_inventory_retains_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = export_rules(Path(folder) / "partial.zip", {},
                dict(records=[dict(name="one")], status="partial", error="Page 2 access denied"))
            self.assertFalse(manifest["complete"])
            self.assertIn("Page 2", manifest["collection"]["error"])

    def test_empty_success_is_distinct_from_failed_collection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.zip"
            manifest = export_rules(path, {}, dict(records=[], status="no_records"))
            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["record_count"], 0)
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Refresh rules"):
                export_rules(path, {}, dict(records=[], status="error"))
            self.assertEqual(path.read_bytes(), original)

    def test_failed_write_preserves_existing_export_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.zip"; path.write_bytes(b"previous")
            with patch("sentinel_app.rule_export.zipfile.ZipFile.writestr", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    export_rules(path, {}, dict(records=[], status="no_records"))
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertEqual(list(Path(folder).iterdir()), [path])
