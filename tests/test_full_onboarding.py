"""Offline tests. No Azure/GitHub CLI process is launched."""
import base64
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from full_onboarding import Onboard, CLI, Stop, validate_config

def gid(n):
    return f"11111111-1111-4111-8111-{n:012d}"

def config():
    return dict(tenant_id=gid(1), subscription_id=gid(2), resource_group="rg-example-sentinel",
        workspace_name="law-example-sentinel", client="acme", workspace_label="primary",
        github_owner="test-org", github_repo="detections", app_owner_user_ids=[gid(3)],
        production_reviewers=[{"type":"User", "name":"reviewer"}], oidc_subject_format="immutable",
        initial_rule_path="rules/sentinel/testing/test.yml", human_groups=[])

class Cloud:
    def __init__(self, apply=False):
        self.apply = apply
        self.c = config()
        self.apps = {}
        self.sps = {}
        self.owners = {}
        self.fics = {}
        self.envs = {}
        self.branches = {}
        self.vars = {}
        self.files = {}
        self.refs = {}
        self.prs = []
        self.groups = {}
        self.assignments = []
        self.writes = []
        self.fail_deployment = False
        self.workspace = {"id": f"/subscriptions/{gid(2)}/resourceGroups/rg-example-sentinel/providers/Microsoft.OperationalInsights/workspaces/law-example-sentinel", "customerId":gid(4)}
    def az(self, *a, write=False):
        if write:
            assert self.apply
            self.writes.append(("az", a))
        def val(k): return a[a.index(k)+1]
        if a == ("version",): return {"azure-cli":"2.80.0"}
        if a == ("account","show"): return {"id":self.c["subscription_id"],"tenantId":self.c["tenant_id"]}
        if a[:4] == ("monitor","log-analytics","workspace","show"): return self.workspace
        if a[:3] == ("ad","user","show"): return {"id":val("--id")}
        if a[:3] == ("ad","app","list"): return list(self.apps.values())
        if a[:3] == ("ad","app","show"): return next(x for x in self.apps.values() if x["appId"]==val("--id"))
        if a[:3] == ("ad","app","create"):
            n = 10 + len(self.apps)*10
            x = dict(id=gid(n),appId=gid(n+1),signInAudience="AzureADMyOrg",displayName=val("--display-name"))
            self.apps[x["id"]] = x
            self.owners[x["id"]] = []
            self.fics[x["id"]] = []
            return x
        if a[:3] == ("ad","sp","list"): return [x for x in self.sps.values() if x["appId"] in val("--filter")]
        if a[:3] == ("ad","sp","create"):
            x = {"id":gid(100+len(self.sps)), "appId":val("--id")}
            self.sps[x["id"]] = x
            return x
        if a[:4] == ("ad","app","owner","list"): return self.owners[val("--id")]
        if a[:4] == ("ad","app","owner","add"):
            self.owners[val("--id")].append({"id":val("--owner-object-id")}); return
        if a[:4] == ("ad","app","federated-credential","list"): return self.fics[val("--id")]
        if a[:4] == ("ad","app","federated-credential","create"):
            self.fics[val("--id")].append(json.loads(Path(val("--parameters")).read_text())); return
        if a[:2] == ("deployment","group"):
            if a[2] == "create" and self.fail_deployment: raise Stop("Simulated ARM failure")
            return {"properties":{"provisioningState":"Succeeded"}, "changes":[]}
        if a[:3] == ("role","definition","list"):
            return [{"id":"/subscriptions/"+gid(2)+"/providers/Microsoft.Authorization/roleDefinitions/"+gid(90),
                     "roleName":val("--name"),"roleType":"BuiltInRole"}]
        if a[:3] == ("ad","group","list"): return list(self.groups.values())
        if a[:3] == ("ad","group","show"): return self.groups[val("--group")]
        if a[:3] == ("ad","group","create"):
            x={"id":gid(200+len(self.groups)), "displayName":val("--display-name"),"securityEnabled":True,
               "owner":[], "member":[]}
            self.groups[x["id"]]=x; return x
        if a[:2] == ("ad","group") and a[2] in ("member","owner"):
            group=self.groups[val("--group")]
            if a[3] == "list": return group[a[2]]
            group[a[2]].append({"id":val("--member-id" if a[2]=="member" else "--owner-object-id")}); return
        if a[:3] == ("role","assignment","list"): return self.assignments
        if a[:3] == ("role","assignment","create"):
            self.assignments.append({"principalId":val("--assignee-object-id"),"roleDefinitionId":val("--role"),"scope":val("--scope")}); return {}
        raise AssertionError(a)
    def gh(self, method, path, body=None, missing=False):
        if method != "GET":
            assert self.apply
            self.writes.append((method,path,body))
        base="repos/test-org/detections"
        if path==base: return {"id":500,"name":"detections","private":True,"archived":False,
            "permissions":{"admin":True},"default_branch":"main","owner":{"login":"test-org","id":400}}
        if path.endswith("/actions/permissions"): return {"enabled":True}
        if path.endswith("/actions/oidc/customization/sub"): return {"use_default":True}
        if path=="users/reviewer": return {"id":600}
        if "/collaborators/" in path: return {"permission":"read"}
        if "/environments/" in path:
            env=path.split("/environments/")[1].split("/")[0]
            if "/variables" in path:
                if method=="GET": return self.vars.get(env)
                self.vars[env]=body; return {}
            if "/deployment-branch-policies" in path:
                if method=="POST": self.branches[env]=[body]; return body
                return {"branch_policies": self.branches.get(env,[])}
            if method=="GET": return self.envs.get(env)
            rules=[] if not body["reviewers"] else [{"type":"required_reviewers",
                "prevent_self_review":body["prevent_self_review"],
                "reviewers":[{"type":r["type"],"reviewer":{"id":r["id"]}} for r in body["reviewers"]]}]
            self.envs[env]={"deployment_branch_policy":body["deployment_branch_policy"],"protection_rules":rules}
            return self.envs[env]
        if "/contents/rules/" in path: return {"content":""}
        if "/contents/clients/" in path:
            if method=="PUT":
                self.files["branch"]=body; return {}
            return self.files.get("main" if path.endswith("?ref=main") else "branch")
        if path.endswith("/git/ref/heads/main"): return {"object":{"sha":"abc123"}}
        if "/git/ref/heads/" in path: return self.refs.get(path.split("/heads/")[1])
        if path.endswith("/git/refs"):
            self.refs[body["ref"].split("refs/heads/")[1]]={"object":{"sha":body["sha"]}}; return {}
        if "/pulls" in path:
            if method=="GET": return self.prs
            x={"html_url":"https://github.com/test-org/detections/pull/1"}
            self.prs.append(x); return x
        raise AssertionError((method,path))
    def pages(self, path, key=None):
        data=self.gh("GET",path)
        return data[key] if key else data

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)/"state.json"
        self.template=Path(__file__).resolve().parents[1]/"azuredeploy.json"
        self.cloud=Cloud()
    def run_it(self, c=None):
        run=Onboard(c or config(), self.cloud, self.state, self.template)
        with contextlib.redirect_stdout(io.StringIO()): run.run()
        return run
    def test_workspace_filename_preserves_target_identity(self):
        self.cloud.apply=True
        run=self.run_it()
        self.assertEqual(run.target_path, "clients/acme/workspace.yml")
        self.assertEqual(run.c["target"], "acme-primary")
        self.assertEqual(run.environment("deploy"), "acme-primary-production")
        writes=[x for x in self.cloud.writes if x[0]=="PUT" and "/contents/clients/" in x[1]]
        self.assertEqual(writes[0][1], "repos/test-org/detections/contents/clients/acme/workspace.yml")

    def test_additional_workspace_has_distinct_filename(self):
        c=config(); c["workspace_label"]="secondary"
        run=self.run_it(c)
        self.assertEqual(run.target_path,"clients/acme/workspace-secondary.yml")
        self.assertEqual(run.c["target"],"acme-secondary")

    def test_empty_rule_target_disabled(self):
        c=config(); c["initial_rule_path"]=""
        run=self.run_it(c)
        self.assertIn("enabled: false",run.manifest())
        self.assertIn("rules: []",run.manifest())

    def test_legacy_manifest_blocks_duplicate_before_writes(self):
        self.cloud.apply=True
        original=self.cloud.gh
        def legacy(method,path,body=None,missing=False):
            if path.endswith("/contents/clients/acme/primary.yml?ref=main"):
                return {"content": base64.b64encode(b"existing target").decode()}
            return original(method,path,body,missing)
        self.cloud.gh=legacy
        with self.assertRaisesRegex(Stop,"Existing legacy manifest"):
            self.run_it()
        self.assertEqual(self.cloud.writes,[])

    def test_readonly_plan(self):
        self.run_it()
        self.assertEqual(self.cloud.writes,[])
        self.assertFalse(self.state.exists())
    def test_full_apply_and_resume_without_duplicate_resources(self):
        self.cloud.apply=True
        self.run_it()
        counts=(len(self.cloud.apps),len(self.cloud.sps),len(self.cloud.prs),sum(map(len,self.cloud.fics.values())))
        self.run_it()
        self.assertEqual(counts,(2,2,1,2))
        self.assertEqual(counts,(len(self.cloud.apps),len(self.cloud.sps),len(self.cloud.prs),sum(map(len,self.cloud.fics.values()))))
        self.assertIn("enabled: false",base64.b64decode(self.cloud.files["branch"]["content"]).decode())
        for env in self.cloud.envs:
            self.assertEqual(self.cloud.branches[env],[{"name":"main","type":"branch"}])
    def test_partial_failure_resumes(self):
        self.cloud.apply=True; self.cloud.fail_deployment=True
        with self.assertRaises(Stop): self.run_it()
        self.assertTrue(self.state.exists())
        self.assertEqual(self.cloud.prs,[])
        self.cloud.fail_deployment=False
        self.run_it()
        self.assertEqual(len(self.cloud.apps),2)
        self.assertEqual(len(self.cloud.prs),1)
    def test_wrong_subscription_stops_before_write(self):
        self.cloud.apply=True; self.cloud.c["subscription_id"]=gid(99)
        with self.assertRaises(Stop): self.run_it()
        self.assertEqual(self.cloud.writes,[])
    def test_oidc_subjects_are_isolated(self):
        self.cloud.apply=True; self.run_it()
        subjects=[v[0]["subject"] for v in self.cloud.fics.values()]
        self.assertEqual(set(subjects),{
            "repo:test-org@400/detections@500:environment:acme-primary-preview",
            "repo:test-org@400/detections@500:environment:acme-primary-production"})
    def test_existing_wrong_trust_stops(self):
        self.cloud.apply=True; self.run_it(); self.cloud.writes.clear()
        next(iter(self.cloud.fics.values()))[0]["subject"]="wrong"
        with self.assertRaises(Stop): self.run_it()
        self.assertEqual(self.cloud.writes,[])
    def test_existing_wrong_client_id_stops(self):
        self.cloud.apply=True; self.run_it(); self.cloud.writes.clear()
        self.cloud.vars["acme-primary-production"]["value"]=gid(900)
        with self.assertRaises(Stop): self.run_it()
        self.assertEqual(self.cloud.writes,[])
    def test_existing_wildcard_branch_stops(self):
        self.cloud.apply=True; self.run_it(); self.cloud.writes.clear()
        self.cloud.branches["acme-primary-production"]=[{"name":"*","type":"branch"}]
        with self.assertRaises(Stop): self.run_it()
        self.assertEqual(self.cloud.writes,[])
    def test_existing_target_not_overwritten(self):
        self.cloud.apply=True
        self.cloud.files["main"]={"content":base64.b64encode(b"rules: [other]").decode()}
        with self.assertRaises(Stop): self.run_it()
        self.assertEqual(self.cloud.writes,[])
    def test_state_cannot_cross_clients(self):
        self.cloud.apply=True; self.run_it()
        c=config(); c["client"]="another"
        with self.assertRaises(Stop): self.run_it(c)
    def test_optional_group_members_and_scoped_assignment_resume(self):
        self.cloud.apply=True
        c=config(); c["human_groups"]=[dict(name="sentinel-acme-readers",role="Microsoft Sentinel Reader",
            owner_user_ids=[gid(3)],member_user_ids=[gid(5)])]
        self.run_it(c); self.run_it(c)
        self.assertEqual(len(self.cloud.groups),1)
        self.assertEqual(len(self.cloud.assignments),1)
        self.assertEqual(self.cloud.assignments[0]["scope"],self.cloud.workspace["id"])
    def test_no_secret_or_rule_write_calls(self):
        self.cloud.apply=True; self.run_it()
        serialized=json.dumps(self.cloud.writes)
        self.assertNotIn("credential reset",serialized)
        self.assertNotIn("alertRules",serialized)
        self.assertNotIn("/dispatches",serialized)
    def test_config_rejects_path_traversal(self):
        c=config(); c["initial_rule_path"]="rules/sentinel/../test.yml"
        with self.assertRaises(Stop): validate_config(c)
    def test_config_rejects_zero_ids(self):
        c=config(); c["tenant_id"]="00000000-0000-0000-0000-000000000000"
        with self.assertRaises(Stop): validate_config(c)
    def test_config_rejects_same_apps(self):
        c=config(); c["existing_preview_app_client_id"]=c["existing_deploy_app_client_id"]=gid(50)
        with self.assertRaises(Stop): validate_config(c)
    def test_cli_blocks_write_in_plan(self):
        cli=object.__new__(CLI); cli.apply=False
        with patch("subprocess.run") as process:
            with self.assertRaises(Stop): cli.run("gh",["api"],write=True)
            process.assert_not_called()
    def test_cli_does_not_swallow_403_as_missing(self):
        cli=object.__new__(CLI); cli.apply=False
        with patch("subprocess.run") as process:
            process.return_value.returncode=1
            process.return_value.stderr="Forbidden (HTTP 403)"
            with self.assertRaises(Stop): cli.run("gh",["api"],missing=True)
    def test_cli_treats_404_as_missing(self):
        cli=object.__new__(CLI); cli.apply=False
        with patch("subprocess.run") as process:
            process.return_value.returncode=1; process.return_value.stderr="Not Found (HTTP 404)"
            self.assertIsNone(cli.run("gh",["api"],missing=True))

if __name__=="__main__":
    unittest.main(verbosity=2)

