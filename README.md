# Sentinel Client Onboarding

## Discover workspace details without a tenant ID

1. Start the desktop app. Azure CLI and Python must be installed; no terminal commands are needed during use. GitHub CLI is only needed for full onboarding.
2. Use **Sign in to CyberQP** if applicable and activate the client's JIT account there.
3. Leave **Tenant ID or domain (optional)** blank. Clear a previously selected client's tenant when connecting to another client. Creating a saved client is optional.
4. Click **Sign in & discover**. Complete Microsoft browser authentication and MFA using the authorized client account. Use another account in the Microsoft prompt if your browser suggests the wrong one.
5. The app automatically selects the default accessible subscription and loads its Log Analytics workspaces. Review the **Subscription** dropdown, which includes the tenant ID. Selecting another subscription automatically reloads its workspaces.
6. Select the intended workspace if several are available. The **Workspace details** text box shows **Tenant ID, Subscription ID, Resource group, Workspace name, and Workspace ID**. Workspace ID is the Log Analytics customer GUID, not the ARM resource path.
7. Click **Copy workspace details** to copy the displayed text. Nothing is deployed or assigned during discovery. Saved client profiles are optional and remain local.

**Open Azure portal** is a separate browser shortcut. A portal-only sign-in cannot populate this app's authenticated session; use **Sign in & discover** to authorize discovery. The app never collects your password. Windows account window remains available for organizations that require broker-based authentication.

Only resources accessible to the signed-in account are returned. No subscriptions means the account needs appropriate subscription access or the correct directory; no workspaces means check the selected subscription and workspace read access. Discovery lists Log Analytics workspaces and does not assert that Sentinel is enabled on each. Advanced permission/onboarding tools remain separate and require explicit review and apply.


A local Windows desktop app for connecting to client Azure tenants, discovering Log Analytics workspaces, and preparing Microsoft Sentinel deployment permissions.

## What it does

- Saves client names and tenant IDs/domains in a local dropdown.
- Opens Microsoft sign-in for an authorized Azure account, including JIT accounts.
- Discovers accessible subscriptions and Log Analytics workspace details.
- Previews and applies scoped permissions for existing preview/deployment service principals.
- Optionally onboards Azure identities, OIDC, GitHub environments, and a client configuration pull request.
- Deletes saved client entries without deleting cloud resources.
- Requires a successful matching preview before Apply.

The app is independent of any employer, customer, or privileged-access provider. CyberQP is optional; activate access through your own provider if your organization requires it. The app starts with an empty client list and blank GitHub settings.

Discovery and permissions-only mode do not require GitHub or CyberQP. Full onboarding requires a compatible private detection repository and GitHub plan supporting its environment policies.

## Start on Windows

1. Download this repository using **Code > Download ZIP**, then extract it.
2. Install Python 3.10 or newer with Tkinter, and Azure CLI 2.76 or newer.
3. Install GitHub CLI if you want full Azure + GitHub onboarding.
4. Double-click **Start-Sentinel.cmd**.
5. Select **Add client**, optionally save a display name and client slug; the tenant GUID/domain can be left blank.
6. Activate JIT access through your organization's normal process, then select **Sign in with Microsoft**.
7. Workspaces load automatically after sign-in or changing the subscription. Use **Refresh workspaces** to reload them.

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

## Public examples\n\nExamples use fictional resource names and placeholder or synthetic identifiers. No actual client inventory is distributed. See [Privacy and publication notes](PRIVACY.md).\n\n## Local data and access

Runtime data lives in desktop-data/, which is excluded from Git:
- Saved client mappings.
- Per-session Azure CLI configuration and authentication caches.
- Onboarding state and generated parameter files.

Do not commit or share this folder. The application has no password-entry field. Microsoft manages authentication through its normal broker/browser flow. Signing out of the app's Azure session does not deactivate access at your privileged-access provider or sign out other browser/Windows sessions.

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


## License

Licensed under the [MIT License](LICENSE).
