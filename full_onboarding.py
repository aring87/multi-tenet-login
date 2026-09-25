#!/usr/bin/env python3
"""Client onboarding for existing Sentinel/GitHub pipeline. No external Python packages."""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

class Stop(RuntimeError):
    pass

def require(ok, message):
    if not ok:
        raise Stop(message)

def guid(value, label):
    require(isinstance(value, str), f"{label} must be a GUID string.")
    try:
        result = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise Stop(f"Replace {label} with a real GUID.")
    require(result != str(uuid.UUID(int=0)), f"{label} cannot be a zero GUID.")
    return result

def slug(value, label, maximum=48):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value)
            and 1 <= len(value) <= maximum, f"{label} must be a lowercase hyphenated name.")
    return value

def validate_config(c):
    required = {"tenant_id", "subscription_id", "resource_group", "workspace_name",
                "client", "workspace_label", "github_owner", "github_repo",
                "app_owner_user_ids", "production_reviewers", "oidc_subject_format"}
    optional = {"initial_rule_path", "allow_missing_mitre", "human_groups",
                "existing_preview_app_client_id", "existing_deploy_app_client_id"}
    require(isinstance(c, dict) and required <= c.keys(), "Config is missing required fields.")
    require(not (c.keys() - required - optional), "Config contains unrecognized fields.")
    c = dict(c)
    for key in ("tenant_id", "subscription_id"):
        c[key] = guid(c[key], key)
    slug(c["client"], "client")
    slug(c["workspace_label"], "workspace_label")
    c["target"] = slug(c["client"] + "-" + c["workspace_label"], "combined target")
    for key in ("resource_group", "workspace_name"):
        require(isinstance(c[key], str) and c[key] and
                not re.search(r"""[<>&|^%!\r\n"'\x60]""", c[key]), f"Unsupported characters in {key}.")
    require(len(c["resource_group"]) <= 90 and not c["resource_group"].endswith("."), "Invalid resource_group.")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,61}[A-Za-z0-9]", c["workspace_name"]), "Invalid workspace_name.")
    for key in ("github_owner", "github_repo"):
        require(isinstance(c[key], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", c[key]), f"Invalid {key}.")
    require(c["oidc_subject_format"] in ("immutable", "legacy"),
            "oidc_subject_format must match the verified GitHub token: immutable or legacy.")
    require(type(c.get("allow_missing_mitre", False)) is bool, "allow_missing_mitre must be boolean.")
    c.setdefault("allow_missing_mitre", False)
    owners = c["app_owner_user_ids"]
    require(isinstance(owners, list) and owners, "List at least one approved app owner user Object ID.")
    c["app_owner_user_ids"] = [guid(x, "app owner") for x in owners]
    reviewers = c["production_reviewers"]
    require(isinstance(reviewers, list) and 1 <= len(reviewers) <= 6, "List 1-6 production reviewers.")
    for r in reviewers:
        require(isinstance(r, dict) and set(r) == {"type", "name"} and r["type"] in ("User", "Team"),
                "Reviewer requires type User/Team and name (login or team slug).")
        require(isinstance(r["name"], str) and re.fullmatch(r"[A-Za-z0-9-]+", r["name"])
                and "REPLACE" not in r["name"].upper(), "Replace reviewer names with real GitHub names.")
    for key in ("existing_preview_app_client_id", "existing_deploy_app_client_id"):
        if c.get(key):
            c[key] = guid(c[key], key)
    require(not (c.get("existing_preview_app_client_id") and
                 c.get("existing_preview_app_client_id") == c.get("existing_deploy_app_client_id")),
            "Preview and deployment applications must differ.")
    path = c.get("initial_rule_path", "")
    require(isinstance(path, str) and (not path or (
        re.fullmatch(r"rules/sentinel/[A-Za-z0-9_./ -]+\.ya?ml", path) and
        all(p not in ("", ".", "..") for p in path.split("/")))), "Invalid initial_rule_path.")
    c["initial_rule_path"] = path
    groups = c.get("human_groups", [])
    require(isinstance(groups, list), "human_groups must be a list.")
    names = set()
    for g in groups:
        require(isinstance(g, dict) and {"name", "role", "owner_user_ids", "member_user_ids"} <= g.keys()
                and not (g.keys() - {"name", "role", "owner_user_ids", "member_user_ids", "existing_object_id"}),
                "Group needs name, role, owner_user_ids and member_user_ids.")
        slug(g["name"], "group name", 64)
        require(g["name"] not in names, "Duplicate human group names.")
        names.add(g["name"])
        require(g["role"] in ("Microsoft Sentinel Reader", "Microsoft Sentinel Responder"),
                "Human-group role must be Sentinel Reader or Responder.")
        for key in ("owner_user_ids", "member_user_ids"):
            require(isinstance(g[key], list), f"Group {key} must be a list.")
            g[key] = [guid(x, f"group {key}") for x in g[key]]
        require(g["owner_user_ids"], "Each human group needs an approved owner.")
        if g.get("existing_object_id"):
            g["existing_object_id"] = guid(g["existing_object_id"], "existing group Object ID")
    c["human_groups"] = groups
    return c

class CLI:
    def __init__(self, apply=False):
        self.apply = apply
        self.azure = shutil.which("az")
        self.github = shutil.which("gh")
        require(self.azure and self.github, "Install Azure CLI and GitHub CLI, then reopen Git Bash.")

    def run(self, executable, args, write=False, data=None, missing=False):
        require(not write or self.apply, "Internal guard: write attempted without --apply.")
        if executable.lower().endswith((".cmd", ".bat")):
            require(all(not re.search(r'[&|<>^%!\r\n"]', str(x)) for x in args),
                    "Unsupported shell characters in an Azure CLI argument/path; use a simple folder path.")
        env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
        p = subprocess.run([executable, *map(str, args)], input=data, text=True,
                           capture_output=True, encoding="utf-8", env=env, shell=False)
        if p.returncode:
            if missing and re.search(r"HTTP 404\b", p.stderr):
                return None
            raise Stop(p.stderr.strip() or f"Command failed with exit {p.returncode}.")
        if not p.stdout.strip():
            return None
        try:
            return json.loads(p.stdout)
        except ValueError:
            raise Stop("A CLI response was not JSON; review CLI configuration/version.")

    def az(self, *args, write=False):
        return self.run(self.azure, [*args, "--only-show-errors", "--output", "json"], write=write)

    def gh(self, method, path, body=None, missing=False):
        args = ["api", "--hostname", "github.com", "--method", method,
                "-H", "Accept: application/vnd.github+json",
                "-H", "X-GitHub-Api-Version: 2026-03-10", path]
        if body is not None:
            args += ["--input", "-"]
        return self.run(self.github, args, write=method != "GET",
                        data=json.dumps(body) if body is not None else None, missing=missing)

    def pages(self, path, key=None):
        result = []
        for page in range(1, 1001):
            value = self.gh("GET", path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            items = value[key] if key else value
            require(isinstance(items, list), "Unexpected paginated GitHub response.")
            result.extend(items)
            if len(items) < 100:
                return result
        raise Stop("Too many API pages; no truncated results will be used.")

class Onboard:
    def __init__(self, config, cli, state_path, template_path):
        self.c, self.io = validate_config(config), cli
        self.apply = cli.apply
        self.state_path, self.template = Path(state_path), Path(template_path)
        require(self.template.is_file(), "Keep azuredeploy.json beside full_onboarding.py.")
        self.binding = {k: self.c[k] for k in ("tenant_id", "subscription_id", "resource_group",
                                             "workspace_name", "target", "github_owner", "github_repo")}
        self.state = {"binding": self.binding, "apps": {}, "groups": {}, "environments": {}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            require(self.state.get("binding") == self.binding, "State belongs to another client/scope. Use its matching config.")
        self.repo_path = f"repos/{self.c['github_owner']}/{self.c['github_repo']}"
        self.apps, self.groups = {}, {}

    def save(self):
        if self.apply:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.state_path)

    def app_name(self, kind):
        suffix = "preview" if kind == "preview" else "deploy"
        return f"github-sentinel-{self.c['target']}-{suffix}"

    def environment(self, kind):
        return self.c["target"] + ("-preview" if kind == "preview" else "-production")

    def preflight(self):
        version = self.io.az("version")["azure-cli"]
        require(tuple(map(int, version.split(".")[:2])) >= (2, 76), "Azure CLI 2.76.0+ is required.")
        account = self.io.az("account", "show")
        require(account["tenantId"].lower() == self.c["tenant_id"] and
                account["id"].lower() == self.c["subscription_id"],
                "Sign in to the client and run az account set --subscription with the configured subscription.")
        self.workspace = self.io.az("monitor", "log-analytics", "workspace", "show",
                                    "--subscription", self.c["subscription_id"],
                                    "--resource-group", self.c["resource_group"],
                                    "--workspace-name", self.c["workspace_name"])
        expected = (f"/subscriptions/{self.c['subscription_id']}/resourceGroups/{self.c['resource_group']}"
                    f"/providers/Microsoft.OperationalInsights/workspaces/{self.c['workspace_name']}")
        require(self.workspace["id"].lower() == expected.lower(), "Azure returned a different workspace.")
        guid(self.workspace["customerId"], "workspace GUID")
        self.repo = self.io.gh("GET", self.repo_path)
        require(self.repo.get("private") and not self.repo.get("archived"), "Use an active private repository.")
        require(self.repo.get("permissions", {}).get("admin"), "GitHub repository admin access is required.")
        require(self.repo["default_branch"] == "main", "This package expects the existing main-based pipeline.")
        require(self.io.gh("GET", self.repo_path + "/actions/permissions")["enabled"], "GitHub Actions is disabled.")
        oidc = self.io.gh("GET", self.repo_path + "/actions/oidc/customization/sub")
        require(oidc.get("use_default") or oidc.get("include_claim_keys") == ["repo", "context"],
                "Custom OIDC subject template detected; use a tailored onboarding script.")
        prefix = f"repo:{self.repo['owner']['login']}"
        if self.c["oidc_subject_format"] == "immutable":
            prefix += f"@{self.repo['owner']['id']}/{self.repo['name']}@{self.repo['id']}"
        else:
            prefix += f"/{self.repo['name']}"
        self.subject_prefix = prefix + ":environment:"
        self.reviewers = []
        for reviewer in self.c["production_reviewers"]:
            if reviewer["type"] == "User":
                item = self.io.gh("GET", "users/" + reviewer["name"])
                access = self.io.gh("GET", self.repo_path + "/collaborators/" + reviewer["name"] + "/permission")
                require(access["permission"] != "none", "Production reviewer lacks repository access.")
            else:
                team_path = f"orgs/{self.c['github_owner']}/teams/{reviewer['name']}"
                item = self.io.gh("GET", team_path)
                self.io.gh("GET", team_path + f"/repos/{self.c['github_owner']}/{self.c['github_repo']}")
            self.reviewers.append({"type": reviewer["type"], "id": item["id"]})
        users = set(self.c["app_owner_user_ids"])
        for group in self.c["human_groups"]:
            users.update(group["owner_user_ids"] + group["member_user_ids"])
        for user_id in sorted(users):
            user = self.io.az("ad", "user", "show", "--id", user_id)
            require(user["id"].lower() == user_id, "Owner/member must be an existing user Object ID.")
        if self.c["initial_rule_path"]:
            self.io.gh("GET", self.repo_path + "/contents/" + quote(self.c["initial_rule_path"], safe="/") + "?ref=main")
        for kind in ("preview", "deploy"):
            self.inspect_app(kind)
            self.check_environment(kind)
        for group in self.c["human_groups"]:
            self.inspect_group(group)

    def inspect_app(self, kind):
        saved = self.state["apps"].get(kind, {})
        app_id = self.c.get(f"existing_{kind}_app_client_id") or saved.get("appId")
        if saved and app_id != saved.get("appId"):
            raise Stop("Config and recorded app identity conflict; do not silently rotate identities.")
        if app_id:
            app = self.io.az("ad", "app", "show", "--id", app_id)
            require(app["signInAudience"] == "AzureADMyOrg", "Expected a client-local single-tenant app.")
            require(app["appId"].lower() == app_id.lower(), "Application identity mismatch.")
            if saved:
                require(app["id"] == saved["id"], "Saved application Object ID changed.")
            self.apps[kind] = app
            creds = self.io.az("ad", "app", "federated-credential", "list", "--id", app["id"])
            require(all(x["issuer"] == "https://token.actions.githubusercontent.com" and
                        x.get("subject") == self.subject_prefix + self.environment(kind) and
                        x.get("audiences") == ["api://AzureADTokenExchange"] for x in creds),
                    "Existing app has other/different trust credentials; use an isolated app or review manually.")
        else:
            matches = self.io.az("ad", "app", "list", "--display-name", self.app_name(kind))
            require(not any(a["displayName"].lower() == self.app_name(kind).lower() for a in matches),
                    f"App name already exists: {self.app_name(kind)}. Supply its client ID explicitly to adopt it.")
            self.apps[kind] = None

    def inspect_group(self, group):
        saved = self.state["groups"].get(group["name"], {})
        object_id = group.get("existing_object_id") or saved.get("id")
        if saved:
            require(object_id == saved["id"], "Group state/config identity conflict.")
        if object_id:
            item = self.io.az("ad", "group", "show", "--group", object_id)
            require(item["id"].lower() == object_id.lower() and item.get("securityEnabled")
                    and item["displayName"] == group["name"], "Existing group identity/type/name mismatch.")
        else:

            existing = self.io.az("ad", "group", "list", "--display-name", group["name"])
            require(not any(g["displayName"].lower() == group["name"].lower() for g in existing),
                    "Group name exists; supply existing_object_id to adopt it explicitly.")
            item = None
        roles = self.io.az("role", "definition", "list", "--name", group["role"])
        roles = [r for r in roles if r.get("roleType") == "BuiltInRole" and r["roleName"] == group["role"]]
        require(len(roles) == 1, "Could not resolve the approved built-in human-group role.")
        self.groups[group["name"]] = (item, roles[0]["id"])


    def env_path(self, kind):
        return self.repo_path + "/environments/" + self.environment(kind)

    def variable_path(self, kind):
        return self.env_path(kind) + "/variables"

    def check_environment(self, kind):
        path = self.env_path(kind)
        env = self.io.gh("GET", path, missing=True)
        if env is None:
            return
        require(env.get("deployment_branch_policy") == {
            "protected_branches": False, "custom_branch_policies": True},
            f"{self.environment(kind)} must allow selected branches only; review existing settings.")
        expected = self.reviewers if kind == "deploy" else []
        rules = [r for r in env.get("protection_rules", []) if r["type"] == "required_reviewers"]
        actual = rules[0].get("reviewers", []) if rules else []
        require({(r["type"], r["reviewer"]["id"]) for r in actual} ==
                {(r["type"], r["id"]) for r in expected}, "Existing environment reviewers differ from config.")
        if kind == "deploy":
            require(rules and rules[0].get("prevent_self_review"), "Production must prevent self-review.")
        branches = self.io.pages(path + "/deployment-branch-policies", "branch_policies")
        require((len(branches) == 1 and branches[0]["name"] == "main" and
                 branches[0].get("type") == "branch") or
                (not branches and self.state["environments"].get(kind)),
                "Environment must contain exactly one Branch rule named main.")
        value = self.io.gh("GET", self.variable_path(kind) + "/AZURE_CLIENT_ID", missing=True)
        if value:
            require(self.apps.get(kind) and value["value"].lower() == self.apps[kind]["appId"].lower(),
                    "Existing AZURE_CLIENT_ID differs; refusing to replace another identity.")

    def ensure_environment(self, kind):
        path = self.env_path(kind)
        if self.io.gh("GET", path, missing=True) is None:
            self.io.gh("PUT", path, {
                "wait_timer": 0, "prevent_self_review": kind == "deploy",
                "reviewers": self.reviewers if kind == "deploy" else [],
                "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}})
            self.state["environments"][kind] = True
            self.save()
        branches = self.io.pages(path + "/deployment-branch-policies", "branch_policies")
        if not branches:
            require(self.state["environments"].get(kind), "Unrecognized partially configured environment.")
            self.io.gh("POST", path + "/deployment-branch-policies", {"name": "main", "type": "branch"})
        self.check_environment(kind)

    def ensure_app(self, kind):
        app = self.apps[kind]
        if app is None:
            app = self.io.az("ad", "app", "create", "--display-name", self.app_name(kind),
                             "--sign-in-audience", "AzureADMyOrg", write=True)
            self.apps[kind] = app
        self.state["apps"][kind] = {"id": app["id"], "appId": app["appId"]}
        self.save()
        principals = self.io.az("ad", "sp", "list", "--filter", f"appId eq '{app['appId']}'")
        require(len(principals) <= 1, "Unexpected multiple service principals.")
        principal = principals[0] if principals else self.io.az("ad", "sp", "create", "--id", app["appId"], write=True)
        self.state["apps"][kind]["principalId"] = principal["id"]
        self.save()
        owners = self.io.az("ad", "app", "owner", "list", "--id", app["id"])
        for owner in self.c["app_owner_user_ids"]:
            if owner not in {x["id"].lower() for x in owners}:
                self.io.az("ad", "app", "owner", "add", "--id", app["id"], "--owner-object-id", owner, write=True)

    def ensure_trust(self, kind):
        app = self.apps[kind]
        creds = self.io.az("ad", "app", "federated-credential", "list", "--id", app["id"])
        expected = {"issuer": "https://token.actions.githubusercontent.com",
                    "subject": self.subject_prefix + self.environment(kind),
                    "audiences": ["api://AzureADTokenExchange"]}
        require(all(all(x.get(k) == v for k, v in expected.items()) for x in creds),
                "Federated trust changed during onboarding; stopped.")
        if not creds:
            with tempfile.TemporaryDirectory(prefix="sentinel-") as temp:
                path = Path(temp) / "credential.json"
                path.write_text(json.dumps(dict(expected, name="github-" + self.environment(kind))), encoding="utf-8")
                self.io.az("ad", "app", "federated-credential", "create", "--id", app["id"],
                           "--parameters", str(path), write=True)
        path = self.variable_path(kind)
        current = self.io.gh("GET", path + "/AZURE_CLIENT_ID", missing=True)
        if current:
            require(current["value"].lower() == app["appId"].lower(), "Environment client ID conflict.")
        else:
            self.io.gh("POST", path, {"name": "AZURE_CLIENT_ID", "value": app["appId"]})

    def deploy_permissions(self):
        values = {"targetName": self.c["target"], "workspaceName": self.c["workspace_name"],
                  "previewPrincipalObjectId": self.state["apps"]["preview"]["principalId"],
                  "deploymentPrincipalObjectId": self.state["apps"]["deploy"]["principalId"]}
        with tempfile.TemporaryDirectory(prefix="sentinel-") as temp:
            params = Path(temp) / "parameters.json"
            params.write_text(json.dumps({"parameters": {k: {"value": v} for k, v in values.items()}}), encoding="utf-8")
            common = ("--subscription", self.c["subscription_id"], "--resource-group", self.c["resource_group"],
                      "--name", "onboard-" + self.c["target"], "--template-file", str(self.template),
                      "--parameters", "@" + str(params), "--mode", "Incremental")
            self.io.az("deployment", "group", "validate", *common)
            changes = self.io.az("deployment", "group", "what-if", *common, "--no-pretty-print")
            print("Azure permission changes:\n" + json.dumps(changes, indent=2))
            result = self.io.az("deployment", "group", "create", *common, write=True)
            require(result.get("properties", {}).get("provisioningState") == "Succeeded", "ARM deployment did not succeed.")
        self.state["permissions_deployed"] = True
        self.save()

    def ensure_groups(self):
        for group in self.c["human_groups"]:
            item, role = self.groups[group["name"]]
            if item is None:
                item = self.io.az("ad", "group", "create", "--display-name", group["name"],
                                  "--mail-nickname", group["name"], write=True)
            self.state["groups"][group["name"]] = {"id": item["id"]}
            self.save()
            for relation, desired, flag in (
                    ("owner", group["owner_user_ids"], "--owner-object-id"),
                    ("member", group["member_user_ids"], "--member-id")):
                existing = self.io.az("ad", "group", relation, "list", "--group", item["id"])
                for user in desired:
                    if user not in {x["id"].lower() for x in existing}:
                        self.io.az("ad", "group", relation, "add", "--group", item["id"], flag, user, write=True)
            assignments = self.io.az("role", "assignment", "list", "--scope", self.workspace["id"])
            if not any(x["principalId"].lower() == item["id"].lower() and
                       x["roleDefinitionId"].lower() == role.lower() and
                       x["scope"].lower() == self.workspace["id"].lower() for x in assignments):
                name = str(uuid.uuid5(uuid.NAMESPACE_URL, (self.workspace["id"] + item["id"] + role).lower()))
                self.io.az("role", "assignment", "create", "--name", name, "--assignee-object-id", item["id"],
                           "--assignee-principal-type", "Group", "--role", role, "--scope", self.workspace["id"], write=True)

    def manifest(self):
        q = json.dumps
        lines = ["version: 1", "target: " + self.c["target"], "client: " + self.c["client"],
                 "enabled: " + str(bool(self.c["initial_rule_path"])).lower(), "allow_missing_mitre: " + str(self.c["allow_missing_mitre"]).lower(), "azure:"]
        for key in ("tenant_id", "subscription_id", "resource_group", "workspace_name"):
            lines.append("  " + key + ": " + q(self.c[key]))
        lines.append("  workspace_id: " + q(self.workspace["customerId"]))
        if self.c["initial_rule_path"]:
            lines += ["rules:", "  - path: " + q(self.c["initial_rule_path"]),
                      "    overrides:", "      enabled: false"]
        else:
            lines.append("rules: []")
        return "\n".join(lines) + "\n"

    def inspect_target(self):
        filename = "workspace.yml" if self.c["workspace_label"] == "primary" else "workspace-" + self.c["workspace_label"] + ".yml"
        self.target_path = f"clients/{self.c['client']}/{filename}"
        self.branch = "codex/onboard-" + self.c["target"]
        path = self.repo_path + "/contents/" + self.target_path
        current = self.io.gh("GET", path + "?ref=main", missing=True)
        self.target_exists = current is not None
        if current:
            require(base64.b64decode(current["content"]).decode().strip() == self.manifest().strip(),
                    "Target already exists with different content. Review it manually; no overwrite performed.")
            return
        legacy_path = f"clients/{self.c['client']}/{self.c['workspace_label']}.yml"
        legacy = self.io.gh("GET", self.repo_path + "/contents/" + legacy_path + "?ref=main", missing=True)
        require(legacy is None, "Existing legacy manifest found: " + legacy_path +
                ". Rename it to " + self.target_path +
                " in a reviewed pull request first; do not create a duplicate target.")
        branch = self.io.gh("GET", self.repo_path + "/git/ref/heads/" + self.branch, missing=True)
        require(branch is None or self.state.get("branch") == self.branch,
                "Onboarding branch already exists outside this state file; review it first.")
        if branch:
            entry = self.io.gh("GET", path + "?ref=" + quote(self.branch, safe=""), missing=True)
            require(entry is None or base64.b64decode(entry["content"]).decode().strip() == self.manifest().strip(),
                    "Onboarding branch target changed; review manually.")

    def create_target_pr(self):
        if self.target_exists:
            print("Target already matches main; no pull request needed.")
            return
        refpath = self.repo_path + "/git/ref/heads/" + self.branch
        if self.io.gh("GET", refpath, missing=True) is None:
            head = self.io.gh("GET", self.repo_path + "/git/ref/heads/main")["object"]["sha"]
            self.io.gh("POST", self.repo_path + "/git/refs", {"ref": "refs/heads/" + self.branch, "sha": head})
            self.state["branch"] = self.branch
            self.save()
        path = self.repo_path + "/contents/" + self.target_path
        if self.io.gh("GET", path + "?ref=" + quote(self.branch, safe=""), missing=True) is None:
            self.io.gh("PUT", path, {"message": "Add Sentinel target " + self.c["target"],
                "branch": self.branch, "content": base64.b64encode(self.manifest().encode()).decode()})
        prs = self.io.pages(self.repo_path + "/pulls?state=open&head=" +
                            quote(self.c["github_owner"] + ":" + self.branch, safe="") + "&base=main")
        pr = prs[0] if prs else self.io.gh("POST", self.repo_path + "/pulls", {
            "title": "Onboard Sentinel workspace " + self.c["target"], "head": self.branch, "base": "main",
            "body": "Adds the client workspace target after Azure identity and permission onboarding. "
                    "Review target scope and CI results before merging. Any initial rule is explicitly disabled. "
                    "After merging, run preview on main, review the result, then run an approved deployment."})
        self.state["pull_request"] = pr["html_url"]
        self.save()
        print("Review pull request: " + pr["html_url"])

    def run(self):
        self.preflight()
        if all(self.apps.values()):
            require(self.apps["preview"]["appId"] != self.apps["deploy"]["appId"], "Preview and deployment apps must differ.")
        self.inspect_target()
        print(f"Target: {self.c['target']}\nWorkspace: {self.workspace['id']}")
        for kind in ("preview", "deploy"):
            print(f"{kind}: {self.app_name(kind)} -> {self.environment(kind)}")
            print("  OIDC subject: " + self.subject_prefix + self.environment(kind))
        print("Plan: two isolated apps; two main-only environments; scoped permissions; "
              f"{len(self.c['human_groups'])} optional groups; target pull request.")
        if not self.apply:
            print("READ-ONLY PLAN. No resources changed. Run again with --apply to perform onboarding.")
            return
        for kind in ("preview", "deploy"):
            self.ensure_environment(kind)
            self.ensure_app(kind)
        require(self.apps["preview"]["appId"] != self.apps["deploy"]["appId"], "App isolation check failed.")
        for kind in ("preview", "deploy"):
            self.ensure_trust(kind)
        self.deploy_permissions()
        self.ensure_groups()
        self.create_target_pr()
        print("Onboarding finished. Review environment administrator bypass in GitHub, merge the reviewed PR, "
              "then test OIDC, preview, and an approved disabled-rule deployment.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    state = args.state or args.config.with_suffix(".state.json")
    lock = Path(str(state) + ".lock")
    acquired = False
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        validate_config(config)
        if args.apply:
            lock.parent.mkdir(parents=True, exist_ok=True)
            try:
                with lock.open("x") as handle:
                    handle.write(str(os.getpid()))
                acquired = True
            except FileExistsError:
                raise Stop("Another run or stale lock exists: " + str(lock) + ". Check before removing it.")
        Onboard(config, CLI(args.apply), state, Path(__file__).with_name("azuredeploy.json")).run()
    except (Stop, OSError, ValueError, KeyError, TypeError) as error:
        print("STOPPED: " + str(error), file=sys.stderr)
        if args.apply:
            print("Changes may be partial. Keep the state file, correct the issue, and rerun; no rollback/deletion occurs.",
                  file=sys.stderr)
        return 1
    finally:
        if acquired:
            lock.unlink()
    return 0

if __name__ == "__main__":
    sys.exit(main())

