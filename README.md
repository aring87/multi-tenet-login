# Sentinel Client Onboarding

A local Windows desktop app for connecting to client Azure tenants, discovering Log Analytics workspaces, and preparing Microsoft Sentinel deployment permissions.

## What it does

- Saves client names and tenant IDs/domains in a local dropdown.
- Opens Microsoft sign-in for an already-activated JIT account.
- Discovers accessible subscriptions and Log Analytics workspace details.
- Previews and applies scoped permissions for existing preview/deployment service principals.
- Optionally onboards Azure identities, OIDC, GitHub environments, and a client configuration pull request.
- Deletes saved client entries without deleting cloud resources.
- Requires a successful matching preview before Apply.

CyberQP account activation is manual in this version. The public app starts with an empty client list and no preconfigured organization.

## Start on Windows

1. Download this repository using **Code > Download ZIP**, then extract it.
2. Install Python 3.10 or newer with Tkinter, and Azure CLI 2.76 or newer.
3. Install GitHub CLI if you want full Azure + GitHub onboarding.
4. Double-click **Start-Sentinel.cmd**.
5. Select **Add client**, enter a display name, client slug, and tenant GUID or verified domain.
6. Activate JIT access through your organization's normal process, then select **Sign in with Microsoft**.
7. Choose a subscription and select **Discover workspaces**.

No third-party Python packages are required. The desktop uses the installed Azure CLI for Microsoft authentication.

## Two setup modes

| Mode | Purpose |
|---|---|
| Permissions only | Validate, preview, and apply the included Azure RBAC template to existing service principals |
| Full Azure + GitHub onboarding | Create separate application identities, OIDC trust, GitHub environments, scoped RBAC, and a client-target pull request |

This application repository is separate from your private detection-rule repository. In full onboarding, configure the private repository that contains your existing Sentinel pipeline.

Full onboarding assumes an existing main-based workflow using environment names <target>-preview and <target>-production and the AZURE_CLIENT_ID environment variable. It does not create the Sentinel workspace, connectors, detection pipeline, or analytics rules.

## Documentation

- [Desktop guide](DESKTOP-START-HERE.md)
- [Full onboarding setup](FULL-ONBOARDING.md)
- [Web-only procedure](PORTAL-ONLY.md)
- [Permissions-only Git Bash route](GIT-BASH-QUICKSTART.md)
- [Verification notes](VALIDATION.md)

## Local data and access

Runtime data lives in desktop-data/, which is excluded from Git:
- Saved client mappings.
- Per-session Azure CLI configuration and authentication caches.
- Onboarding state and generated parameter files.

Do not commit or share this folder. The application has no password-entry field. Microsoft manages authentication through its normal broker/browser flow. Signing out of the app's Azure session does not deactivate CyberQP JIT access or sign out other browser/Windows sessions.

Use an approved onboarding account with the required Azure/Entra permissions. Runtime deployment identities do not receive RBAC administration privileges. Existing and inherited grants remain additive.

Delete client removes only the saved mapping and invalidates the current selection/preview. Cloud resources and onboarding state are retained.

## Development and checks

```text
python -m unittest discover -s tests -p "test_*.py" -v
```

The tests simulate Azure/GitHub calls and do not contact a live tenant. Desktop tests require Tkinter and a Windows desktop-capable session. GitHub Actions runs the Python tests on Windows.

Live authentication, tenant policies, API permissions, and a disabled-rule deployment pilot still require validation in an authorized environment. The app is an initial implementation, not a claim of production certification.

## Files

| File | Purpose |
|---|---|
| desktop_app.py | Desktop interface and client inventory controls |
| desktop_backend.py | Azure discovery, isolated CLI sessions, and setup execution |
| full_onboarding.py | Reusable onboarding orchestration |
| azuredeploy.json | Scoped custom roles and assignments |
| Start-Sentinel.cmd | Windows launcher |
| onboard-all.sh | Optional Git Bash launcher |
| onboarding.example.json | Placeholder configuration for command-line onboarding |
| tests/ | Offline regression tests |
