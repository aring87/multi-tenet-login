#!/usr/bin/env python3
"""Client onboarding via Azure Lighthouse delegation. No external Python packages.

Replaces full_onboarding.py's per-client identity work. Under the delegated model
the managing tenant holds ONE pair of app registrations with ONE federated
credential each, bound to two fixed GitHub environments. Onboarding a client is
therefore:

  1. deploy a subscription-scope Lighthouse delegation in the CLIENT tenant
  2. open a target-file pull request in the detection repository

No app registrations, no federated credentials, no GitHub environments, no custom
role definitions, and no role assignments are created per client.

Run signed in to the CLIENT tenant with Owner or User Access Administrator on the
target subscription. Read-only by default; pass --apply to make changes.
"""

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

MANAGED_SERVICES = "Microsoft.ManagedServices"
DELEGATION_WRITE = "Microsoft.Authorization/roleAssignments/write"


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
                "managing_tenant_id", "deploy_group_object_id", "read_group_object_id",
                "engineer_group_object_id", "preview_environment", "production_environment"}
    optional = {"initial_rule_path", "allow_missing_mitre", "msp_offer_name",
                "delegation_location"}
    require(isinstance(c, dict) and required <= c.keys(), "Config is missing required fields.")
    require(not (c.keys() - required - optional), "Config contains unrecognized fields.")
    c = dict(c)

    for key in ("tenant_id", "subscription_id", "managing_tenant_id",
                "deploy_group_object_id", "read_group_object_id", "engineer_group_object_id"):
        c[key] = guid(c[key], key)

    require(c["tenant_id"] != c["managing_tenant_id"],
            "Client tenant equals the managing tenant. A subscription cannot be delegated to "
            "the tenant it already lives in; use the in-tenant onboarding path instead.")
    require(len({c["deploy_group_object_id"], c["read_group_object_id"],
                 c["engineer_group_object_id"]}) == 3,
            "The deploy, read and engineer groups must be three distinct groups.")

    slug(c["client"], "client")
    slug(c["workspace_label"], "workspace_label")
    c["target"] = slug(c["client"] + "-" + c["workspace_label"], "combined target")

    for key in ("resource_group", "workspace_name"):
        require(isinstance(c[key], str) and c[key] and
                not re.search(r"""[<>&|^%!\r\n"'\x60]""", c[key]), f"Unsupported characters in {key}.")
    require(len(c["resource_group"]) <= 90 and not c["resource_group"].endswith("."),
            "Invalid resource_group.")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,61}[A-Za-z0-9]", c["workspace_name"]),
            "Invalid workspace_name.")

    for key in ("github_owner", "github_repo"):
        require(isinstance(c[key], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", c[key]), f"Invalid {key}.")
    for key in ("preview_environment", "production_environment"):
        require(isinstance(c[key], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", c[key]),
                f"Invalid {key}.")
    require(c["preview_environment"] != c["production_environment"],
            "Preview and production environments must differ.")

    require(type(c.get("allow_missing_mitre", False)) is bool, "allow_missing_mitre must be boolean.")
    c.setdefault("allow_missing_mitre", False)
    c.setdefault("msp_offer_name", "ISI Enterprises - Sentinel Detection Engineering")
    c.setdefault("delegation_location", "eastus")
    require(isinstance(c["msp_offer_name"], str) and 1 <= len(c["msp_offer_name"]) <= 200
            and not re.search(r"[\r\n]", c["msp_offer_name"]), "Invalid msp_offer_name.")
    require(re.fullmatch(r"[a-z0-9]+", c["delegation_location"]), "Invalid delegation_location.")

    path = c.get("initial_rule_path", "")
    require(isinstance(path, str) and (not path or (
        re.fullmatch(r"rules/sentinel/[A-Za-z0-9_./ -]+\.ya?ml", path) and
        all(p not in ("", ".", "..") for p in path.split("/")))), "Invalid initial_rule_path.")
    c["initial_rule_path"] = path
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
        require(self.template.is_file(),
                "Keep lighthouse-onboard.json beside lighthouse_onboarding.py.")
        self.binding = {k: self.c[k] for k in ("tenant_id", "subscription_id", "resource_group",
                                               "workspace_name", "target", "github_owner",
                                               "github_repo", "managing_tenant_id")}
        self.state = {"binding": self.binding}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            require(self.state.get("binding") == self.binding,
                    "State belongs to another client/scope. Use its matching config.")
        self.repo_path = f"repos/{self.c['github_owner']}/{self.c['github_repo']}"
        self.scope = f"/subscriptions/{self.c['subscription_id']}"

    def save(self):
        if self.apply:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.state_path)

    # ---------------- preflight ----------------

    def preflight(self):
        version = self.io.az("version")["azure-cli"]
        require(tuple(map(int, version.split(".")[:2])) >= (2, 76), "Azure CLI 2.76.0+ is required.")

        account = self.io.az("account", "show")
        require(account["tenantId"].lower() == self.c["tenant_id"],
                "Sign in to the CLIENT tenant. The delegation is created in the client's "
                "directory, not the managing tenant.")
        require(account["id"].lower() == self.c["subscription_id"],
                "Run az account set --subscription with the configured client subscription.")

        self.check_delegation_rights()
        self.check_provider()
        self.check_existing_delegation()

        self.workspace = self.io.az("monitor", "log-analytics", "workspace", "show",
                                    "--subscription", self.c["subscription_id"],
                                    "--resource-group", self.c["resource_group"],
                                    "--workspace-name", self.c["workspace_name"])
        expected = (f"{self.scope}/resourceGroups/{self.c['resource_group']}"
                    f"/providers/Microsoft.OperationalInsights/workspaces/{self.c['workspace_name']}")
        require(self.workspace["id"].lower() == expected.lower(), "Azure returned a different workspace.")
        guid(self.workspace["customerId"], "workspace GUID")

        self.check_repo()
        self.check_shared_environments()
        self.inspect_target()

    def check_delegation_rights(self):
        """Creating a delegation is a role assignment in the client's directory.
        Owner or User Access Administrator; Contributor and Sentinel roles are not enough."""
        permissions = self.io.az("role", "assignment", "list", "--scope", self.scope,
                                 "--include-inherited", "--assignee",
                                 self.io.az("account", "show")["user"]["name"])
        roles = {p["roleDefinitionName"] for p in permissions}
        require(roles & {"Owner", "User Access Administrator"},
                "The signed-in account needs Owner or User Access Administrator on the client "
                f"subscription to create a delegation. Found: {sorted(roles) or 'none'}. "
                "Activate JIT/PIM and sign in again, or ask the client to deploy the template.")
        self.state["caller_roles"] = sorted(roles)

    def check_provider(self):
        state = self.io.az("provider", "show", "--namespace", MANAGED_SERVICES,
                           "--subscription", self.c["subscription_id"])["registrationState"]
        self.provider_registered = state == "Registered"
        require(self.provider_registered or self.apply,
                f"{MANAGED_SERVICES} is {state}. Rerun with --apply to register it, or register "
                "it in the portal under Subscription > Resource providers.")

    def check_existing_delegation(self):
        existing = self.io.az("managedservices", "assignment", "list",
                              "--subscription", self.c["subscription_id"]) or []
        mine = [a for a in existing
                if str(a.get("properties", {}).get("registrationDefinitionId", "")).lower()
                .startswith(self.scope.lower())]
        self.existing_assignments = mine
        foreign = [a for a in existing if a not in mine]
        require(not foreign or self.state.get("delegation_deployed"),
                "This subscription already carries delegations from another offer. Review them "
                "before adding a second managing tenant.")

    def check_repo(self):
        self.repo = self.io.gh("GET", self.repo_path)
        require(self.repo.get("private") and not self.repo.get("archived"),
                "Use an active private repository.")
        require(self.repo.get("permissions", {}).get("admin"),
                "GitHub repository admin access is required.")
        require(self.repo["default_branch"] == "main",
                "This package expects the existing main-based pipeline.")
        require(self.io.gh("GET", self.repo_path + "/actions/permissions")["enabled"],
                "GitHub Actions is disabled.")
        if self.c["initial_rule_path"]:
            self.io.gh("GET", self.repo_path + "/contents/"
                       + quote(self.c["initial_rule_path"], safe="/") + "?ref=main")

    def check_shared_environments(self):
        """The two environments are shared by every client and created once, by hand.
        This verifies them rather than creating them - a missing one is a setup error,
        not something to silently provision per client."""
        for key, kind in (("preview_environment", "preview"), ("production_environment", "production")):
            name = self.c[key]
            env = self.io.gh("GET", self.repo_path + "/environments/" + quote(name, safe=""), missing=True)
            require(env is not None,
                    f"Shared environment '{name}' does not exist. Create the two shared "
                    "environments once, with AZURE_CLIENT_ID and production reviewers, before "
                    "onboarding clients.")
            value = self.io.gh("GET", self.repo_path + "/environments/" + quote(name, safe="")
                               + "/variables/AZURE_CLIENT_ID", missing=True)
            require(value and value.get("value"),
                    f"Shared environment '{name}' has no AZURE_CLIENT_ID variable.")
            guid(value["value"], f"{name} AZURE_CLIENT_ID")
            if kind == "production":
                rules = [r for r in env.get("protection_rules", []) if r["type"] == "required_reviewers"]
                require(rules and rules[0].get("reviewers"),
                        f"Shared environment '{name}' has no required reviewers. Deployments to "
                        "every client would be ungated.")

    # ---------------- target file ----------------

    def manifest(self):
        """IMPORTANT: tenant_id is the MANAGING tenant, not the client's.

        Under Lighthouse the workflow authenticates against the managing tenant and the
        delegated subscription is simply visible from there. Writing the client's tenant
        ID here makes az login fail in a way that does not point at the cause."""
        q = json.dumps
        lines = ["version: 1",
                 "target: " + self.c["target"],
                 "client: " + self.c["client"],
                 "enabled: " + str(bool(self.c["initial_rule_path"])).lower(),
                 "allow_missing_mitre: " + str(self.c["allow_missing_mitre"]).lower(),
                 "azure:",
                 "  tenant_id: " + q(self.c["managing_tenant_id"]) + "   # managing tenant",
                 "  subscription_id: " + q(self.c["subscription_id"]),
                 "  resource_group: " + q(self.c["resource_group"]),
                 "  workspace_name: " + q(self.c["workspace_name"]),
                 "  workspace_id: " + q(self.workspace["customerId"])]
        if self.c["initial_rule_path"]:
            lines += ["rules:", "  - path: " + q(self.c["initial_rule_path"]),
                      "    overrides:", "      enabled: false"]
        else:
            lines.append("rules: []")
        return "\n".join(lines) + "\n"

    def inspect_target(self):
        filename = ("workspace.yml" if self.c["workspace_label"] in ("primary", "workspace")
                    else "workspace-" + self.c["workspace_label"] + ".yml")
        self.target_path = f"clients/{self.c['client']}/{filename}"
        self.branch = "onboard/" + self.c["target"]
        path = self.repo_path + "/contents/" + self.target_path
        current = self.io.gh("GET", path + "?ref=main", missing=True)
        self.target_exists = current is not None
        if current:
            require(base64.b64decode(current["content"]).decode().strip() == self.manifest().strip(),
                    "Target already exists with different content. Review it manually; no "
                    "overwrite performed.")
            return
        branch = self.io.gh("GET", self.repo_path + "/git/ref/heads/" + self.branch, missing=True)
        require(branch is None or self.state.get("branch") == self.branch,
                "Onboarding branch already exists outside this state file; review it first.")
        if branch:
            entry = self.io.gh("GET", path + "?ref=" + quote(self.branch, safe=""), missing=True)
            require(entry is None
                    or base64.b64decode(entry["content"]).decode().strip() == self.manifest().strip(),
                    "Onboarding branch target changed; review manually.")

    def create_target_pr(self):
        if self.target_exists:
            print("Target already matches main; no pull request needed.")
            return
        refpath = self.repo_path + "/git/ref/heads/" + self.branch
        if self.io.gh("GET", refpath, missing=True) is None:
            head = self.io.gh("GET", self.repo_path + "/git/ref/heads/main")["object"]["sha"]
            self.io.gh("POST", self.repo_path + "/git/refs",
                       {"ref": "refs/heads/" + self.branch, "sha": head})
            self.state["branch"] = self.branch
            self.save()
        path = self.repo_path + "/contents/" + self.target_path
        if self.io.gh("GET", path + "?ref=" + quote(self.branch, safe=""), missing=True) is None:
            self.io.gh("PUT", path, {"message": "Add Sentinel target " + self.c["target"],
                                     "branch": self.branch,
                                     "content": base64.b64encode(self.manifest().encode()).decode()})
        prs = self.io.pages(self.repo_path + "/pulls?state=open&head="
                            + quote(self.c["github_owner"] + ":" + self.branch, safe="") + "&base=main")
        pr = prs[0] if prs else self.io.gh("POST", self.repo_path + "/pulls", {
            "title": "Onboard Sentinel workspace " + self.c["target"],
            "head": self.branch, "base": "main",
            "body": "Adds the client workspace target. Access is via an Azure Lighthouse "
                    "delegation, so no per-client identity, credential, environment or role "
                    "definition was created. tenant_id is the MANAGING tenant by design. "
                    "Any initial rule is explicitly disabled. After merging, run preview on "
                    "main, review the what-if, then run an approved deployment."})
        self.state["pull_request"] = pr["html_url"]
        self.save()
        print("Review pull request: " + pr["html_url"])

    # ---------------- delegation ----------------

    def register_provider(self):
        if self.provider_registered:
            return
        self.io.az("provider", "register", "--namespace", MANAGED_SERVICES,
                   "--subscription", self.c["subscription_id"], "--wait", write=True)
        state = self.io.az("provider", "show", "--namespace", MANAGED_SERVICES,
                           "--subscription", self.c["subscription_id"])["registrationState"]
        require(state == "Registered", f"{MANAGED_SERVICES} did not reach Registered ({state}).")
        self.provider_registered = True

    def deploy_delegation(self):
        values = {"mspOfferName": self.c["msp_offer_name"],
                  "managedByTenantId": self.c["managing_tenant_id"],
                  "deployGroupObjectId": self.c["deploy_group_object_id"],
                  "readGroupObjectId": self.c["read_group_object_id"],
                  "engineerGroupObjectId": self.c["engineer_group_object_id"]}
        with tempfile.TemporaryDirectory(prefix="sentinel-") as temp:
            params = Path(temp) / "parameters.json"
            params.write_text(json.dumps({"parameters": {k: {"value": v} for k, v in values.items()}}),
                              encoding="utf-8")
            common = ("--subscription", self.c["subscription_id"],
                      "--location", self.c["delegation_location"],
                      "--name", "lighthouse-onboard-" + self.c["target"],
                      "--template-file", str(self.template),
                      "--parameters", "@" + str(params))
            self.io.az("deployment", "sub", "validate", *common)
            result = self.io.az("deployment", "sub", "create", *common, write=True)
        require(result.get("properties", {}).get("provisioningState") == "Succeeded",
                "Delegation deployment did not succeed.")
        self.state["delegation_deployed"] = True
        self.save()
        self.verify_delegation()

    def verify_delegation(self):
        """The definition and the assignment are two resources. A succeeded deployment
        with no assignment has been observed, so check for the assignment explicitly
        rather than trusting provisioningState."""
        assignments = self.io.az("managedservices", "assignment", "list",
                                 "--subscription", self.c["subscription_id"]) or []
        require(assignments,
                "The registration definition was created but no registration assignment "
                "exists, so nothing is delegated yet. Redeploy, or bind it in the client "
                "portal under Service providers > Service provider offers > Delegate "
                "subscriptions.")
        print(f"Delegation active: {len(assignments)} assignment(s) on this subscription.")

    # ---------------- run ----------------

    def run(self):
        self.preflight()
        print(f"Client tenant:    {self.c['tenant_id']}")
        print(f"Managing tenant:  {self.c['managing_tenant_id']}")
        print(f"Subscription:     {self.c['subscription_id']}")
        print(f"Target:           {self.c['target']}")
        print(f"Workspace:        {self.workspace['id']}")
        print(f"Workspace GUID:   {self.workspace['customerId']}")
        print(f"Caller roles:     {', '.join(self.state.get('caller_roles', []))}")
        print(f"Provider:         {'Registered' if self.provider_registered else 'NOT registered'}")
        print(f"Delegation:       {len(self.existing_assignments)} existing assignment(s)")
        print(f"Environments:     {self.c['preview_environment']}, {self.c['production_environment']} (shared, verified)")
        print("Plan: register provider if needed; deploy one subscription-scope Lighthouse "
              "delegation granting three managing-tenant groups built-in roles; open a target "
              "pull request. No apps, credentials, environments or role definitions are created.")
        if not self.apply:
            print("READ-ONLY PLAN. No resources changed. Run again with --apply to onboard.")
            return
        self.register_provider()
        self.deploy_delegation()
        self.create_target_pr()
        print("Onboarding finished. Merge the reviewed PR, run preview on main, review the "
              "what-if, then run an approved deployment.")


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
        Onboard(config, CLI(args.apply), state,
                Path(__file__).with_name("lighthouse-onboard.json")).run()
    except (Stop, OSError, ValueError, KeyError, TypeError) as error:
        print("STOPPED: " + str(error), file=sys.stderr)
        if args.apply:
            print("Changes may be partial. Keep the state file, correct the issue, and rerun; "
                  "no rollback/deletion occurs.", file=sys.stderr)
        return 1
    finally:
        if acquired:
            lock.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
