# Sentinel Client Onboarding — desktop app

## Start
Double-click Start-Sentinel.cmd in the extracted folder. Keep all application files together. Python 3.10+ with Tkinter and Azure CLI 2.76+ are required; full GitHub onboarding also requires GitHub CLI. Install these dependencies before first use.

This is the first local desktop version. It does not require a web server, an app registration for the desktop interface, or an AI service.

## First use
1. Activate approved Azure access using your organization's normal process, if activation is required.
2. Choose a saved client in Connect, or use Add client to create the first entry. No client is prefilled.
3. For another client, select Add client and enter its display name, client slug and tenant GUID or verified tenant domain. A domain can be used when you have not yet collected the GUID.
4. Click Sign in with Microsoft. Complete the Windows Microsoft account window or browser sign-in with the activated JIT account and MFA. This app has no password field.
5. Choose the subscription, then click Discover workspaces.
6. Select the correct Log Analytics workspace. The app displays the signed-in account, tenant ID, subscription ID, resource group, workspace name and workspace GUID. It lists accessible Log Analytics workspaces; verify that the selected workspace is the one using Sentinel.
7. Click Copy workspace details if you only need the discovered information. Save client mapping retains the client name, slug and tenant hint for the dropdown.

A CyberQP customer name and an Azure tenant are not assumed to be the same identifier. This release uses manually saved client mappings. CyberQP API activation and automatic customer import are not yet implemented.

## Choose setup options

### Permissions only
Use this for existing preview and deployment applications. Enter their Enterprise applications > Object ID values. The app verifies both resolve to service principals and uses the shared Azure permission template. GitHub access, app-owner user IDs and reviewer settings are not needed for this operation.

Preview performs Azure template validation and what-if. Apply repeats those checks and creates the scoped RBAC resources. The writer has the six documented rule/query actions; the preview identity lacks rule-write. Separate ARM roles are assigned at the resource group.

### Full Azure + GitHub onboarding
Use this for a new client target. Fill in the GitHub organization/repository, approved Azure app-owner user Object IDs, production reviewers, and OIDC format. Sign in to GitHub using the button if needed; follow the browser authorization and any one-time code shown in Review.

An initial rule path is optional. Without a rule, the target is created disabled to pass validation. With a rule, the target is enabled and that rule gets an enabled: false override.

Full onboarding runs the existing full_onboarding.py logic to create apps, OIDC, environments, permissions and a target pull request. Imported configurations may include optional human groups or explicit existing app IDs; these are preserved. This UI accepts one reviewer type per configuration (all User or all Team).

Export onboarding JSON saves the configured, discovered destination for reuse. Import onboarding JSON restores setup options, but requires fresh Azure sign-in and workspace discovery. Additional group editing and complex configurations remain available in the JSON-based workflow.

## Preview and apply
Go to Review, click Preview setup, and inspect the destination and output. Full onboarding's plan is a read-only preflight; its ARM what-if occurs during apply after new service principal IDs exist. Permissions-only preview performs actual ARM what-if.

Apply reviewed setup requires a successful preview of the same configuration within the last 15 minutes. A destination summary is shown before changes begin. The application rechecks the Azure session before deployment. A failed or changed plan must be run again.

For the primary identity label, the manifest filename is workspace.yml while the target remains <client>-primary. An additional label such as secondary produces workspace-secondary.yml and a separate target. For an existing primary.yml, rename the file in a reviewed pull request while preserving the target field, rule selections and existing environment names.

Full onboarding ends with a PR. Review CI and merge normally, verify GitHub administrator-bypass policy, then run a disabled-rule pilot through your existing pipeline. No analytics rule is deployed by this desktop tool.

## Access and data
Use the same approved JIT bootstrap permissions described in FULL-ONBOARDING.md. Being able to read a workspace does not automatically grant permission to create custom roles, assignments or applications.

The desktop keeps its Azure CLI configuration in a separate folder per session under desktop-data/sessions. Azure CLI may cache authentication tokens there. The Microsoft Windows account broker/browser may also retain sign-in state under Microsoft's normal behavior. The app does not collect or store passwords.

Sign out of Azure session clears that app session's Azure CLI login. Closing the app normally also requests logout. This does not deactivate access at your privileged-access provider or sign you out of other Microsoft browser/Windows sessions. Finish JIT access through your organization's normal process.

desktop-data/clients.json contains the saved dropdown mappings. desktop-data/runs holds resource state and parameter files needed for retrying partial onboarding. Treat this as local administration data and do not commit or share desktop-data. It is excluded from the downloadable ZIP.

Cloud operations run in a background thread. Closing is blocked while an operation is running. A timeout or failure can leave partial cloud changes; retain state and inspect the reported result before retrying. The app does not automatically roll back cloud resources.

## Verification
20 offline desktop tests passed, including UI startup, session cache separation, destination parsing, permission preview/apply ordering, validation failure handling and missing/expired plan rejection. The underlying onboarding package's 22 tests also passed before this interface was added.

Launching the app performs no Azure/GitHub sign-in or deployment. Live discovery and a client-approved pilot still need to be performed after the operator signs in.

References:
- [Azure CLI interactive sign-in](https://learn.microsoft.com/cli/azure/authenticate-azure-cli-interactively)
- [Azure CLI configuration](https://learn.microsoft.com/en-us/cli/azure/azure-cli-configuration)
- [Log Analytics workspace discovery](https://learn.microsoft.com/en-us/cli/azure/monitor/log-analytics/workspace)
- [CyberQP API for a future integration](https://tours.cyberqp.com/answers/cyberqp-public-api-documentation)

## Interface update - 25 September 2026
The app now uses a three-step sidebar: Connect, Configure, Review. Configure displays the fields for the selected operation. Each page scrolls for longer forms, and the Activity panel has its own scrollbar.

Delete client removes the selected saved mapping after confirmation. It clears the active workspace selection and preview, but retains local onboarding state and leaves Azure, GitHub and CyberQP resources unchanged. Deleting the final saved client leaves the dropdown empty; Add client restores an entry. Deletion is blocked while an operation is running.

Close an older open copy and reopen Start-Sentinel.cmd to load this version. No client records are deleted by installing the update.

## CyberQP browser shortcut and tenant entry
On Connect, select the optional CyberQP region (US, EU, Canada), then click Sign in to CyberQP. The button opens the corresponding official portal in your default browser. Complete sign-in and any JIT activation there, then return to Sign in with Microsoft. No CyberQP credentials or API tokens are collected by the application.

The Tenant ID or verified domain field is for a Directory (tenant) GUID or a verified tenant domain, such as client.onmicrosoft.com. Do not enter azure.portal.com, portal.azure.com, a website URL or a user email address. Find the tenant GUID under Microsoft Entra ID > Overview. The app now rejects common portal addresses before launching Azure sign-in. A syntactically valid domain must still belong to a real accessible tenant.

Portal regions are documented by [CyberQP](https://support.getquickpass.com/hc/en-us/articles/23325164900119-Getting-started-with-the-CyberQP-API). This shortcut is optional and does not implement API-based JIT activation.
