"""Export a previously collected Sentinel rule inventory without cloud calls."""
import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from audit_evidence import csv_value, flatten
from workspace_tools import rule_values


def export_rules(destination, workspace, collection):
    """Atomically write raw records, a spreadsheet and provenance into a ZIP."""
    rows = collection["records"]
    complete = collection.get("status") in ("collected", "no_records")
    if not rows and not complete:
        raise ValueError("No rule inventory is available. Refresh rules before exporting.")
    exported = datetime.now(timezone.utc).isoformat()
    csv_rows = []
    for row in rows:
        name, state, severity, kind = rule_values(row)
        csv_rows.append(dict(rule_name=name, state=state, severity=severity, kind=kind,
                             **{"raw." + key: value for key, value in flatten(row).items()}))
    columns = ["rule_name", "state", "severity", "kind"]
    columns += sorted({key for row in csv_rows for key in row} - set(columns))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([csv_value(key) for key in columns])
    writer.writerows([[csv_value(row.get(key)) for key in columns] for row in csv_rows])

    def encoded(value):
        return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    files = {"rules.json": encoded(dict(workspace=workspace, collection=collection)),
             "rules.csv": stream.getvalue().encode("utf-8-sig")}
    manifest = dict(schema_version=1, exported_at_utc=exported,
                    workspace=workspace, complete=complete,
                    record_count=len(rows), collection={k: v for k, v in collection.items() if k != "records"},
                    scope="All loaded analytics rules, including disabled rules, within the signed-in account's visibility. Search and state filters do not limit this export.",
                    note="Configuration snapshot from the recorded collection time; export does not refresh Azure. CSV cells that could be formulas are escaped; JSON retains original values.",
                    files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    files["manifest.json"] = encoded(manifest)
    path = Path(destination)
    # Stage beside the destination so a failed write never replaces a prior audit.
    fd, temporary = tempfile.mkstemp(prefix=".rules-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return manifest
