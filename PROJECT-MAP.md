# Project file map

Start with Start-Sentinel.cmd and README.md. Most root Python files are imported
application modules, so they remain together to preserve launchers and CLI usage.

| Area | Files |
|---|---|
| Desktop UI | desktop_app.py, desktop_theme.py, signin_window.py, rules_page.py |
| Repository UI | repository_catalog.py, catalog_page.py, rule_builder_page.py, rule_drafts.py, rule_schema.py |
| Rule review submission | repository_reviews.py, rule_review_dialog.py, review_history.py (private repository draft PRs and local request history) |
| Azure sessions and discovery | desktop_backend.py, auth_recovery.py, workspace_tools.py |
| Current onboarding | lighthouse_onboarding.py, lighthouse-onboard.json, APP-ONBOARDING.md |
| Audit collection and analysis | audit_evidence.py, audit_analysis.py, AUDIT-EVIDENCE.md |
| Optional older setup routes | full_onboarding.py, onboard-all.sh, onboard-permissions.sh, azuredeploy.json |
| Workspace rename utility | rename_workspace_v2.py, Rename-Workspace-v2.cmd |
| Examples | *.example.json, client.example.conf (placeholders only) |
| Verification | tests/, .github/workflows/ci.yml |
| Uploaded reference sources | detection-as-code/ (not loaded as application code) |
| Local state | desktop-data/ (ignored by Git) |

The legacy CLI routes and their documentation are retained because they are still
usable entry points. Removing or moving them requires a separate migration.
