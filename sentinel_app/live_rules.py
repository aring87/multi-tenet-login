"""A rule live in a Sentinel workspace as a new rule-library rule, proven to deploy back exactly as it is.

A reviewed port of the library's own importer (detection-as-code scripts/convert_sentinel_export.py:
candidate, normalize_properties, differences, check_round_trip), so the app never runs repository
code. The proof compiles the converted rule with rule_schema.to_arm (the pipeline's compiler), and
the library file the Rule builder would write from it read back, and compares both with the live
properties, strictly. Like the importer, it always drops read-only and null properties and trailing
whitespace on the query (the pipeline trims it; the query doesn't change); beyond that the only
allowances are listed in ALLOWANCES, and each one that applies to a rule is reported. Read the rule at rule_schema.API_VERSION, the version
the pipeline deploys at: newer API versions leave out properties such as subTechniques, and a rule
converted without them would deploy with them cleared. Nothing here reads Azure or GitHub."""
import copy
import json
import re

import yaml

from . import rule_drafts as drafts
from . import rule_schema

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
KINDS = ("Scheduled", "NRT")  # The kinds the library holds (rule_schema.validate).
SCHEDULE = ("queryFrequency", "queryPeriod", "triggerOperator", "triggerThreshold")
REQUIRED = ("displayName", "enabled", "severity", "query")
# Live properties copied under the same name (the importer's DIRECT, which includes these).
DIRECT = ("description", "enabled", "severity", "query", *SCHEDULE, "tactics", "techniques", "subTechniques",
          "entityMappings", "customDetails", "suppressionEnabled", "suppressionDuration", "eventGroupingSettings",
          "alertDetailsOverride", "alertRuleTemplateName", "templateVersion", "sentinelEntitiesMappings")
READ_ONLY = {"lastModifiedUtc"}
# Built-in rule kinds that run Microsoft's own detections, with no query to keep.
PLATFORM = {"Fusion": "Fusion", "MicrosoftSecurityIncidentCreation": "Microsoft incident-creation",
            "MLBehaviorAnalytics": "ML behavior analytics", "ThreatIntelligence": "Threat intelligence matching"}
OWNER = "security-engineering"  # As every rule the library's importer brought in.
SUBTECHNIQUE = re.compile(r"T\d{4}\.\d{3}")
# Where the proof is looser than a plain comparison. Each is applied to both sides and reported.
ALLOWANCES = {
    "empty": "An empty {key} in the workspace is left out: the pipeline deploys none, which Sentinel treats the same.",
    "description": "Spaces or line breaks around the description are dropped, as the pipeline always does.",
    "subtechniques": "Sub-technique IDs listed under techniques ({ids}) move to subTechniques, as the library requires.",
}


class NotConvertible(ValueError):
    """The rule can't be a library rule, or wouldn't deploy back exactly as it is."""


def rule_id(record):
    """The rule's GUID, from the REST record's name (its last path segment), in lower case."""
    name = str((record or {}).get("name") or "").rsplit("/", 1)[-1]
    if not GUID.fullmatch(name):
        raise NotConvertible("Its rule ID isn't a GUID, so the library can't keep it.")
    return name.lower()


def why_not(record):
    """Why Drift can't offer to add this live rule to the library, before reading anything; None if it can."""
    if not isinstance(record, dict): return "There is no live rule to add."
    kind = record.get("kind")
    if kind not in KINDS:
        return (f"{PLATFORM[kind]} rules have no query of their own to keep in the library, so they stay in the portal."
                if kind in PLATFORM else f"The library holds Scheduled and NRT rules only; this one is {kind or 'of no known kind'}.")
    try:
        rule_id(record)
    except NotConvertible as error:
        return str(error)
    return None


def empty(value):
    return value == [] or value == {} or value == ""


def split_techniques(techniques, sub_techniques):
    """(techniques, subTechniques, moved): parent IDs only under techniques, as the importer does."""
    moved = sorted({t for t in techniques if isinstance(t, str) and SUBTECHNIQUE.fullmatch(t)})
    if not moved: return techniques, sub_techniques, []
    # Only the well-formed sub-technique IDs move; anything else (T1059.1, say) stays as it is, so the
    # library's checks flag it rather than it quietly becoming its parent.
    kept = [t for t in techniques if t not in moved]
    parents = sorted({t.split(".")[0] for t in moved} | {t for t in kept if isinstance(t, str)}) + \
        [t for t in kept if not isinstance(t, str)]
    return parents, sorted(set(sub_techniques or []) | set(moved)), moved


def to_library(record, owner=OWNER):
    """The library rule for a live REST ``record``: (rule, applied allowance keys). Raises NotConvertible."""
    reason = why_not(record)
    if reason: raise NotConvertible(reason)
    kind, props = record["kind"], record.get("properties")
    if not isinstance(props, dict): raise NotConvertible("The rule's properties couldn't be read.")
    required = REQUIRED + (SCHEDULE if kind == "Scheduled" else ())
    missing = [key for key in required if key not in props]
    if missing: raise NotConvertible("Sentinel didn't return " + ", ".join(missing) + " for it.")
    if not isinstance(props["enabled"], bool): raise NotConvertible("Its enabled state isn't true or false.")
    for key in ("query", "displayName"):
        if not isinstance(props[key], str) or not props[key].strip(): raise NotConvertible(f"Its {key} is empty.")
    if kind == "NRT":
        present = [key for key in SCHEDULE if props.get(key) is not None]
        if present: raise NotConvertible("It is an NRT rule with schedule settings (" + ", ".join(present) + ").")
    rule = dict(id=rule_id(record), name=props["displayName"], description=props.get("description", ""),
                owner=owner, status="baseline", kind=kind)
    applied = []
    for key in DIRECT:
        if key not in props or props[key] is None: continue
        if kind == "NRT" and key in SCHEDULE: continue
        if key in rule_schema.PASSTHROUGH and empty(props[key]):
            applied.append(("empty", key)); continue
        rule[key] = copy.deepcopy(props[key])
    rule.setdefault("tactics", []); rule.setdefault("techniques", [])
    if isinstance(rule["techniques"], list):
        techniques, subs, moved = split_techniques(rule["techniques"], rule.get("subTechniques"))
        if moved:
            rule["techniques"], rule["subTechniques"] = techniques, subs
            applied.append(("subtechniques", ", ".join(moved)))
    incident = props.get("incidentConfiguration")
    if isinstance(incident, dict):
        for key in ("createIncident", "groupingConfiguration"):
            if key in incident: rule[key] = copy.deepcopy(incident[key])
    if isinstance(rule.get("description"), str) and rule["description"] != rule["description"].strip():
        applied.append(("description", ""))
    # Fresh objects throughout: a list shared between two keys would be written as a YAML alias,
    # which the library's reader refuses.
    return json.loads(json.dumps(rule)), applied


def normalize(props, applied):
    """Both sides of the proof, made comparable the same way (the importer's normalize_properties,
    plus the allowances this rule needed)."""
    result = {key: copy.deepcopy(value) for key, value in props.items() if value is not None and key not in READ_ONLY}
    if isinstance(result.get("query"), str): result["query"] = result["query"].rstrip()
    kinds = {kind for kind, _ in applied}
    if "description" in kinds and isinstance(result.get("description"), str):
        result["description"] = result["description"].strip()
    if "subtechniques" in kinds and isinstance(result.get("techniques"), list):
        result["techniques"], subs, _ = split_techniques(result["techniques"], result.get("subTechniques"))
        result["subTechniques"] = subs
    for kind, key in applied:
        if kind == "empty" and key in result and empty(result[key]): del result[key]
    for key in ("tactics", "techniques", "subTechniques"):
        if isinstance(result.get(key), list) and all(isinstance(x, str) for x in result[key]):
            result[key] = sorted(result[key])
    return result


def differences(live, deployed, path="properties"):
    """Every way ``deployed`` differs from ``live``, strictly: types, keys and values (the importer's differences)."""
    if type(live) is not type(deployed): return [f"{path}: value/type differs"]
    if isinstance(live, dict):
        found = []
        for key in sorted(set(live) | set(deployed)):
            if key not in deployed: found.append(f"{path}.{key}: omitted by pipeline")
            elif key not in live: found.append(f"{path}.{key}: added by pipeline")
            else: found += differences(live[key], deployed[key], f"{path}.{key}")
        return found
    if isinstance(live, list):
        if len(live) != len(deployed): return [f"{path}: value differs"]
        found = []
        for index, (a, b) in enumerate(zip(live, deployed)): found += differences(a, b, f"{path}[{index}]")
        return found
    return [] if live == deployed else [f"{path}: value differs"]


def prove(record, rule, applied):
    """What would change if ``rule`` were deployed in place of the live ``record``: [] when nothing would.
    Checks both the rule and what the Rule builder would submit after opening it unedited."""
    try:
        arm = rule_schema.to_arm(copy.deepcopy(rule))
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        return ["The pipeline couldn't compile it: " + str(error)]
    problems = []
    if arm["kind"] != record["kind"]: problems.append("kind: value differs")
    if not arm["name"].endswith("/" + rule["id"] + "')]"): problems.append("rule ID: value differs")
    problems += differences(normalize(record["properties"], applied), normalize(arm["properties"], applied))
    try:
        rebuilt = drafts.to_rule(drafts.from_rule(copy.deepcopy(rule)))
        submitted = rule_schema.to_arm(copy.deepcopy(rebuilt))
        # The file the builder writes, read back as the library's pipeline reads it.
        written = rule_schema.to_arm(yaml.load(drafts.dump(rebuilt), Loader=rule_schema.UniqueLoader))
    except (KeyError, TypeError, AttributeError, ValueError, yaml.YAMLError) as error:
        return problems + ["The Rule builder can't open it: " + str(error)]
    if submitted != arm:
        problems.append("The Rule builder would change it on the way to the library: "
                        + "; ".join(differences(arm["properties"], submitted["properties"])[:5]))
    elif written != arm:
        problems.append("Saving it as a library file would change it: "
                        + "; ".join(differences(arm["properties"], written["properties"])[:5])
                        + " (a character YAML can't keep, such as U+0085)")
    return problems


def convert(record, owner=OWNER):
    """The library rule for a live rule read at rule_schema.API_VERSION, proven exact, and what to say about
    it: dict(rule, notes, fixes). ``notes`` name the allowances used; ``fixes`` are what the library's checks
    ask for before it can be submitted (the Rule builder shows them too). Raises NotConvertible when it
    can't be converted, or would deploy differently from the live rule."""
    rule, applied = to_library(record, owner)
    problems = prove(record, rule, applied)
    if problems:
        shown = problems[:8] + ([f"and {len(problems) - 8} more"] if len(problems) > 8 else [])
        raise NotConvertible("It wouldn't deploy back exactly as it is in the workspace:\n" + "\n".join("• " + p for p in shown))
    notes = [ALLOWANCES[kind].format(key=key, ids=key) for kind, key in applied]
    fixes = []
    try:
        for warning in rule_schema.validate(copy.deepcopy(rule), allow_missing_mitre=True):
            if warning.startswith("Missing MITRE"):
                fixes.append("It has no MITRE tactic or technique: add them, or tick Allow missing MITRE details.")
            else: fixes.append(warning)
    except ValueError as error:
        fixes.append(str(error)[:1].upper() + str(error)[1:] + ".")
    return dict(rule=rule, notes=notes, fixes=fixes)
