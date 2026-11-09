"""GCC High onboarding tests use fake Azure and GitHub responses only."""
import base64
import contextlib
import io
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sentinel_app import desktop_backend as backend
from sentinel_app import gov_onboarding as gov
from sentinel_app import rule_assignments
from sentinel_app.lighthouse_onboarding import Stop

TENANT = "11111111-1111-4111-8111-111111111111"
SUB = "22222222-2222-4222-8222-222222222222"
WS = "33333333-3333-4333-8333-333333333333"
USER = "44444444-4444-4444-8444-444444444444"
RG = "rg-sentinel"
SCOPE = f"/subscriptions/{SUB}/resourceGroups/{RG}"
OWNER_ID, REPO_ID = 308037977, 1309226902


def token(oid=USER):
    claims = base64.urlsafe_b64encode(json.dumps({"oid": oid}).encode()).decode().rstrip("=")
    return "x." + claims + ".y"


def config(**changes):
    c = dict(tenant_id=TENANT, subscription_id=SUB, resource_group=RG, workspace_name="Sentinel-Instance",
             client="aosense", workspace_label="workspace-gcch", github_owner="isi-security",
             github_repo="detection-as-code", preview_environment="sentinel-preview",
             production_environment="sentinel-production",
             initial_rule_path="rules/sentinel/testing/pipeline-connection-test.yml", allow_missing_mitre=True)
    c.update(changes)
    return c


class FakeTenant:
    """Just enough Azure Government and GitHub to exercise every onboarding step."""

    def __init__(self, apply=False):
        self.apply = apply
        self.calls, self.writes = [], []
        self.cloud = "AzureUSGovernment"
        self.tenant = TENANT
        self.caller_roles = [{"roleDefinitionName": "User Access Administrator"}]
        self.apps, self.sps, self.creds, self.assignments = {}, {}, {}, []
        self.main_target = None
        self.pull = {"html_url": "https://github.com/isi-security/detection-as-code/pull/200"}
        self.counter = 0

    def new_id(self):
        self.counter += 1
        return f"{self.counter:08d}-0000-4000-8000-000000000000"

    def az(self, *args, write=False):
        self.calls.append(("az", args, write))
        if write:
            assert self.apply, "write without apply"
            self.writes.append(args)
        if args[:2] == ("cloud", "show"): return {"name": self.cloud}
        if args[:2] == ("account", "show"): return {"tenantId": self.tenant, "id": SUB}
        if args[:2] == ("account", "get-access-token"): return {"accessToken": token()}
        if args[:4] == ("monitor", "log-analytics", "workspace", "show"):
            return {"id": SCOPE + "/providers/Microsoft.OperationalInsights/workspaces/Sentinel-Instance", "customerId": WS}
        if args[:3] == ("role", "assignment", "list"):
            principal = args[args.index("--assignee-object-id") + 1]
            if principal == USER: return self.caller_roles
            return [a for a in self.assignments if a["principalId"] == principal]
        if args[:3] == ("ad", "app", "list"):
            name = args[args.index("--display-name") + 1]
            return [a for a in self.apps.values() if a["displayName"] == name]
        if args[:3] == ("ad", "app", "create"):
            app = {"appId": self.new_id(), "id": self.new_id(), "displayName": args[args.index("--display-name") + 1]}
            self.apps[app["appId"]] = app
            return app
        if args[:3] == ("ad", "sp", "list"):
            app_id = args[args.index("--filter") + 1].split("'")[1]
            return [self.sps[app_id]] if app_id in self.sps else []
        if args[:3] == ("ad", "sp", "create"):
            self.sps[args[-1]] = {"id": self.new_id(), "appId": args[-1]}
            return self.sps[args[-1]]
        if args[:4] == ("ad", "app", "federated-credential", "list"):
            return list(self.creds.get(args[-1], []))
        if args[:4] == ("ad", "app", "federated-credential", "create"):
            body = json.loads(Path(args[args.index("--parameters") + 1]).read_text(encoding="utf-8"))
            self.creds.setdefault(args[args.index("--id") + 1], []).append(body)
            return body
        if args[:3] == ("role", "assignment", "create"):
            self.assignments.append({"principalId": args[args.index("--assignee-object-id") + 1],
                                     "roleDefinitionId": "/providers/Microsoft.Authorization/roleDefinitions/" +
                                                         args[args.index("--role") + 1],
                                     "scope": args[args.index("--scope") + 1]})
            return {}
        raise AssertionError(args)

    def gh(self, method, endpoint, body=None, missing=False):
        self.calls.append(("gh", method, endpoint, body))
        if method != "GET":
            assert self.apply, "GitHub write without apply"
            self.writes.append((method, endpoint))
        if endpoint == "repos/isi-security/detection-as-code":
            return dict(private=True, archived=False, permissions={"admin": True}, default_branch="main",
                        id=REPO_ID, owner={"id": OWNER_ID})
        if endpoint.endswith("/actions/permissions"): return {"enabled": True}
        if endpoint.endswith("/variables/AZURE_CLIENT_ID"): return {"value": "99999999-9999-4999-8999-999999999999"}
        if "/environments/" in endpoint:
            return {"protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}]}
        if "/contents/clients/" in endpoint and method == "GET":
            if "?ref=main" in endpoint and self.main_target:
                return {"content": base64.b64encode(self.main_target.encode()).decode()}
            return None
        if "/contents/rules/" in endpoint: return {}
        if endpoint.endswith("/git/ref/heads/main"): return {"object": {"sha": "abc"}}
        if "/git/ref/heads/" in endpoint: return None
        if "/contents/.github/workflows/" in endpoint: return None
        if method == "POST" and endpoint.endswith("/pulls"): return self.pull
        return {}

    def pages(self, path, key=None):
        return []


class GovOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.state = Path(self.folder.name) / "state.json"
        self.fake = FakeTenant()
        reader = patch("sentinel_app.same_tenant_onboarding.GitHubReader")
        reader.start().return_value.load.return_value = {"issues": [], "clients": []}
        self.addCleanup(reader.stop)

    def run_onboarding(self, apply=False, **changes):
        self.fake.apply = apply
        run = gov.GovOnboard(config(**changes), self.fake, self.state)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            run.run()
        return run, out.getvalue()

    def test_preview_changes_nothing_and_shows_the_plan(self):
        run, out = self.run_onboarding()
        self.assertEqual(self.fake.writes, [])
        self.assertFalse(self.state.exists())
        self.assertIn("App registration:   create", out)
        self.assertIn(f"repo:isi-security@{OWNER_ID}/detection-as-code@{REPO_ID}:environment:sentinel-preview", out)
        self.assertIn("READ-ONLY PLAN", out)

    def test_apply_creates_two_trusted_apps_roles_and_pull_request(self):
        run, out = self.run_onboarding(apply=True)
        self.assertEqual(len(self.fake.apps), 2)
        self.assertEqual(len(self.fake.sps), 2)
        preview, deploy = run.apps["preview"], run.apps["deploy"]
        self.assertNotEqual(preview["app_id"], deploy["app_id"])
        for kind, env in (("preview", "sentinel-preview"), ("deploy", "sentinel-production")):
            [cred] = self.fake.creds[run.apps[kind]["app_id"]]
            self.assertEqual(cred["audiences"], ["api://AzureADTokenExchangeUSGov"])
            self.assertEqual(cred["issuer"], "https://token.actions.githubusercontent.com")
            self.assertTrue(cred["subject"].endswith(":environment:" + env))
        roles = {(a["principalId"], a["roleDefinitionId"].rsplit("/", 1)[-1]) for a in self.fake.assignments}
        self.assertEqual(roles, {(preview["sp_id"], r) for _, r in gov.ROLES["preview"]} |
                                {(deploy["sp_id"], r) for _, r in gov.ROLES["deploy"]})
        self.assertTrue(all(a["scope"] == SCOPE for a in self.fake.assignments))
        put = next(c for c in self.fake.calls if c[0] == "gh" and c[1] == "PUT")
        manifest = base64.b64decode(put[3]["content"]).decode()
        self.assertIn(f'tenant_id: "{TENANT}"', manifest)
        self.assertIn('cloud: "AzureUSGovernment"', manifest)
        self.assertIn(f'preview_client_id: "{preview["app_id"]}"', manifest)
        self.assertIn(f'deploy_client_id: "{deploy["app_id"]}"', manifest)
        self.assertTrue(put[2].endswith("/contents/clients/aosense/aosense-workspace-gcch.yml"))
        body = next(c for c in self.fake.calls if c[0] == "gh" and c[1] == "POST" and c[2].endswith("/pulls"))[3]["body"]
        self.assertIn("CLIENT's Gov tenant", body)
        self.assertIn("Review pull request: " + self.fake.pull["html_url"], out)
        self.assertEqual(json.loads(self.state.read_text())["apps"]["deploy"]["app_id"], deploy["app_id"])

    def test_manifest_is_accepted_by_assignment_editor(self):
        run, _ = self.run_onboarding(apply=True)
        import yaml
        doc = yaml.safe_load(run.manifest())
        doc["rules"] = []
        rule_assignments.validate_target("clients/aosense/aosense-workspace-gcch.yml", doc, {})

    def test_rerun_after_completion_skips_finished_steps(self):
        first, _ = self.run_onboarding(apply=True)
        self.fake.main_target = first.manifest()
        self.fake.writes.clear()
        _, out = self.run_onboarding(apply=True)
        self.assertEqual(self.fake.writes, [])
        self.assertIn("Target already matches main", out)

    def test_interrupted_run_resumes_without_duplicates(self):
        self.fake.apply = True
        run = gov.GovOnboard(config(), self.fake, self.state)
        with patch.object(gov.GovOnboard, "ensure_roles", side_effect=Stop("network")), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Stop):
            run.run()
        self.run_onboarding(apply=True)
        self.assertEqual(len(self.fake.apps), 2)
        self.assertEqual(sum(len(v) for v in self.fake.creds.values()), 2)
        self.assertEqual(len(self.fake.assignments), 3)

    def test_commercial_session_stops_before_github(self):
        self.fake.cloud = "AzureCloud"
        with self.assertRaisesRegex(Stop, "GCC High"):
            self.run_onboarding()
        self.assertFalse(any(c[0] == "gh" for c in self.fake.calls))

    def test_wrong_tenant_stops(self):
        self.fake.tenant = USER
        with self.assertRaisesRegex(Stop, "mismatch"):
            self.run_onboarding()

    def test_global_admin_without_azure_access_is_told_how_to_fix_it(self):
        self.fake.caller_roles = [{"roleDefinitionName": "Reader"}]
        with self.assertRaisesRegex(Stop, "Access management for Azure resources"):
            self.run_onboarding(apply=True)
        self.assertEqual(self.fake.writes, [])

    def test_credential_name_clash_with_other_subject_stops(self):
        self.run_onboarding(apply=True)
        app_id = next(a["appId"] for a in self.fake.apps.values() if a["displayName"].endswith("Preview"))
        self.fake.creds[app_id] = [{"name": "github-sentinel-preview", "issuer": gov.ISSUER,
                                    "subject": "repo:someone/else:environment:sentinel-preview", "audiences": [gov.AUDIENCE]}]
        with self.assertRaisesRegex(Stop, "Review it"):
            self.run_onboarding()

    def test_duplicate_app_names_stop(self):
        for _ in range(2):
            self.fake.apply = True
            self.fake.az("ad", "app", "create", "--display-name", "ISI Sentinel Pipeline - Preview", write=True)
        with self.assertRaisesRegex(Stop, "More than one"):
            self.run_onboarding()

    def test_config_validation(self):
        for changes in ({"client": "AOSense"}, {"tenant_id": "x"}, {"preview_environment": "sentinel-production"},
                        {"subject_format": "repo:x"}, {"unknown": 1}):
            with self.subTest(changes=changes), self.assertRaises(Stop):
                gov.validate_gov_config(config(**changes))


class CloudDetectionTests(unittest.TestCase):
    def test_detects_gcc_high_and_commercial(self):
        gov_doc = json.dumps({"cloud_instance_name": "microsoftonline.us"}).encode()
        com_doc = json.dumps({"cloud_instance_name": "microsoftonline.com", "tenant_region_sub_scope": "GCC"}).encode()
        self.assertEqual(backend.detect_cloud("aosense.com", lambda url: gov_doc)[0], "AzureUSGovernment")
        self.assertEqual(backend.detect_cloud("zero.onmicrosoft.com", lambda url: com_doc)[0], "AzureCloud")

    def test_blank_or_unreachable_stays_on_public_cloud(self):
        fetch = MagicMock(side_effect=OSError("offline"))
        self.assertEqual(backend.detect_cloud("", fetch), ("AzureCloud", ""))
        fetch.assert_not_called()
        cloud, note = backend.detect_cloud("x.com", fetch)
        self.assertEqual(cloud, "AzureCloud"); self.assertIn("offline", note)

    def test_prepare_cloud_switches_only_for_government(self):
        with tempfile.TemporaryDirectory() as folder:
            session = backend.Session(Path(folder))
            with patch.object(backend, "detect_cloud", return_value=("AzureCloud", "")), \
                    patch.object(session, "az") as az:
                session.prepare_cloud("zero.onmicrosoft.com")
            az.assert_not_called()
            with patch.object(backend, "detect_cloud", return_value=("AzureUSGovernment", "note")), \
                    patch.object(session, "az") as az:
                session.prepare_cloud("aosense.onmicrosoft.us")
            az.assert_called_once_with("cloud", "set", "--name", "AzureUSGovernment")
            self.assertEqual(session.cloud, "AzureUSGovernment")

    def test_gov_run_requires_gov_session(self):
        session = MagicMock(cloud="AzureCloud")
        with self.assertRaisesRegex(Stop, "GCC High"):
            backend.gov_run(session, config())


class GovDialogTests(unittest.TestCase):
    def test_slug_suggestion(self):
        from sentinel_app.gov_onboarding_dialog import suggested_slug
        self.assertEqual(suggested_slug("AOSense, Inc"), "aosense")
        self.assertEqual(suggested_slug("Allied Mission Group LLC"), "allied-mission-group")

    def test_button_only_for_government_workspace(self):
        from sentinel_app import desktop_app as ui
        with tempfile.TemporaryDirectory() as folder, patch.object(ui, "DATA", Path(folder)), \
                patch.object(ui, "create_handoff", return_value=None):
            root = tk.Tk(); root.withdraw()
            try:
                app = ui.App(root)
                app.workspace = backend.workspace_record(
                    {"id": SCOPE + "/providers/Microsoft.OperationalInsights/workspaces/Sentinel-Instance",
                     "customerId": WS}, SUB, TENANT)
                app.session = MagicMock(cloud="AzureCloud", account={})
                app.sync_connection(); root.update_idletasks()
                self.assertFalse(app.gov_button.winfo_manager())
                app.session.cloud = "AzureUSGovernment"
                app.sync_connection(); root.update_idletasks()
                self.assertTrue(app.gov_button.winfo_manager())
                self.check_dialog(app)
            finally:
                for timer in root.tk.call("after", "info"): root.after_cancel(timer)
                root.destroy()


    def check_dialog(self, app):
        from sentinel_app import gov_onboarding_dialog as dialog_module
        app.settings = {"github_owner": "isi-security", "github_repo": "detection-as-code"}
        app.vars["client_name"].set("AOSense, Inc")
        app.work = lambda title, task, done, page="connect": done(task())
        outputs = [("GCC High onboarding plan ready. Nothing changed.", "plan"),
                   ("GCC High onboarding applied; review the pull request.",
                    "Review pull request: https://github.com/isi-security/detection-as-code/pull/7")]
        with patch.object(dialog_module, "gov_run", side_effect=lambda s, c, a: outputs[1 if a else 0]) as run:
            dialog = dialog_module.GovOnboardingDialog(app)
            self.assertEqual(str(dialog.apply_button.cget("state")), "disabled")
            style = lambda button: str(button.cget("style"))
            self.assertEqual((style(dialog.preview_button), style(dialog.apply_button)), ("Primary.TButton", "Secondary.TButton"))
            self.assertEqual(dialog.window.bind("<Return>"), "")  # Only the colour moves; Enter still does nothing.
            dialog.preview()
            self.assertEqual((style(dialog.preview_button), style(dialog.apply_button)), ("Secondary.TButton", "Primary.TButton"))
            config = run.call_args.args[1]
            self.assertEqual(config["target"] if "target" in config else config["client"] + "-" + config["workspace_label"],
                             "aosense-workspace-gcch")
            self.assertEqual(str(dialog.apply_button.cget("state")), "normal")
            dialog.vars["label"].set("workspace-gcch2")
            self.assertEqual(str(dialog.apply_button.cget("state")), "disabled")
            dialog.vars["label"].set("workspace-gcch"); dialog.preview(); dialog.apply()
            self.assertTrue(run.call_args.args[2])
            self.assertEqual(dialog.pull_request, "https://github.com/isi-security/detection-as-code/pull/7")
            self.assertEqual(dialog.window.bind("<Return>"), "")
            # Right after an apply nothing is blue (open the pull request next); new settings bring Preview back.
            self.assertEqual((style(dialog.preview_button), style(dialog.apply_button)), ("Secondary.TButton", "Secondary.TButton"))
            dialog.vars["label"].set("workspace-gcch2")
            self.assertEqual((style(dialog.preview_button), style(dialog.apply_button)), ("Primary.TButton", "Secondary.TButton"))
            dialog.vars["label"].set("workspace-gcch"); dialog.preview(); dialog.apply()
            self.assertEqual(style(dialog.preview_button), "Secondary.TButton")
            dialog.failed("STOPPED: Preview failed."); dialog.controls()  # As run_in_dialog's after= does.
            self.assertEqual((style(dialog.preview_button), str(dialog.pr_button.cget("state"))), ("Primary.TButton", "normal"))
            dialog.close()
            dialog = dialog_module.GovOnboardingDialog(app)
            with patch.object(dialog.review, "see") as see:
                dialog.failed("READ-ONLY PLAN\nApp registration: create\nSTOPPED: Role assignment failed: AuthorizationFailed.\n"
                              "Changes may be partial.")
            see.assert_called_once_with("end")
            self.assertEqual(dialog.notice.get(), "Stopped: Role assignment failed: AuthorizationFailed. "
                                                  "The details are at the end of the plan above.")
            self.assertEqual(str(dialog.review.tag_ranges("danger")[0]), "3.0")
            self.assertEqual(str(dialog.preview_button.cget("style")), "Primary.TButton")
            dialog.failed("Sign in to the client's GCC High tenant first.")
            self.assertEqual(dialog.notice.get(), "Stopped: Sign in to the client's GCC High tenant first. "
                                                  "The details are at the end of the plan above.")
            self.assertEqual(str(dialog.review.tag_ranges("danger")[0]), "1.0")
            dialog.close()


if __name__ == "__main__":
    unittest.main()
