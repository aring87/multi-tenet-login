"""Preserve slug strings when emitting YAML without a YAML dependency."""
import json


def yaml_slug(value):
    # Keep existing ordinary manifests byte-compatible with onboarding's retry
    # checks. Numeric-leading and YAML 1.1 reserved words must be quoted so 0413,
    # 413, on, etc. retain their exact string identity when loaded by PyYAML.
    if value[0].isdigit() or value in {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}:
        return json.dumps(value)
    return value


def matches_legacy_slug_manifest(text, expected, *, client, target):
    """Allow only old/partially repaired slug quoting in an otherwise exact manifest.

    Do not parse YAML: that would erase the identity of values such as 0413 or on.
    Only identifiers that the generator now quotes may change their spelling.
    All other lines, including duplicate fields and resource settings, must match.
    """
    variants = {expected.strip()}
    for field, value in (("client", client), ("target", target)):
        encoded = yaml_slug(value)
        if encoded == value:
            continue
        canonical = field + ": " + encoded
        for candidate in tuple(variants):
            for spelling in (value, "'" + value + "'"):
                variants.add("\n".join(
                    field + ": " + spelling if line == canonical else line
                    for line in candidate.split("\n")))
    return text.strip() in variants
