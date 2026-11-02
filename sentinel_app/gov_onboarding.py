"""GCC High (Azure Government) client onboarding through a CyberQP JIT session.

Commercial Azure Lighthouse cannot reach an Azure Government tenant, and ISI has no
Gov managing tenant, so each GCC High client gets its own pair of pipeline app
registrations inside the client's tenant. Run signed in to the client's Gov tenant
with the CyberQP JIT Global Administrator account. The steps are:

  1. create (or reuse) a preview app and a deploy app registration, each with a
     service principal
  2. add one GitHub OIDC federated credential to each app, trusting only its own
     shared environment (sentinel-preview / sentinel-production)
  3. assign built-in roles on the workspace resource group:
     preview -> Microsoft Sentinel Reader + Log Analytics Reader
     deploy  -> Microsoft Sentinel Contributor
  4. open a target-file pull request with the client tenant ID, the Gov cloud and
     both client IDs filled in

No secrets are created. Every step is checked first and skipped when it already
exists, so an interrupted run can simply be run again. Read-only unless applied.
"""
import base64
import json
import re
import tempfile
import time
from pathlib import Path

from .lighthouse_onboarding import Onboard, Stop, guid, require, validate_target
from .same_tenant_onboarding import SameTenantOnboard
from .yaml_identifiers import yaml_slug

GOV_CLOUD = "AzureUSGovernment"
ISSUER = "https://token.actions.githubusercontent.com"
AUDIENCE = "api://AzureADTokenExchangeUSGov"
# This organization issues immutable-ID subject claims (see detection-as-code
# docs/azure-identities.md). Use Actions > Inspect Azure trust subject to confirm.
DEFAULT_SUBJECT_FORMAT = "repo:{owner}@{owner_id}/{repo}@{repo_id}:environment:{environment}"
DEFAULT_APP_PREFIX = "ISI Sentinel Pipeline"
DEFAULT_LABEL = "workspace-gcch"
KINDS = ("preview", "deploy")
ROLES = {
    "preview": (("Microsoft Sentinel Reader", "8d289c81-5878-46d4-8554-54e1e3d8b5cb"),
                ("Log Analytics Reader", "73c42c96-874c-492b-b04d-ab87d138a893")),
    "deploy": (("Microsoft Sentinel Contributor", "ab8e14d6-4a74-4a29-9ba8-549422addade"),),
}
CALLER_ROLES = {"Owner", "User Access Administrator"}
ASSIGNMENT_RETRIES = 6
ASSIGNMENT_WAIT_SECONDS = 10

REQUIRED = {"tenant_id", "subscription_id", "resource_group", "workspace_name", "client",
            "workspace_label", "github_owner", "github_repo", "preview_environment",
            "production_environment"}
OPTIONAL = {"initial_rule_path", "allow_missing_mitre", "app_name_prefix", "subject_format"}


def validate_gov_config(c):
    require(isinstance(c, dict) and REQUIRED <= c.keys(), "Config is missing required fields.")
    require(not (c.keys() - REQUIRED - OPTIONAL), "Config contains unrecognized fields.")
    c = dict(c)
    for key in ("tenant_id", "subscription_id"):
        c[key] = guid(c[key], key)
    c["target"] = validate_target(c["client"], c["workspace_label"])
    for key in ("resource_group", "workspace_name"):
        require(isinstance(c[key], str) and c[key] and
                not re.search(r"""[<>&|^%!\r\n"'\x60]""", c[key]), f"Unsupported characters in {key}.")
    require(len(c["resource_group"]) <= 90 and not c["resource_group"].endswith("."), "Invalid resource_group.")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,61}[A-Za-z0-9]", c["workspace_name"]),
            "Invalid workspace_name.")
    for key in ("github_owner", "github_repo", "preview_environment", "production_environment"):
        require(isinstance(c[key], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", c[key]), f"Invalid {key}.")
    require(c["preview_environment"] != c["production_environment"],
            "Preview and production environments must differ.")
    require(type(c.get("allow_missing_mitre", False)) is bool, "allow_missing_mitre must be boolean.")
    c.setdefault("allow_missing_mitre", False)
    c.setdefault("app_name_prefix", DEFAULT_APP_PREFIX)
    require(isinstance(c["app_name_prefix"], str) and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,90}", c["app_name_prefix"]),
            "Invalid app_name_prefix.")
    c.setdefault("subject_format", DEFAULT_SUBJECT_FORMAT)
    require(isinstance(c["subject_format"], str) and "{environment}" in c["subject_format"]
            and not re.search(r"[\s\"'\\]", c["subject_format"]), "Invalid subject_format.")
    path = c.get("initial_rule_path", "")
    require(isinstance(path, str) and (not path or (
        re.fullmatch(r"rules/sentinel/[A-Za-z0-9_./ -]+\.ya?ml", path) and
        all(p not in ("", ".", "..") for p in path.split("/")))), "Invalid initial_rule_path.")
    c["initial_rule_path"] = path
    return c


class GovOnboard(Onboard):
    validate = staticmethod(validate_gov_config)
    # Same duplicate-registration guard as in-tenant registration.
    check_duplicate_workspace = SameTenantOnboard.check_duplicate_workspace

    def __init__(self, config, cli, state_path):
        self.c, self.io = self.validate(config), cli
        self.apply = cli.apply
        self.state_path = Path(state_path)
        self.binding = {k: self.c[k] for k in ("tenant_id", "subscription_id", "resource_group",
                                               "workspace_name", "target", "github_owner", "github_repo")}
        self.binding["cloud"] = GOV_CLOUD
        self.state = {"binding": self.binding}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            require(self.state.get("binding") == self.binding,
                    "State belongs to another client/scope. Use its matching settings.")
        self.state.setdefault("apps", {})
        self.repo_path = f"repos/{self.c['github_owner']}/{self.c['github_repo']}"
        self.scope = f"/subscriptions/{self.c['subscription_id']}"
        self.rg_scope = self.scope + "/resourceGroups/" + self.c["resource_group"]
        self.apps = {}

    # ---------------- names ----------------

    def app_name(self, kind):
        return self.c["app_name_prefix"] + " - " + ("Preview" if kind == "preview" else "Deploy")

    def environment(self, kind):
        return self.c["preview_environment" if kind == "preview" else "production_environment"]

    def subject(self, kind):
        return self.c["subject_format"].format(owner=self.c["github_owner"], repo=self.c["github_repo"],
                                               owner_id=self.repo["owner"]["id"], repo_id=self.repo["id"],
                                               environment=self.environment(kind))

    # ---------------- preflight ----------------

    def preflight(self):
        cloud = self.io.az("cloud", "show")
        require(cloud.get("name") == GOV_CLOUD,
                "This is GCC High onboarding, but the session is signed in to " + str(cloud.get("name")) +
                ". Sign in again with the client's .onmicrosoft.us tenant; the app switches to Azure "
                "Government automatically for GCC High tenants.")
        account = self.io.az("account", "show")
        require(account["tenantId"].lower() == self.c["tenant_id"] and account["id"].lower() == self.c["subscription_id"],
                "Session/destination mismatch. Sign in to the client's GCC High tenant and select this subscription.")
        self.workspace = self.io.az("monitor", "log-analytics", "workspace", "show",
                                    "--subscription", self.c["subscription_id"],
                                    "--resource-group", self.c["resource_group"],
                                    "--workspace-name", self.c["workspace_name"])
        expected = self.rg_scope + "/providers/Microsoft.OperationalInsights/workspaces/" + self.c["workspace_name"]
        require(self.workspace["id"].lower() == expected.lower(), "Azure returned a different workspace.")
        guid(self.workspace["customerId"], "workspace GUID")
        self.check_caller_rights()
        self.check_repo()
        self.check_shared_environments()
        self.check_duplicate_workspace()
        for kind in KINDS:
            self.apps[kind] = self.find_app(kind)
        self.check_target_path()

    def check_caller_rights(self):
        """Role assignments need Owner or User Access Administrator. A JIT Global Administrator
        has neither until Access management for Azure resources is turned on."""
        token = self.io.az("account", "get-access-token", "--tenant", self.c["tenant_id"])["accessToken"]
        principal = guid(_claims(token).get("oid", ""), "signed-in user object ID")
        rows = self.io.az("role", "assignment", "list", "--scope", self.rg_scope, "--include-inherited",
                          "--include-groups", "--assignee-object-id", principal,
                          "--fill-principal-name", "false") or []
        roles = {r.get("roleDefinitionName") for r in rows}
        require(roles & CALLER_ROLES,
                "The signed-in account needs Owner or User Access Administrator on the workspace resource "
                f"group to grant the pipeline roles. Found: {sorted(r for r in roles if r) or 'none'}. "
                "With the CyberQP Global Administrator account, turn on Microsoft Entra ID > Properties > "
                "Access management for Azure resources, sign in again, and preview again. Turn it off when finished.")
        self.state["caller_roles"] = sorted(roles & CALLER_ROLES)

    def find_app(self, kind):
        name = self.app_name(kind)
        apps = [a for a in self.io.az("ad", "app", "list", "--display-name", name) or []
                if a.get("displayName") == name]
        require(len(apps) <= 1, f"More than one app registration is named '{name}' in this tenant. "
                                "Remove or rename the extra one, then preview again.")
        known = self.state["apps"].get(kind, {}).get("app_id")
        if not apps:
            require(not known, f"The '{name}' app registration recorded for this client ({known}) no longer "
                               "exists. Review the tenant before recreating it.")
            return {"name": name, "app_id": None, "sp_id": None, "credentials": [], "roles": []}
        app_id = guid(apps[0]["appId"], name + " application ID")
        require(not known or known == app_id, f"'{name}' is a different app registration from the one "
                                              "recorded for this client. Review it before continuing.")
        sps = self.io.az("ad", "sp", "list", "--filter", f"appId eq '{app_id}'") or []
        sp_id = guid(sps[0]["id"], name + " service principal ID") if sps else None
        credentials = self.io.az("ad", "app", "federated-credential", "list", "--id", app_id) or []
        roles = self.role_assignments(sp_id) if sp_id else []
        return {"name": name, "app_id": app_id, "sp_id": sp_id, "credentials": credentials, "roles": roles}

    def role_assignments(self, principal):
        rows = self.io.az("role", "assignment", "list", "--scope", self.rg_scope,
                          "--assignee-object-id", principal, "--fill-principal-name", "false") or []
        return [r.get("roleDefinitionId", "").rsplit("/", 1)[-1].lower() for r in rows
                if r.get("scope", "").lower() == self.rg_scope.lower() and not r.get("condition")]

    def credential_status(self, kind):
        app = self.apps[kind]
        subject, env = self.subject(kind), self.environment(kind)
        for item in app["credentials"]:
            if item.get("subject") == subject and item.get("issuer") == ISSUER:
                require(AUDIENCE in item.get("audiences", []),
                        f"{app['name']} trusts {subject} with audience {item.get('audiences')}. "
                        f"Azure Government requires {AUDIENCE}; fix that credential in the portal.")
                return "present"
            require(item.get("name") != "github-" + env,
                    f"{app['name']} already has a credential named github-{env} with subject "
                    f"{item.get('subject')}. Review it before continuing.")
        return "missing"

    def check_target_path(self):
        self.target_path = f"clients/{self.c['client']}/{self.c['target']}.yml"
        self.branch = "onboard/" + self.c["target"]
        if all(self.apps[k]["app_id"] for k in KINDS):
            self.inspect_target()  # Full comparison is possible once both client IDs are known.
            return
        current = self.io.gh("GET", self.repo_path + "/contents/" + self.target_path + "?ref=main", missing=True)
        require(current is None, self.target_path + " already exists on main, but this tenant has no matching "
                                 "pipeline app registrations. Review the target file before onboarding.")
        self.target_exists = False
        branch = self.io.gh("GET", self.repo_path + "/git/ref/heads/" + self.branch, missing=True)
        require(branch is None or self.state.get("branch") == self.branch,
                "Onboarding branch already exists outside this state file; review it first.")

    # ---------------- target file ----------------

    def manifest(self):
        q = json.dumps
        lines = ["version: 1",
                 "target: " + yaml_slug(self.c["target"]),
                 "client: " + yaml_slug(self.c["client"]),
                 "enabled: " + str(bool(self.c["initial_rule_path"])).lower(),
                 "allow_missing_mitre: " + str(self.c["allow_missing_mitre"]).lower(),
                 "azure:",
                 "  tenant_id: " + q(self.c["tenant_id"]) + "   # client GCC High tenant",
                 "  subscription_id: " + q(self.c["subscription_id"]),
                 "  resource_group: " + q(self.c["resource_group"]),
                 "  workspace_name: " + q(self.c["workspace_name"]),
                 "  workspace_id: " + q(self.workspace["customerId"]),
                 "  cloud: " + q(GOV_CLOUD),
                 "  preview_client_id: " + q(self.apps["preview"]["app_id"] or ""),
                 "  deploy_client_id: " + q(self.apps["deploy"]["app_id"] or "")]
        if self.c["initial_rule_path"]:
            lines += ["rules:", "  - path: " + q(self.c["initial_rule_path"]),
                      "    overrides:", "      enabled: false"]
        else:
            lines.append("rules: []")
        return "\n".join(lines) + "\n"

    def access_description(self):
        return ("Adds a GCC High (Azure Government) client workspace target. Access is through two "
                "app registrations in the client's own tenant (preview: Microsoft Sentinel Reader and "
                "Log Analytics Reader; deploy: Microsoft Sentinel Contributor, both on the workspace "
                "resource group), each trusting one shared GitHub environment through OIDC. No secrets "
                "were created. ")

    def tenant_note(self):
        return "tenant_id is the CLIENT's Gov tenant, unlike commercial Lighthouse targets. "

    # ---------------- changes ----------------

    def ensure_app(self, kind):
        app = self.apps[kind]
        if not app["app_id"]:
            created = self.io.az("ad", "app", "create", "--display-name", app["name"],
                                 "--sign-in-audience", "AzureADMyOrg", write=True)
            app["app_id"] = guid(created["appId"], app["name"] + " application ID")
            print(f"Created app registration {app['name']}: {app['app_id']}")
        self.state["apps"].setdefault(kind, {})["app_id"] = app["app_id"]
        self.save()
        if not app["sp_id"]:
            sp = self.io.az("ad", "sp", "create", "--id", app["app_id"], write=True)
            app["sp_id"] = guid(sp["id"], app["name"] + " service principal ID")
            print(f"Created service principal for {app['name']}: {app['sp_id']}")
        self.state["apps"][kind]["sp_id"] = app["sp_id"]
        self.save()

    def ensure_credential(self, kind):
        if self.credential_status(kind) == "present":
            return
        app, env = self.apps[kind], self.environment(kind)
        body = {"name": "github-" + env, "issuer": ISSUER, "subject": self.subject(kind),
                "audiences": [AUDIENCE], "description": "GitHub Actions " + env + " environment"}
        with tempfile.TemporaryDirectory(prefix="sentinel-") as temp:
            path = Path(temp) / "credential.json"  # A file keeps JSON quotes away from the Windows shell.
            path.write_text(json.dumps(body), encoding="utf-8")
            created = self.io.az("ad", "app", "federated-credential", "create", "--id", app["app_id"],
                                 "--parameters", str(path), write=True)
        app["credentials"].append(created or body)
        print(f"Added federated credential to {app['name']}: {body['subject']}")

    def ensure_roles(self, kind):
        app = self.apps[kind]
        for name, role_id in ROLES[kind]:
            if role_id in app["roles"]:
                continue
            for attempt in range(ASSIGNMENT_RETRIES):
                try:
                    self.io.az("role", "assignment", "create", "--assignee-object-id", app["sp_id"],
                               "--assignee-principal-type", "ServicePrincipal", "--role", role_id,
                               "--scope", self.rg_scope, write=True)
                    break
                except Stop as error:
                    # A brand-new service principal can take a short while to replicate.
                    if "PrincipalNotFound" not in str(error) or attempt == ASSIGNMENT_RETRIES - 1:
                        raise
                    time.sleep(ASSIGNMENT_WAIT_SECONDS)
            app["roles"].append(role_id)
            print(f"Assigned {name} to {app['name']} on {self.rg_scope}")

    # ---------------- run ----------------

    def plan_lines(self):
        lines = []
        for kind in KINDS:
            app = self.apps[kind]
            lines.append(f"{app['name']}:")
            lines.append("  App registration:   " + (app["app_id"] or "create"))
            lines.append("  Service principal:  " + (app["sp_id"] or "create"))
            lines.append(f"  OIDC trust:         {self.credential_status(kind)} -> {self.subject(kind)}")
            for name, role_id in ROLES[kind]:
                lines.append(f"  {name + ':':<34}{'present' if role_id in app['roles'] else 'assign'}")
        return lines

    def run(self):
        self.preflight()
        print("GCC High onboarding (Azure Government)")
        print(f"Client tenant:    {self.c['tenant_id']}")
        print(f"Subscription:     {self.c['subscription_id']}")
        print(f"Target:           {self.c['target']} -> {self.target_path}")
        print(f"Workspace:        {self.workspace['id']}")
        print(f"Workspace GUID:   {self.workspace['customerId']}")
        print(f"Caller roles:     {', '.join(self.state['caller_roles'])}")
        print(f"Environments:     {self.c['preview_environment']}, {self.c['production_environment']} (shared, verified)")
        for line in self.plan_lines():
            print(line)
        print("Pull request:     " + ("none needed; target already matches main" if self.target_exists else
                                     "add the target with tenant_id, cloud, preview_client_id and deploy_client_id"))
        if not self.apply:
            print("READ-ONLY PLAN. Nothing in Azure or GitHub changed.")
            return
        for kind in KINDS:
            self.ensure_app(kind)
            self.ensure_credential(kind)
            self.ensure_roles(kind)
        self.inspect_target()
        self.create_target_pr()
        print("GCC High onboarding finished. Merge the reviewed PR, run preview on main, review the "
              "what-if, then run an approved deployment.")


def _claims(token):
    try:
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        require(isinstance(claims, dict), "Invalid ARM token claims.")
        return claims
    except (IndexError, ValueError, TypeError, UnicodeError):
        raise Stop("Could not read the signed-in identity from the Azure token. Sign in again.") from None
