"""Desktop onboarding backend. Network operations only occur after a UI action."""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from full_onboarding import CLI, Onboard, Stop, guid, require, validate_config

BASE = Path(__file__).resolve().parent
DATA = BASE / "desktop-data"

def fingerprint(config, mode, principals):
    return hashlib.sha256(json.dumps([config, mode, principals], sort_keys=True).encode()).hexdigest()

def workspace_record(item, subscription, tenant):
    resource_id = item.get("id", "")
    match = re.fullmatch(r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/Microsoft\.OperationalInsights/workspaces/([^/]+)",
                         resource_id, flags=re.I)
    require(match is not None, "Azure returned an invalid workspace resource ID.")
    require(match[1].lower() == subscription.lower(), "Workspace belongs to a different subscription.")
    return dict(tenant_id=guid(tenant, "tenant"), subscription_id=guid(subscription, "subscription"),
                resource_group=match[2], workspace_name=match[3],
                workspace_id=guid(item.get("customerId"), "workspace GUID"), resource_id=resource_id)

CYBERQP_PORTALS = {
    "US": "https://admin.getquickpass.com/",
    "EU": "https://eu-admin.getquickpass.com/",
    "Canada": "https://ca.admin.cyberqp.com/",
}

def validate_tenant_hint(value):
    hint = (value or "").strip().lower()
    guidance = ("Enter the client's Directory (tenant) ID or verified tenant domain "
                "(for example client.onmicrosoft.com). An Azure portal URL is not a tenant. "
                "Find the ID in Microsoft Entra ID > Overview.")
    portals = {"azure.portal.com", "portal.azure.com", "entra.microsoft.com",
               "login.microsoftonline.com", "portal.office.com", "admin.microsoft.com",
               "azure.microsoft.com"}
    require(hint not in portals and "://" not in hint and "/" not in hint and "@" not in hint, guidance)
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", hint):
        return guid(hint, "tenant ID")
    labels = hint.split(".")
    require(len(hint) <= 253 and len(labels) >= 2 and not all(x.isdigit() for x in labels)
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in labels)
            and re.search(r"[a-z]", labels[-1]), guidance)
    return hint

class Session:
    def __init__(self, folder=None, logger=None):
        self.folder = Path(folder or DATA / "sessions" / str(uuid.uuid4()))
        self.folder.mkdir(parents=True, exist_ok=True)
        self.log = logger or (lambda message: None)
        self.env = dict(os.environ, AZURE_CONFIG_DIR=str(self.folder),
                        AZURE_CORE_LOGIN_EXPERIENCE_V2="off",
                        AZURE_CORE_ENABLE_BROKER_ON_WINDOWS="false",
                        MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*",
                        PYTHONIOENCODING="utf-8")
        self.az_exe = shutil.which("az")
        self.gh_exe = shutil.which("gh")
        self.account = None

    def execute(self, executable, args, data=None, timeout=900):
        require(executable, "Required tool is missing. Install Azure CLI / GitHub CLI and restart this app.")
        if executable.lower().endswith((".cmd", ".bat")):
            require(all(not re.search(r'[&|<>^%!\r\n"]', str(x)) for x in args),
                    "An argument contains unsupported Windows shell characters.")
        try:
            result = subprocess.run([executable, *map(str, args)], input=data or "", text=True,
                encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self.env, shell=False, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise Stop("The operation timed out. A cloud change may still be running; inspect its status before retrying.")
        return result

    def az(self, *args):
        result = self.execute(self.az_exe, [*args, "--only-show-errors", "--output", "json"])
        require(result.returncode == 0, result.stderr.strip() or "Azure operation failed.")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def login(self, tenant_hint):
        tenant_hint = validate_tenant_hint(tenant_hint)
        self.log("Complete Microsoft sign-in with the client's authorized account and MFA.")
        # Browser sign-in by default. The desktop can opt into the Windows broker for policies that require it.
        accounts = self.az("login", "--tenant", tenant_hint, "--allow-no-subscriptions")
        subscriptions = [x for x in accounts if x.get("state") == "Enabled" and x.get("id") != x.get("tenantId")]
        if re.fullmatch(r"[0-9a-fA-F-]{36}", tenant_hint):
            require(all(x["tenantId"].lower() == tenant_hint.lower() for x in subscriptions),
                    "Sign-in returned a different tenant.")
        require(subscriptions, "Signed in, but no enabled subscriptions are visible. Check the account's Azure subscription access.")
        return subscriptions

    def discover(self, subscription, tenant):
        self.az("account", "set", "--subscription", subscription)
        account = self.az("account", "show")
        require(account["tenantId"].lower() == tenant.lower() and account["id"].lower() == subscription.lower(),
                "Active Azure account does not match the selected client/subscription.")
        self.account = account
        self.log("Reading accessible Log Analytics workspaces in the selected subscription...")
        rows = self.az("monitor", "log-analytics", "workspace", "list", "--subscription", subscription)
        return [workspace_record(x, subscription, tenant) for x in rows]

    def verify(self, config):
        account = self.az("account", "show")
        require(account["id"].lower() == config["subscription_id"].lower() and
                account["tenantId"].lower() == config["tenant_id"].lower(), "Session/destination mismatch. Sign in again.")
        return account

    def logout(self):
        if self.az_exe:
            try:
                self.az("logout")
            except Stop as error:
                if "there are no active accounts" not in str(error).lower():
                    raise
                self.log("No Azure account is active in this app session; continuing.")
        self.account = None

class DesktopCLI(CLI):
    def __init__(self, session, apply):
        self.apply = apply
        self.session = session
        self.azure, self.github = session.az_exe, session.gh_exe
        require(self.azure and self.github, "Full onboarding requires both Azure CLI and GitHub CLI.")

    def run(self, executable, args, write=False, data=None, missing=False):
        require(not write or self.apply, "Write blocked: run a reviewed plan before applying.")
        self.session.log(("Applying: " if write else "Checking: ") + " ".join(str(a) for a in args[:3]))
        result = self.session.execute(executable, args, data)
        if result.returncode:
            if missing and re.search(r"HTTP 404\b", result.stderr):
                return None
            raise Stop(result.stderr.strip() or "Operation failed.")
        return json.loads(result.stdout) if result.stdout.strip() else None

def config_from_workspace(workspace, client, label, fields, extras=None):
    require(workspace, "Select a discovered workspace first.")
    config = dict(tenant_id=workspace["tenant_id"], subscription_id=workspace["subscription_id"],
                  resource_group=workspace["resource_group"], workspace_name=workspace["workspace_name"],
                  client=client, workspace_label=label, github_owner=fields["github_owner"],
                  github_repo=fields["github_repo"], oidc_subject_format=fields["oidc_subject_format"],
                  app_owner_user_ids=[x.strip() for x in fields["app_owners"].split(",") if x.strip()],
                  production_reviewers=[{"type": fields["reviewer_type"], "name": x.strip()}
                                        for x in fields["reviewers"].split(",") if x.strip()],
                  initial_rule_path=fields["rule_path"], allow_missing_mitre=fields["allow_missing_mitre"],
                  human_groups=(extras or {}).get("human_groups", []))
    for key in ("existing_preview_app_client_id", "existing_deploy_app_client_id"):
        if fields.get(key):
            config[key] = fields[key]
    # Validation derives target; do not include that internal field in exported configuration.
    validate_config(config)
    return config

def permission_run(session, workspace, target, principals, apply=False):
    require(re.fullmatch(r"[a-z][a-z0-9-]{1,49}", target), "Use a lowercase target ID.")
    first, second = [guid(x, "service principal Object ID") for x in principals]
    require(first != second, "Preview and deployment service principals must differ.")
    session.verify(workspace)
    for value in (first, second):
        principal = session.az("ad", "sp", "show", "--id", value)
        require(principal["id"].lower() == value, "Use the enterprise application's Object ID, not the app's client ID.")
    current = session.az("monitor", "log-analytics", "workspace", "show",
                         "--subscription", workspace["subscription_id"], "--resource-group", workspace["resource_group"],
                         "--workspace-name", workspace["workspace_name"])
    verified = workspace_record(current, workspace["subscription_id"], workspace["tenant_id"])
    require(verified["workspace_id"] == workspace["workspace_id"], "Workspace changed since discovery.")
    params = {"parameters": {k: {"value": v} for k, v in dict(targetName=target,
        workspaceName=workspace["workspace_name"], previewPrincipalObjectId=first,
        deploymentPrincipalObjectId=second).items()}}
    folder = DATA / "runs" / target
    folder.mkdir(parents=True, exist_ok=True)
    paramfile = folder / "permissions.parameters.json"
    paramfile.write_text(json.dumps(params, indent=2), encoding="utf-8")
    common = ["--subscription", workspace["subscription_id"], "--resource-group", workspace["resource_group"],
              "--name", "onboard-" + target, "--template-file", str(BASE / "azuredeploy.json"),
              "--parameters", "@" + str(paramfile), "--mode", "Incremental"]
    session.log("Validating permissions and retrieving Azure what-if...")
    session.az("deployment", "group", "validate", *common)
    preview = session.az("deployment", "group", "what-if", *common, "--no-pretty-print")
    session.log(json.dumps(preview, indent=2))
    if apply:
        session.log("Applying the scoped permission template...")
        result = session.az("deployment", "group", "create", *common)
        require(result.get("properties", {}).get("provisioningState") == "Succeeded", "Permission deployment did not succeed.")
        return "Permissions applied successfully."
    return "Permission plan completed. No role assignments were changed."

def full_run(session, config, apply=False):
    validate_config(config)
    session.verify(config)
    target = config["client"] + "-" + config["workspace_label"]
    folder = DATA / "runs" / target
    folder.mkdir(parents=True, exist_ok=True)
    state = folder / "onboarding.state.json"
    lock = folder / "onboarding.lock"
    acquired = False
    try:
        if apply:
            try:
                with lock.open("x") as file:
                    file.write(str(os.getpid()))
                acquired = True
            except FileExistsError:
                raise Stop("An onboarding lock exists. Check whether an earlier run is active before retrying.")
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                Onboard(config, DesktopCLI(session, apply), state, BASE / "azuredeploy.json").run()
        finally:
            session.log(buffer.getvalue())
        return "Full onboarding completed." if apply else "Full onboarding plan completed; no cloud resources changed."
    finally:
        if acquired:
            lock.unlink()

