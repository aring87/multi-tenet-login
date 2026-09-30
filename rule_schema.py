"""Shared YAML loader, metadata checks, and deterministic ARM compiler.

Adapted from the user's original rules.py. GitHub runs this; no local CLI needed.
"""
import copy
import json
import re
import uuid
from pathlib import Path
import yaml

API_VERSION = "2023-12-01-preview"
SEVERITIES = {"Informational", "Low", "Medium", "High"}
TACTICS = {
    "Reconnaissance", "ResourceDevelopment", "InitialAccess", "Execution",
    "Persistence", "PrivilegeEscalation", "DefenseEvasion", "CredentialAccess",
    "Discovery", "LateralMovement", "Collection", "CommandAndControl",
    "Exfiltration", "Impact", "ImpairProcessControl", "InhibitResponseFunction",
}
FIELDS = {
    "id", "name", "description", "owner", "status", "enabled", "kind", "severity",
    "query", "tactics", "techniques", "subTechniques", "queryFrequency", "queryPeriod",
    "triggerOperator", "triggerThreshold", "createIncident", "groupingConfiguration",
    "suppressionDuration", "suppressionEnabled", "eventGroupingSettings",
    "entityMappings", "customDetails", "alertDetailsOverride",
    "alertRuleTemplateName", "templateVersion", "sentinelEntitiesMappings",
}
# Optional ARM properties that are passed through unchanged when they hold a value.
PASSTHROUGH = ("entityMappings", "customDetails", "alertDetailsOverride",
               "alertRuleTemplateName", "templateVersion", "sentinelEntitiesMappings")
OVERRIDES = {"id", "enabled", "severity", "queryFrequency", "queryPeriod",
             "triggerThreshold", "createIncident"}
DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML keys must be unique strings: " + str(key))
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def load_yaml(path):
    return yaml.load(Path(path).read_text(encoding="utf-8-sig"), Loader=UniqueLoader)


def guid(value):
    try:
        parsed = uuid.UUID(value)
        return isinstance(value, str) and str(parsed) == value.lower() and parsed.int != 0
    except (ValueError, TypeError, AttributeError):
        return False


def seconds(value):
    match = DURATION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Expected an ISO 8601 duration")
    values = [int(x or 0) for x in match.groups()]
    total = sum(a * b for a, b in zip(values, (86400, 3600, 60, 1)))
    if total <= 0:
        raise ValueError("Duration must be positive")
    return total


def validate(r, allow_missing_mitre=False):
    if not isinstance(r, dict):
        raise ValueError("Rule must be a YAML mapping")
    unknown = set(r) - FIELDS
    if unknown:
        raise ValueError("Unknown rule fields: " + ", ".join(sorted(unknown)))
    for field in ("name", "description", "owner", "query"):
        if not isinstance(r.get(field), str) or not r[field].strip():
            raise ValueError(field + " must be nonempty text")
    if not guid(r.get("id")):
        raise ValueError("id must be a nonzero GUID")
    if len(r["description"].strip()) < 20:
        raise ValueError("description must be at least 20 characters")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._@-]*", r["owner"]):
        raise ValueError("owner must be a team/account identifier")
    if not isinstance(r.get("severity"), str) or r["severity"] not in SEVERITIES:
        raise ValueError("Invalid severity")
    if r.get("status") not in ("baseline", "production", "retired"):
        raise ValueError("Invalid status")
    for field in ("enabled", "createIncident", "suppressionEnabled"):
        if field == "enabled" or field in r:
            if not isinstance(r.get(field), bool):
                raise ValueError(field + " must be true or false, not quoted text")
    if r["status"] == "retired" and r["enabled"]:
        raise ValueError("Retired rules must be disabled")
    kind = r.get("kind", "Scheduled")
    if kind not in ("Scheduled", "NRT"):
        raise ValueError("Only Scheduled and NRT rules are supported")
    if kind == "Scheduled":
        frequency, period = seconds(r.get("queryFrequency")), seconds(r.get("queryPeriod"))
        if not 300 <= frequency <= 14 * 86400 or not frequency <= period <= 14 * 86400:
            raise ValueError("Scheduled frequency/period must be 5m..14d, period >= frequency")
        if r.get("triggerOperator", "GreaterThan") not in ("GreaterThan", "LessThan", "Equal", "NotEqual"):
            raise ValueError("Invalid triggerOperator")
        threshold = r.get("triggerThreshold", 0)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
            raise ValueError("triggerThreshold must be a nonnegative integer")
    elif any(key in r for key in ("queryFrequency", "queryPeriod", "triggerOperator", "triggerThreshold")):
        raise ValueError("NRT rules must omit schedule and threshold properties")
    for field in ("tactics", "techniques", "subTechniques"):
        entries = r.get(field, [])
        if not isinstance(entries, list) or any(not isinstance(x, str) for x in entries):
            raise ValueError(field + " must be a list of strings")
    if any(x not in TACTICS for x in r.get("tactics", [])):
        raise ValueError("Unknown MITRE tactic")
    # Enterprise ATT&CK IDs are T1xxx-T9xxx; ATT&CK for ICS IDs are T0xxx (e.g. T0849).
    for tech in r.get("techniques", []):
        if not re.fullmatch(r"T\d{4}", tech) or tech == "T0000":
            raise ValueError("techniques accepts parent IDs such as T1562 or T0849; put T1562.004 in subTechniques")
    for tech in r.get("subTechniques", []):
        if not re.fullmatch(r"T\d{4}\.\d{3}", tech):
            raise ValueError("subTechniques requires IDs such as T1562.004")
        if tech.split(".")[0] not in r.get("techniques", []):
            raise ValueError("Include the parent technique for each subTechnique")
    warnings = []
    if not r.get("tactics") or not r.get("techniques"):
        if not allow_missing_mitre:
            raise ValueError("Missing MITRE metadata; backfill or enable target migration exception")
        warnings.append("Missing MITRE metadata: migration exception")
    for field, kind_type in (("groupingConfiguration", dict), ("eventGroupingSettings", dict),
                             ("entityMappings", list), ("customDetails", dict),
                             ("alertDetailsOverride", dict), ("templateVersion", str),
                             ("sentinelEntitiesMappings", list)):
        if field in r and r[field] is not None and not isinstance(r[field], kind_type):
            raise ValueError(field + " has an invalid type")
    template_name = r.get("alertRuleTemplateName")
    if template_name is not None:
        if not guid(template_name):
            raise ValueError("alertRuleTemplateName must be a lowercase template GUID")
        if template_name == r["id"].lower():
            raise ValueError("alertRuleTemplateName must not equal the rule id")
    if "suppressionDuration" in r:
        seconds(r["suppressionDuration"])
    if r["query"].rstrip().endswith("|"):
        raise ValueError("Query ends in a dangling pipe")
    return warnings


def apply_overrides(rule, overrides):
    if not isinstance(overrides, dict) or set(overrides) - OVERRIDES:
        raise ValueError("Only documented per-target override fields are allowed")
    result = copy.deepcopy(rule)
    result.update(overrides)
    return result


def to_arm(r):
    props = {
        "displayName": r["name"], "description": r["description"].strip(),
        "severity": r["severity"], "enabled": r["enabled"], "query": r["query"].rstrip(),
        "suppressionDuration": r.get("suppressionDuration", "PT1H"),
        "suppressionEnabled": r.get("suppressionEnabled", False),
        "tactics": r.get("tactics", []), "techniques": r.get("techniques", []),
        "subTechniques": r.get("subTechniques", []),
        "incidentConfiguration": {
            "createIncident": r.get("createIncident", False),
            "groupingConfiguration": r.get("groupingConfiguration", {
                "enabled": False, "reopenClosedIncident": False,
                "lookbackDuration": "PT5M", "matchingMethod": "AllEntities",
                "groupByEntities": [], "groupByAlertDetails": [], "groupByCustomDetails": [],
            }),
        },
        "eventGroupingSettings": r.get("eventGroupingSettings", {"aggregationKind": "SingleAlert"}),
    }
    for key in PASSTHROUGH:
        if r.get(key):
            props[key] = r[key]
    if r.get("kind", "Scheduled") == "Scheduled":
        props.update({key: r[key] for key in ("queryFrequency", "queryPeriod")})
        props["triggerOperator"] = r.get("triggerOperator", "GreaterThan")
        props["triggerThreshold"] = r.get("triggerThreshold", 0)
    return {
        "type": "Microsoft.OperationalInsights/workspaces/providers/alertRules",
        "apiVersion": API_VERSION, "kind": r.get("kind", "Scheduled"),
        "name": "[concat(parameters('workspace'), '/Microsoft.SecurityInsights/" + r["id"].lower() + "')]",
        "properties": props,
    }


def template(rules):
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {"workspace": {"type": "string"}},
        "resources": [to_arm(r) for r in rules],
    }
