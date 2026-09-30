"""Explicit target selection and immutable deployment bundles."""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from rules import guid, load_yaml, validate, apply_overrides, template

ROOT = Path(__file__).resolve().parents[1]
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,49}$")
AZURE_FIELDS = {"tenant_id", "subscription_id", "resource_group", "workspace_name", "workspace_id"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rule_path(root, value):
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("Rule paths must use forward slashes")
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or not value.startswith("rules/sentinel/"):
        raise ValueError("Rule path must stay under rules/sentinel")
    path = root / rel
    if path.suffix not in (".yml", ".yaml") or not path.is_file():
        raise ValueError("Rule does not exist: " + value)
    if not path.resolve().is_relative_to((root / "rules/sentinel").resolve()):
        raise ValueError("Rule path escapes the rule library")
    if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)):
        raise ValueError("Symbolic links are not allowed")
    return path


def load_catalog(root=ROOT):
    result, destinations, customer_ids = {}, set(), set()
    for path in sorted((root / "clients").rglob("*")):
        if path.suffix not in (".yml", ".yaml"):
            continue
        if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)):
            raise ValueError("Target files must not be symbolic links")
        target = load_yaml(path)
        expected = {"version", "target", "client", "enabled", "allow_missing_mitre", "azure", "rules"}
        if not isinstance(target, dict) or set(target) != expected or target["version"] != 1:
            raise ValueError(str(path) + ": unexpected target schema")
        for field in ("target", "client"):
            if not isinstance(target[field], str) or not SLUG.fullmatch(target[field]):
                raise ValueError("Target/client must be lowercase slugs, 2..50 characters")
        if target["target"] in result:
            raise ValueError("Duplicate target: " + target["target"])
        if path.parent.name != target["client"] or not target["target"].startswith(target["client"] + "-"):
            raise ValueError("Client folder, client field and target prefix must agree")
        if not isinstance(target["enabled"], bool) or not isinstance(target["allow_missing_mitre"], bool):
            raise ValueError("Target enabled and allow_missing_mitre must be booleans")
        azure = target["azure"]
        if not isinstance(azure, dict) or set(azure) != AZURE_FIELDS:
            raise ValueError("Target Azure fields do not match the schema")
        if any(not isinstance(value, str) or not value.strip() for value in azure.values()):
            raise ValueError("Azure identifiers must be nonempty strings")
        if not isinstance(target["rules"], list):
            raise ValueError("Target rules must be a list")
        if target["enabled"]:
            for field in ("tenant_id", "subscription_id", "workspace_id"):
                if not guid(azure[field]):
                    raise ValueError("Enabled target requires a real " + field)
            for field in ("resource_group", "workspace_name"):
                if not re.fullmatch(r"[A-Za-z0-9_.()-]{1,90}", azure[field]) or "REPLACE" in azure[field]:
                    raise ValueError("Invalid " + field)
            dest = tuple(azure[k].lower() for k in ("subscription_id", "resource_group", "workspace_name"))
            customer = (azure["tenant_id"].lower(), azure["workspace_id"].lower())
            if dest in destinations or customer in customer_ids:
                raise ValueError("Workspace registered more than once")
            destinations.add(dest)
            customer_ids.add(customer)
            if not target["rules"]:
                raise ValueError("Enabled target must explicitly select at least one rule")
        result[target["target"]] = target
    return result


def selected_rules(root, target):
    selected, identities, paths = [], set(), set()
    for entry in target["rules"]:
        if not isinstance(entry, dict) or set(entry) - {"path", "overrides"} or "path" not in entry:
            raise ValueError("Rule selection must contain path and optional overrides")
        path = rule_path(root, entry["path"])
        canonical = path.resolve()
        if canonical in paths:
            raise ValueError("Rule selected twice: " + entry["path"])
        paths.add(canonical)
        effective = apply_overrides(load_yaml(path), entry.get("overrides", {}))
        warnings = validate(effective, target["allow_missing_mitre"])
        for warning in warnings:
            print("WARNING:", target["target"], entry["path"], warning)
        identity = effective["id"].lower()
        if identity in identities:
            raise ValueError("Duplicate effective rule ID in target")
        identities.add(identity)
        selected.append((entry["path"], effective))
    return selected


def check_all(root=ROOT):
    ids = {}
    count = 0
    library = root / "rules/sentinel"
    if not library.is_dir():
        raise ValueError("Missing rules/sentinel directory")
    for path in sorted(library.rglob("*")):
        if path.suffix not in (".yaml", ".yml"):
            continue
        rule_path(root, path.relative_to(root).as_posix())
        rule = load_yaml(path)
        for warning in validate(rule, allow_missing_mitre=True):
            print("WARNING:", path.relative_to(root), warning)
        identity = rule["id"].lower()
        if identity in ids:
            raise ValueError("Duplicate shared rule ID: " + str(path))
        ids[identity] = path
        count += 1
    catalog = load_catalog(root)
    for target in catalog.values():
        selected_rules(root, target)
    print("Checked", count, "shared rules and", len(catalog), "targets (offline).")
    return catalog


def choose(root, target_id):
    catalog = check_all(root)
    if target_id not in catalog or not catalog[target_id]["enabled"]:
        raise ValueError("Unknown or disabled target: " + target_id)
    return catalog[target_id]


def encode(doc):
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()


def build(root, target_id, out, rule_path=None):
    target = choose(root, target_id)
    rules = selected_rules(root, target)
    if rule_path:
        # Require an exact selected path; never deploy an arbitrary library file.
        matches = [(path, rule) for path, rule in rules if path == rule_path]
        if not matches:
            raise ValueError("Rule is not selected for target: " + rule_path)
        rules = matches
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output folder is not empty; refusing stale artifacts")
    out.mkdir(parents=True, exist_ok=True)
    arm = encode(template([r for _, r in rules]))
    manifest = {
        "version": 1, "target": target,
        "commit": os.environ.get("GITHUB_SHA", "local-test"),
        "template_sha256": digest(arm),
        "rules": [{"path": p, "id": r["id"].lower(), "status": r["status"]} for p, r in rules],
    }
    blob = encode(manifest)
    (out / "template.json").write_bytes(arm)
    (out / "bundle.json").write_bytes(blob)
    return target, digest(blob)


def verify_bundle(folder, expected_digest):
    blob = (folder / "bundle.json").read_bytes()
    if not expected_digest or digest(blob) != expected_digest:
        raise ValueError("Bundle manifest digest mismatch")
    manifest = json.loads(blob)
    arm = (folder / "template.json").read_bytes()
    if digest(arm) != manifest["template_sha256"]:
        raise ValueError("Template digest mismatch")
    template_doc = json.loads(arm)
    if len(template_doc["resources"]) != len(manifest["rules"]) or not manifest["rules"]:
        raise ValueError("Bundle has no rules or inconsistent rule counts")
    for item in template_doc["resources"]:
        if item["type"] != "Microsoft.OperationalInsights/workspaces/providers/alertRules":
            raise ValueError("Unexpected resource type in bundle")
    return manifest, template_doc


def output(key, value):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(key + "=" + value + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check", "build", "catalog"])
    ap.add_argument("--target")
    ap.add_argument("--rule-path", default="")
    ap.add_argument("--out", default="build/bundle")
    args = ap.parse_args()
    if args.command == "check":
        check_all()
    elif args.command == "catalog":
        catalog = check_all()
        active = [t for t in catalog if catalog[t]["enabled"]]
        if "GITHUB_OUTPUT" in os.environ:
            output("targets", json.dumps(active))
            output("has_targets", str(bool(active)).lower())
        print("Enabled targets:", ", ".join(active) or "none")
    else:
        target, checksum = build(ROOT, args.target or "", ROOT / args.out, args.rule_path)
        if "GITHUB_OUTPUT" in os.environ:
            output("target", json.dumps(target, separators=(",", ":")))
            output("digest", checksum)
        print("Built selected target:", target["target"])
        print("Rule selection:", args.rule_path or "all selected rules (query check)")
        if "GITHUB_STEP_SUMMARY" in os.environ:
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write("## Selected Sentinel target\n\n")
                stream.write("- Target: " + target["target"] + "\n")
                stream.write("- Workspace: " + target["azure"]["workspace_name"] + "\n")
                stream.write("- Commit: " + os.environ.get("GITHUB_SHA", "") + "\n")
                stream.write("- Bundle SHA-256: " + checksum + "\n")
                stream.write("- Rule selection: " + (args.rule_path or "all selected rules") + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
