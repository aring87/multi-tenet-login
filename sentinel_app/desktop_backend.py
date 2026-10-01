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
from .auth_recovery import AuthenticationRequired, SignInCancelled, signin_was_cancelled, azure_error, recovery_scope
from .signin_process import run_signin
from .lighthouse_onboarding import CLI, Onboard, Stop, guid, require, validate_config

from .paths import ROOT as BASE, DATA

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
                        AZURE_CORE_ONLY_SHOW_ERRORS="false",
                        AZURE_CORE_ENABLE_BROKER_ON_WINDOWS="false",
                        MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*",
                        PYTHONIOENCODING="utf-8")
        self.az_exe = shutil.which("az")
        self.gh_exe = shutil.which("gh")
        self.account = None
        self.login_tenant = ""
        self.login_diagnostics = ""
        self.signed_in = False
        self._signin_cancel = None

    def execute(self, executable, args, data=None, timeout=900):
        require(executable, "Required tool is missing. Install Azure CLI / GitHub CLI and restart this app.")
        auth_command = executable == self.az_exe and (
            tuple(args[:1]) in (("login",), ("logout",)) or tuple(args[:2]) == ("cloud", "show"))
        # The MSI wrapper invokes this exact interpreter. Bypass cmd.exe so REST
        # query separators, percent escapes and pagination links remain literal.
        if executable == self.az_exe and Path(executable).name.lower() == "az.cmd":
            interpreter = Path(executable).parent.parent / "python.exe"
            if interpreter.is_file():
                executable = str(interpreter)
                args = ["-IBm", "azure.cli", *args]
        if executable.lower().endswith((".cmd", ".bat")):
            require(all(not re.search(r'[&|<>^%!\r\n"]', str(x)) for x in args),
                    "An argument contains unsupported Windows shell characters.")
        if self._signin_cancel is not None:
            require(auth_command, "Only authentication commands can use sign-in cancellation.")
            # Authentication owns a direct process. A shell wrapper could leave
            # its Python child waiting after cancellation, so require the normal
            # Azure CLI interpreter resolved above for Windows cmd installations.
            require(not executable.lower().endswith((".cmd", ".bat")),
                    "Cannot safely cancel this Azure CLI wrapper. Repair the Azure CLI installation and restart the app.")
            return run_signin([executable, *map(str, args)], self.env, self._signin_cancel, timeout)
        try:
            result = subprocess.run([executable, *map(str, args)], input=data or "", text=True,
                encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self.env, shell=False, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise Stop("The operation timed out. A cloud change may still be running; inspect its status before retrying.")
        return result

    def authentication_tenant(self):
        return (self.account or {}).get("tenantId") or self.login_tenant

    def az(self, *args):
        signing_in = bool(args and args[0] == "login")
        # Azure CLI emits its claims-challenge recovery command as a warning.
        flags = ["--output", "json"]
        result = self.execute(self.az_exe, [*args, *flags])
        details = result.stderr.strip()
        if signing_in:
            self.login_diagnostics = details
            self.signed_in = result.returncode == 0
        if result.returncode:
            if signing_in and signin_was_cancelled(details):
                raise SignInCancelled()
            raise azure_error(details or "Azure operation failed.", self.authentication_tenant())
        # Successful multi-tenant login can still report a failed tenant on stderr.
        if signing_in and details:
            self.log("Azure sign-in details: " + str(azure_error(details, self.login_tenant)))
        return json.loads(result.stdout) if result.stdout.strip() else None

    def login(self, tenant_hint="", resource=None, claims=None, allow_empty=False):
        tenant_hint = validate_tenant_hint(tenant_hint) if tenant_hint.strip() else ""
        self.login_tenant = tenant_hint
        self.login_diagnostics = ""
        self.signed_in = False
        self.log("Complete Microsoft sign-in with the client's authorized account and MFA.")
        args = ["login", "--allow-no-subscriptions"]
        if tenant_hint:
            args.extend(["--tenant", tenant_hint])
        if resource is not None:
            require(tenant_hint, "Enter the client tenant ID or verified domain before recovering authentication.")
            args.extend(["--scope", recovery_scope(self.az("cloud", "show"), resource)])
            if claims:
                args.extend(["--claims-challenge", claims])
        accounts = self.az(*args) or []
        subscriptions = [x for x in accounts if x.get("id") != x.get("tenantId")]
        if re.fullmatch(r"[0-9a-fA-F-]{36}", tenant_hint):
            require(all(x["tenantId"].lower() == tenant_hint.lower() for x in (accounts if allow_empty else subscriptions)),
                    "Sign-in returned a different tenant.")
        if not subscriptions:
            if self.login_diagnostics:
                if signin_was_cancelled(self.login_diagnostics):
                    raise SignInCancelled()
                problem = azure_error(self.login_diagnostics, tenant_hint)
                if isinstance(problem, AuthenticationRequired):
                    raise problem
            if allow_empty:
                require(accounts, "Azure did not return a tenant account. Sign in to the client tenant again.")
                self.log("Signed in without visible subscriptions. Review Azure access management to continue.")
                return []
            raise Stop("Sign-in returned no accessible Azure subscriptions. This can mean the wrong "
                       "account/tenant, incomplete tenant authentication, or missing Azure access. "
                       "Enter the client tenant ID and sign in again. If sign-in succeeded, use "
                       "Check missing subscription with its ID from Azure portal.\n" + self.login_diagnostics)
        return subscriptions

    def refresh_subscriptions(self):
        rows=self.az("account","list","--all","--refresh") or []
        return [r for r in rows if r.get("id") != r.get("tenantId")]

    def check_subscription(self, subscription):
        subscription=guid(subscription.strip(),"subscription ID")
        account=self.az("account","show")
        cloud=self.az("cloud","show")
        endpoint=cloud["endpoints"]["resourceManager"].rstrip("/")
        report=["Signed in: "+account.get("user",{}).get("name","unknown"),
                "Authentication tenant: "+account["tenantId"],"Cloud: "+cloud["name"],
                "Requested subscription ID: "+subscription]
        try:
            sub=self.az("rest","--method","get","--url",endpoint+"/subscriptions/"+subscription+"?api-version=2022-12-01")
        except AuthenticationRequired:
            raise
        except Stop as error:
            return "\n".join(report)+"\nSubscription lookup failed:\n"+str(error)
        require(sub.get("subscriptionId","").lower()==subscription,"Subscription response mismatch.")
        report.extend(["Subscription name: "+sub.get("displayName","unknown"),
                       "Subscription state: "+sub.get("state","unknown"),
                       "Tenant ID: "+sub.get("tenantId","Not returned by Azure")])
        try:
            result=self.az("rest","--method","get","--url",endpoint+"/subscriptions/"+subscription+
                           "/providers/Microsoft.OperationalInsights/workspaces?api-version=2025-07-01")
            rows=result.get("value",[])
            for item in rows:
                record=workspace_record(dict(id=item["id"],customerId=item.get("properties",{}).get("customerId")),
                                        subscription,sub.get("tenantId"))
                report.extend(["", "Resource group: "+record["resource_group"],
                               "Workspace name: "+record["workspace_name"],"Workspace ID: "+record["workspace_id"]])
            if not rows: report.append("No accessible workspaces returned by Azure in this subscription.")
            if result.get("nextLink"): report.append("Additional workspace pages exist; this diagnostic shows the first page.")
        except AuthenticationRequired:
            raise
        except Stop as error:
            report.append("Workspace lookup failed:\n"+str(error))
        return "\n".join(report)

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
        self.signed_in = False

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
            details = result.stderr.strip() or "Operation failed."
            if executable == self.azure:
                raise azure_error(details, self.session.authentication_tenant())
            raise Stop(details)
        return json.loads(result.stdout) if result.stdout.strip() else None

def config_from_workspace(workspace, client, label, fields, extras=None):
    """Build a delegation onboarding config from a discovered workspace.

    Under the Lighthouse model the per-client fields are gone: no app owners, no
    OIDC subject format, no per-client reviewers or environments, no human groups.
    What remains constant for every client comes from settings - the managing
    tenant, the three pipeline group Object IDs, and the two shared environment
    names."""
    require(workspace, "Select a discovered workspace first.")
    constants = ("managing_tenant_id", "deploy_group_object_id", "read_group_object_id",
                 "engineer_group_object_id", "preview_environment", "production_environment")
    for key in constants:
        require(fields.get(key), f"Missing {key}. Set the managing tenant, the three pipeline "
                                 "group Object IDs and the two shared environment names in settings.")
    config = dict(tenant_id=workspace["tenant_id"], subscription_id=workspace["subscription_id"],
                  resource_group=workspace["resource_group"], workspace_name=workspace["workspace_name"],
                  client=client, workspace_label=label,
                  github_owner=fields["github_owner"], github_repo=fields["github_repo"],
                  managing_tenant_id=fields["managing_tenant_id"],
                  deploy_group_object_id=fields["deploy_group_object_id"],
                  read_group_object_id=fields["read_group_object_id"],
                  engineer_group_object_id=fields["engineer_group_object_id"],
                  preview_environment=fields["preview_environment"],
                  production_environment=fields["production_environment"],
                  initial_rule_path=fields["rule_path"],
                  allow_missing_mitre=fields["allow_missing_mitre"])
    for key in ("msp_offer_name", "delegation_location"):
        if fields.get(key):
            config[key] = fields[key]
    # Validation derives target; do not include that internal field in exported configuration.
    validate_config(config)
    return config

def permission_run(session, workspace, target, principals, apply=False):
    """Retired. Kept so the UI reports why rather than failing obscurely."""
    raise Stop(
        "Permissions-only mode is retired. Client access is now granted by an Azure Lighthouse "
        "delegation that assigns built-in roles to managing-tenant groups, not by per-client "
        "custom roles on per-client service principals. Use full onboarding, which deploys the "
        "delegation and opens the target pull request.")

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
                Onboard(config, DesktopCLI(session, apply), state, BASE / "templates" / "lighthouse-onboard.json").run()
        finally:
            session.log(buffer.getvalue())
        return "Full onboarding completed." if apply else "Full onboarding plan completed; no cloud resources changed."
    finally:
        if acquired:
            lock.unlink()
