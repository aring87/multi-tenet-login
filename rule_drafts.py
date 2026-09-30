"""Local rule authoring. No Azure calls, Git operations or repository code execution."""
import copy
import json
import os
from pathlib import Path
import tempfile
import uuid

import yaml
from repository_catalog import MAX_FILE, parse_yaml
import rule_schema

TEXT_FIELDS = ("name", "owner", "status", "kind", "severity", "queryFrequency",
               "queryPeriod", "triggerOperator", "triggerThreshold")
BOOL_FIELDS = ("enabled", "createIncident")
LIST_FIELDS = ("tactics", "techniques", "subTechniques")
EDITED_FIELDS = set(TEXT_FIELDS + BOOL_FIELDS + LIST_FIELDS + ("id", "description", "query"))
SCHEDULE_FIELDS = {"queryFrequency", "queryPeriod", "triggerOperator", "triggerThreshold"}


def new_form():
    return {"id": str(uuid.uuid4()), "name": "", "owner": "", "status": "baseline",
            "kind": "Scheduled", "severity": "Medium", "description": "", "query": "",
            "queryFrequency": "PT1H", "queryPeriod": "PT1H",
            "triggerOperator": "GreaterThan", "triggerThreshold": "0",
            "enabled": False, "createIncident": False, "tactics": "", "techniques": "",
            "subTechniques": "", "advanced": "{}\n", "allow_missing_mitre": False}


def from_rule(rule):
    """Keep all unedited properties; refuse values the form cannot faithfully represent."""
    if not isinstance(rule, dict):
        raise ValueError("Rule must be a mapping.")
    form = new_form()
    # Imported rules must retain their identity, including an invalid/missing one.
    form["id"] = ""
    for key in EDITED_FIELDS:
        if key not in rule:
            continue
        value = rule[key]
        if key in BOOL_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(key + " must be a boolean before it can be edited.")
        elif key in LIST_FIELDS:
            if not isinstance(value, list) or any(not isinstance(x, str) or "," in x or "\n" in x for x in value):
                raise ValueError(key + " must contain plain string entries.")
            value = ", ".join(value)
        elif key == "triggerThreshold":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("triggerThreshold must be an integer.")
            value = str(value)
        elif not isinstance(value, str):
            raise ValueError(key + " must be text before it can be edited.")
        form[key] = value
    form["advanced"] = yaml.safe_dump({k: copy.deepcopy(v) for k, v in rule.items()
                                       if k not in EDITED_FIELDS}, sort_keys=False)
    return form


def to_rule(form):
    advanced = parse_yaml(form["advanced"].encode("utf-8") if form["advanced"].strip() else b"{}")
    if set(advanced) & EDITED_FIELDS:
        raise ValueError("Use the guided fields for: " + ", ".join(sorted(set(advanced) & EDITED_FIELDS)))
    result = copy.deepcopy(advanced)
    for key in TEXT_FIELDS + BOOL_FIELDS + ("id", "description", "query"):
        if key in SCHEDULE_FIELDS and form["kind"] == "NRT":
            continue
        result[key] = form[key]
    if form["kind"] != "NRT":
        try:
            result["triggerThreshold"] = int(form["triggerThreshold"])
        except ValueError as error:
            raise ValueError("Alert threshold must be a whole number, such as 0.") from error
    for key in LIST_FIELDS:
        result[key] = [x.strip() for x in form[key].replace("\n", ",").split(",") if x.strip()]
    return result


def validated_yaml(form):
    rule = to_rule(form)
    warnings = rule_schema.validate(rule, allow_missing_mitre=form["allow_missing_mitre"])
    # Exercise the same ARM transformation, without deploying or saving an ARM template.
    rule_schema.to_arm(rule)
    return yaml.safe_dump(rule, sort_keys=False, allow_unicode=True), warnings


def read_bounded(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError("File exceeds the 2 MB authoring limit.")
    return raw


def read_rule(path):
    return from_rule(parse_yaml(read_bounded(path)))


def atomic_write(path, text):
    """Replace only after successful encoding/write; leave existing contents on failure."""
    path = Path(path)
    raw = text.encode("utf-8")
    if len(raw) > MAX_FILE:
        raise ValueError("File exceeds the 2 MB authoring limit.")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def save_draft(path, form):
    atomic_write(path, json.dumps({"format": "sentinel-rule-draft", "version": 1,
                                 "form": form}, indent=2) + "\n")


def read_draft(path):
    doc = json.loads(read_bounded(path))
    if not isinstance(doc, dict) or doc.get("format") != "sentinel-rule-draft" or doc.get("version") != 1:
        raise ValueError("Not a supported rule draft. Use Open YAML for repository rules.")
    form, expected = doc.get("form"), new_form()
    if not isinstance(form, dict) or set(form) != set(expected):
        raise ValueError("Draft fields do not match this version of the editor.")
    if any(type(form[k]) is not type(v) for k, v in expected.items()):
        raise ValueError("Draft contains invalid field types.")
    return form


def export_rule(path, form):
    text, warnings = validated_yaml(form)
    atomic_write(path, text)
    return warnings
