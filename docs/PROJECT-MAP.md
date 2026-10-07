# Project file map

Start with [README](../README.md) and **Start-Sentinel.cmd** in the repository root.
The application code is a Python package; supporting files are grouped by purpose.

```text
multi-tenet-login/
  Start-Sentinel.cmd       Windows desktop launcher
  README.md               Getting started and documentation links
  requirements.txt        Python dependencies
  sentinel_app/           Desktop UI, Azure services, GitHub workflows, rule builder
  docs/                   User guides, design notes, validation and privacy information
  templates/              Azure deployment templates
  examples/               Placeholder configuration files to copy and customize
  scripts/                Optional command-line and workspace maintenance utilities
  tests/                  Offline tests and fixtures
  .github/workflows/      Automated checks
  detection-as-code/      Sanitized reference uploads for the private repository
  desktop-data/           Local clients, settings and sessions (ignored by Git)
```

## Application code

All the following modules live in `sentinel_app/`:

| Area | Modules |
|---|---|
| Desktop and sign-in UI | desktop_app, connection_page, desktop_theme, signin_window, signin_process |
| Analytics rule inventory and export | rules_page, rule_export |
| Repository catalog | repository_catalog, catalog_page |
| Reviewed client rule assignments | rule_assignments, assignment_dialog |
| Guided rule builder | rule_builder_page, rule_drafts, rule_schema |
| Rule reviews and history | repository_reviews, rule_review_dialog, review_history |
| Saved workflow history | workflow_history, workflow_history_dialog |
| GitHub previews and deployments | preview_workflow, preview_dialog, deployment_workflow, deployment_dialog |
| Azure sessions and workspace access | desktop_backend, auth_recovery, workspace_tools, tenant_access, access_readiness |
| Current Lighthouse onboarding | lighthouse_onboarding, yaml_identifiers |
| Audit collection and analysis | audit_evidence, audit_analysis |
| Legacy per-client identity onboarding | full_onboarding |
| Resource and data locations | paths |

## Running commands

Run these from the repository root, the folder containing `Start-Sentinel.cmd`:

```powershell
py -m pip install -r requirements.txt
py -m sentinel_app
py -m sentinel_app.lighthouse_onboarding --help
py -m sentinel_app.full_onboarding --help
py -m unittest discover -s tests -p "test_*.py" -v
```

The GUI launcher still works by double-clicking it. Command-line users should replace
`python desktop_app.py` with `python -m sentinel_app`; the old loose Python entry
points have moved. Do not run individual package modules as file paths.

The optional Git Bash routes are `bash scripts/onboard-all.sh --help` and
`bash scripts/onboard-permissions.sh --help`. The standalone workspace rename tool
is [scripts/Rename-Workspace-v2.cmd](../scripts/Rename-Workspace-v2.cmd).

## Upgrading an existing checkout

Close the app, merge the organization PR, and pull main. Git handles the file moves.
Keep your existing `desktop-data/` folder in the repository root; no client import,
settings migration, or sign-in-cache move is required. Its paths are independent of
the terminal working directory. If downloading a ZIP, extract into a new folder
and copy your local `desktop-data/` there instead of overlaying the old source files.

Continue uploading sanitized private-repository files into `detection-as-code/`.
Its reference ZIP and scripts are not application modules. This reorganization
requires no file replacement or workflow change in the private detection repository.
