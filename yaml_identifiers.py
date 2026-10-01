"""Preserve slug strings when emitting YAML without a YAML dependency."""
import json


def yaml_slug(value):
    # Keep existing ordinary manifests byte-compatible with onboarding's retry
    # checks. Numeric-leading and YAML 1.1 reserved words must be quoted so 0413,
    # 413, on, etc. retain their exact string identity when loaded by PyYAML.
    if value[0].isdigit() or value in {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}:
        return json.dumps(value)
    return value
