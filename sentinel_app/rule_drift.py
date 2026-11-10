"""Rule drift: what the rule library assigns to a client's workspace target, against the analytics
rules read from that workspace. Pure functions over data the app already holds (a loaded library
snapshot and a loaded rule collection); nothing here reads Azure or GitHub."""
from .repository_catalog import state
from .workspace_tools import rule_values

MISSING, DIFFERENT, LIVE_ONLY = "missing", "different", "live_only"
KIND_ORDER = (MISSING, DIFFERENT, LIVE_ONLY)
# How a library target is matched to the connected workspace, best first. A tenant alone never
# matches: Lighthouse targets record the managing tenant, which every delegated client shares.
MATCHES = (("workspace ID", ("workspace_id",)),
           ("subscription, resource group and workspace name", ("subscription_id", "resource_group", "workspace_name")),
           ("tenant and workspace name", ("tenant_id", "workspace_name")))


def key(value):
    """A comparable form of an Azure identifier or name: trimmed, lower case; '' for nothing."""
    return str(value if value is not None else "").strip().lower()


def match_targets(clients, workspace):
    """The library targets that deploy to ``workspace`` and how they matched: ([client rows], how),
    or ([], None). The workspace ID (its customerId GUID) decides; failing that, the subscription,
    resource group and workspace name together; failing that, the tenant and workspace name."""
    for how, fields in MATCHES:
        found = [client for client in clients
                 if all(key(client["raw"]["azure"].get(field)) and
                        key(client["raw"]["azure"].get(field)) == key(workspace.get(field)) for field in fields)]
        if found: return found, how
    return [], None


def expected_rules(snapshot, client):
    """The rules ``client``'s target deploys, by lower-case rule ID, each the library rule with the
    target's overrides applied as the pipeline compiles it; and the assigned paths the library lacks."""
    rules = {rule["path"]: rule for rule in snapshot["rules"]}
    expected, unavailable = {}, []
    for assignment in client["assignments"]:
        rule = rules.get(assignment["path"])
        if rule is None:
            unavailable.append(assignment["path"]); continue
        overrides = assignment.get("overrides") or {}
        effective = dict(rule["raw"]); effective.update(overrides)
        rule_id = key(effective.get("id"))
        if rule_id and rule_id not in expected:  # The pipeline refuses a duplicate ID; the first stands.
            expected[rule_id] = dict(rule=rule, effective=effective, overrides=overrides)
    return expected, unavailable


def library_side(effective):
    """(state, severity) as the library sets them; None where the library leaves it open."""
    enabled = effective.get("enabled")
    severity = effective.get("severity")
    return (state(enabled) if isinstance(enabled, bool) else None,
            severity.strip() if isinstance(severity, str) and severity.strip() else None)


def differences(effective, row):
    """[(field, library value, workspace value)] for the enabled state and severity that differ. A
    value the library leaves unset is not compared: it sets no expectation."""
    want_state, want_severity = library_side(effective)
    _, live_state, live_severity, _ = rule_values(row)
    found = []
    if want_state is not None and live_state != want_state:
        found.append(("State", want_state, live_state))
    if want_severity is not None and key(live_severity) != key(want_severity):
        found.append(("Severity", want_severity, live_severity))
    return found


def drift(snapshot, records, workspace):
    """Compare a loaded library ``snapshot`` with the rule ``records`` read from ``workspace``.

    Returns a dict with ``status``: 'no_target' (no library target deploys to the workspace),
    'ambiguous' (several do; ``targets`` names them) or 'ok', and for 'ok' the matched ``client``,
    ``how`` it matched, ``entries`` (missing, then different, then live-only, each by name),
    ``in_sync`` (assigned rules that match), ``unavailable`` assigned paths and ``assigned``."""
    targets, how = match_targets(snapshot["clients"], workspace)
    if not targets: return dict(status="no_target", targets=[], how=None, entries=[])
    if len(targets) > 1:
        return dict(status="ambiguous", targets=[client["target"] for client in targets], how=how, entries=[])
    client = targets[0]
    expected, unavailable = expected_rules(snapshot, client)
    live = {}
    for row in records:
        live.setdefault(key(row.get("name")), row)
    library = {}
    for rule in snapshot["rules"]:
        library.setdefault(key(rule.get("id")), rule)
    entries, in_sync = [], 0
    for rule_id, plan in expected.items():
        row = live.get(rule_id)
        name = str(plan["effective"].get("name") or plan["rule"]["name"])
        base = dict(id=rule_id, name=name, rule=plan["rule"], effective=plan["effective"], overrides=plan["overrides"])
        if row is None:
            entries.append(dict(base, kind=MISSING, live=None, differences=[]))
        elif differences(plan["effective"], row):
            entries.append(dict(base, kind=DIFFERENT, live=row, differences=differences(plan["effective"], row)))
        else:
            in_sync += 1
    for rule_id, row in live.items():
        if rule_id in expected: continue
        rule = library.get(rule_id)  # In the library, but not assigned to this target.
        entries.append(dict(id=rule_id or str(row.get("name") or ""), name=rule_values(row)[0], kind=LIVE_ONLY, live=row,
                            rule=rule, effective=dict(rule["raw"]) if rule else None, overrides={}, differences=[]))
    entries.sort(key=lambda entry: (KIND_ORDER.index(entry["kind"]), entry["name"].casefold(), entry["id"]))
    return dict(status="ok", targets=[client["target"]], how=how, client=client, entries=entries,
                in_sync=in_sync, unavailable=unavailable, assigned=len(expected))
