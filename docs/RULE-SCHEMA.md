# Reviewed rule schema

The application imports sentinel_app/rule_schema.py, a reviewed copy of detection-as-code/rules.py
from the uploaded reference at app main commit 8411dba. It never imports validator
code from a selected local or remote repository.

SHA-256: `3e869b3f00be1c76911ee3889a67e81ad96112edb96585aa61a9c4eaac32366a`

Hash uses UTF-8 with LF line endings. To change the supported schema, review new
reference uploads, update the bundled module and this hash, then run the draft and
full regression tests. An upload alone does not change runtime validation.

The pilot supports Scheduled and NRT fields in that reference, preserves advanced
properties during editing, and calls its validate and to_arm functions before export.
Its checks are not a full ARM schema validation, MITRE relationship validation, KQL
execution, or a guarantee that the private repository still uses the same revision.
The repository pipeline remains authoritative and handles client assignments,
overrides, preview, approval, and deployment.
