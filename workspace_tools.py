"""Explicit subscription access and read-only Sentinel rule inventory."""
import fnmatch
import time
import uuid
from desktop_backend import require, guid
from audit_evidence import Collector, CONFIGURATIONS, ARM

CONTRIBUTOR = "b24988ac-6180-42a0-ab88-20f7382dd24c"

def permits(blocks, action):
    action = action.lower()
    return any(any(fnmatch.fnmatchcase(action, a.lower()) for a in b.get("actions", []))
               and not any(fnmatch.fnmatchcase(action, a.lower()) for a in b.get("notActions", []))
               for b in blocks)

def access_plan(session, target):
    account = session.verify(target)
    require(session.az("cloud", "show").get("name") == "AzureCloud", "This access action supports Azure public cloud only.")
    require(account.get("user", {}).get("type", "").lower() == "user", "Sign in with your authorized user account.")
    principal = guid(session.az("ad", "signed-in-user", "show")["id"], "signed-in user")
    scope = "/subscriptions/" + guid(target["subscription_id"], "subscription")
    collector = Collector(session, dict(target, resource_id=scope))
    result = collector.collect("permissions", "Subscription permissions", ARM + scope +
        "/providers/Microsoft.Authorization/permissions?api-version=2022-04-01", "Effective subscription actions")
    require(result["status"] in ("collected", "no_records"), result.get("error", "Could not check permissions."))
    blocks = result["records"]
    needs_role = not permits(blocks, "Microsoft.ManagedServices/register/action")
    if needs_role:
        require(permits(blocks, "Microsoft.Authorization/roleAssignments/write"),
                "Your current Azure session cannot assign roles at this subscription. Activate authorized access in CyberQP, then sign in again.")
    return dict(target=dict(target), principal=principal, account=account["user"]["name"],
                scope=scope, needs_role=needs_role, created=time.time())

def apply_contributor(session, plan):
    require(0 <= time.time()-plan["created"] < 900, "Access review expired. Check access again.")
    fresh = access_plan(session, plan["target"])
    require(fresh["principal"] == plan["principal"] and fresh["scope"] == plan["scope"], "Signed-in identity changed. Review access again.")
    if not fresh["needs_role"]:
        return "Provider registration is already permitted. No role assignment was needed."
    assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, plan["target"]["tenant_id"] + plan["scope"] + plan["principal"] + CONTRIBUTOR))
    session.az("role", "assignment", "create", "--assignee-object-id", plan["principal"],
               "--assignee-principal-type", "User", "--role", CONTRIBUTOR,
               "--scope", plan["scope"], "--name", assignment)
    return ("Azure accepted the Contributor assignment for " + plan["account"] + ". "
            "It remains assigned until removed. Allow RBAC propagation, then run Preview setup again. "
            "Assignment ID: " + assignment)

def list_rules(session, workspace):
    collector = Collector(session, workspace)
    collector.verify_workspace()
    title, resource, version = CONFIGURATIONS["rules"]
    return collector.collect("rules", title, collector.base + "/providers/Microsoft.SecurityInsights/" +
                             resource + "?api-version=" + version, "Configured analytics rules, including disabled rules")

def rule_values(row):
    props = row.get("properties", {})
    enabled = props.get("enabled")
    state = "Enabled" if enabled is True else "Disabled" if enabled is False else "Not specified"
    return (props.get("displayName") or row.get("name", "Unnamed"), state,
            props.get("severity", "Not specified"), row.get("kind", "Unknown"))
