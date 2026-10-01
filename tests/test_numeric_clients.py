"""Exercise YAML round trips and the reviewed private-pipeline validation function."""
import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml
from sentinel_app import lighthouse_onboarding as lh
from sentinel_app import full_onboarding as legacy
from sentinel_app.repository_catalog import catalog

ROOT = Path(__file__).resolve().parents[1]


def pipeline_loader():
    # Only the reviewed validator and its regex constants run. Never import the
    # uploaded source tree or its CLI, network helpers or rule processing code.
    tree = ast.parse((ROOT / "detection-as-code/pipeline.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "load_catalog"
             or isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id in
                 {"SLUG", "SLUG_MAXIMUM", "AZURE_FIELDS"} for n in node.targets)]
    scope = dict(re=re, ROOT=ROOT, load_yaml=lambda path: yaml.safe_load(path.read_text(encoding="utf-8")))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "reviewed-pipeline-validator", "exec"), scope)
    return scope["load_catalog"]


class NumericClientsTests(unittest.TestCase):
    def manifest(self, client, onboard=lh.Onboard, label="workspace"):
        run = object.__new__(onboard)
        run.c = dict(client=client, target=lh.validate_target(client, label),
                     initial_rule_path="", allow_missing_mitre=True,
                     tenant_id="tenant", managing_tenant_id="manager", subscription_id="sub",
                     resource_group="rg", workspace_name="ws")
        run.workspace = dict(customerId="workspace")
        return run.manifest()

    def test_identifiers_round_trip_and_pass_pipeline_and_app_catalog(self):
        for client in ("413", "0413", "7", "413-client", "on", "null", "true", "a" * 54):
            with self.subTest(client=client), tempfile.TemporaryDirectory() as folder:
                raw = self.manifest(client)
                data = yaml.safe_load(raw)
                self.assertEqual(data["client"], client)
                self.assertEqual(data["target"], client + "-workspace")
                root = Path(folder)
                path = root / "clients" / client / "workspace.yml"
                path.parent.mkdir(parents=True)
                path.write_text(raw, encoding="utf-8")
                self.assertIn(client + "-workspace", pipeline_loader()(root))
                result = catalog({path.relative_to(root).as_posix(): raw.encode()}, "test", "revision")
                self.assertFalse(result["issues"])
                self.assertEqual(result["clients"][0]["name"], client)

    def test_legacy_generator_preserves_numeric_identity(self):
        for client in ("413", "0413", "off"):
            self.assertEqual(yaml.safe_load(self.manifest(client, legacy.Onboard))["client"], client)

    def test_existing_ordinary_manifest_spelling_stays_compatible(self):
        for onboard in (lh.Onboard, legacy.Onboard):
            raw = self.manifest("existing-client", onboard)
            self.assertIn("\nclient: existing-client\n", raw)
            self.assertIn("\ntarget: existing-client-workspace\n", raw)

    def test_pipeline_rejects_invalid_names_and_explains_unquoted_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "clients/413/workspace.yml"
            path.parent.mkdir(parents=True)
            for field, value in (("client", 413), ("client", True), ("client", None),
                                 ("target", "a" * 65), ("target", "413--workspace"),
                                 ("target", "413-Workspace"), ("target", "413-"),
                                 ("target", "../413"), ("client", "")):
                data = yaml.safe_load(self.manifest("413")); data[field] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "Quote numeric YAML values"):
                    pipeline_loader()(root)

    def test_pipeline_still_checks_folder_and_target_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "clients/wrong/workspace.yml"
            path.parent.mkdir(parents=True); path.write_text(self.manifest("413"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must agree"):
                pipeline_loader()(root)
