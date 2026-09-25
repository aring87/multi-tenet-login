# Git Bash + Azure CLI: fast Sentinel permission onboarding

Use this route for the Azure RBAC step of PORTAL-ONLY.md. Keep using the Entra and GitHub websites for applications, OIDC credentials, environments and normal rule delivery.

## 1. Prepare once

- Use an approved installation of Git for Windows (Git Bash) and Azure CLI 2.76.0 or newer.
- Reopen Git Bash after installing Azure CLI so it receives the updated PATH.
- Extract the onboarding ZIP to a folder. Keep onboard-permissions.sh beside azuredeploy.json.
- Open that folder with **Git Bash Here**, or use cd to enter it.
- Run `az version` to verify the CLI is available.

No local Git clone is required. The script uses Bash and Azure CLI; Python, jq and Azure PowerShell modules are not required.

## 2. Complete the client's one-time identity setup

Follow PORTAL-ONLY.md through the identity/environment steps:
- Activate the approved CyberQP/JIT onboarding account in the client tenant.
- Create the separate preview and deployment apps.
- Configure their exact OIDC environment subjects.
- Record each Enterprise application's Object ID.
- Ensure the onboarding account can create Azure custom roles, assign roles, deploy at the resource group, and read those service principals in Entra.

The script uses the human onboarding account, not either runtime app. It cannot bootstrap access to an unauthorized client tenant.

## 3. Create a reusable client config

Copy client.example.conf to acme-primary.conf using File Explorer, or in Git Bash:

```bash
cp client.example.conf acme-primary.conf
```

Open acme-primary.conf in a text editor and replace every example. Example structure:

```text
TARGET=acme-primary
TENANT_ID=YOUR-CLIENT-TENANT-GUID
SUBSCRIPTION_ID=YOUR-CLIENT-SUBSCRIPTION-GUID
RESOURCE_GROUP=YourSentinelResourceGroup
WORKSPACE_NAME=YourWorkspaceName
PREVIEW_SP_OBJECT_ID=PREVIEW-ENTERPRISE-APPLICATION-OBJECT-GUID
DEPLOY_SP_OBJECT_ID=DEPLOY-ENTERPRISE-APPLICATION-OBJECT-GUID
```

Use plain values without quotation marks. A resource group containing spaces is written directly after the equals sign. There is no variable expansion or shell execution in this file. Inline comments after values are not supported; place comments on separate lines.

The app IDs MUST be **Enterprise application Object IDs**. Application client IDs are used for GitHub login, not these role-assignment parameters. Preview and deployment must be different applications.

The config contains identifiers, not passwords. Store it only where your company allows client configuration data. Never add JIT passwords, tokens or application secrets.

## 4. Sign in to the client

In Git Bash:

```bash
az login --tenant "YOUR-CLIENT-TENANT-GUID"
```

Complete sign-in with the approved client onboarding account. Follow your company's sign-in policy if a broker or account picker appears. Signing in does not grant roles you do not already possess.

The script will select the configured subscription only after checking its tenant, then recheck the active tenant before its directory lookups.

## 5. Preview the permission changes

```bash
bash onboard-permissions.sh --config acme-primary.conf
```

This checks:
1. Required configuration, GUIDs and distinct preview/deployment identities.
2. Azure CLI version.
3. Requested subscription's tenant and the active tenant.
4. The actual workspace resource ID.
5. Both service principal Object IDs through Entra.
6. Azure template validation.
7. ARM what-if for the permission changes.

It displays the target workspace and application names/IDs before the what-if result. The default run does not submit role-definition or role-assignment changes.

Review the four roles and four assignments. Compare the application names with the preview and deployment apps. Entra lookup proves that an Object ID identifies a service principal; it cannot decide which app you intended to use.

## 6. Apply the permissions

After the preview looks correct:

```bash
bash onboard-permissions.sh --config acme-primary.conf --apply
```

This repeats the checks and what-if, then submits an **Incremental** ARM deployment. The --apply flag explicitly authorizes the write; there is no extra interactive confirmation after the script starts.

The Azure deployment name is sentinel-rbac-acme-primary. The script waits for completion and displays the deployment outputs.

If validation or what-if fails, the script stops before submitting role changes. If create fails, partial changes can exist: inspect Azure deployment operations before retrying.

## 7. Verify and continue in GitHub

In Azure portal, verify:
- Preview workspace role belongs to the preview application.
- Writer workspace role belongs to the deployment application.
- Resource-group ARM Preview and ARM Deployment roles belong to the corresponding applications.
- Effective access has no preexisting broad grants that undermine the intended separation.

Then complete the GitHub steps in PORTAL-ONLY.md. Select one disabled test rule, pass CI, preview and deploy it through the approved workflow, and verify it in the new client's Sentinel workspace.

## Repeat for another client

Copy the example config to another client/workspace file, enter that client's values, activate/sign in with the correct JIT onboarding account, then repeat preview and apply. Both client applications must already exist.

If a client has several workspaces, use a separate target/config and identity pair for each, as in the current design. No manual permission-by-permission clicking is required for the four roles this template manages.

The script does not log into every client automatically, use stored passwords, create groups/apps, or create GitHub environments. Bulk directory provisioning would be a separate onboarding component requiring client-tenant authorization.

## Re-running and updating

The same workspace/principal configuration reuses deterministic role and assignment IDs. It can update the roles from the same template.

Changing the principal, workspace or subscription does not remove old grants. Use IAM to retire superseded assignments after confirming the replacement. Azure RBAC grants are additive.

When a just-created role is temporarily unavailable due to propagation, wait and retry the same config; do not broaden access to work around it.

## Why Git Bash path handling is included

Git Bash can rewrite Azure /subscriptions/... arguments as Windows paths. The script disables that conversion for Azure calls and explicitly converts the local template filename to a Windows-compatible path. Resource group names and filesystem paths containing spaces remain separate, quoted arguments.

Keep .sh files with LF line endings. The included .gitattributes helps preserve this if the package is committed to GitHub.

## Two different previews

**Onboarding preview in this script:** the authorized onboarding account validates proposed role definitions and assignments with Provider validation. That account is expected to possess permission to grant the access.

**Normal Sentinel rule preview in GitHub:** the preview app runs query checks and ARM what-if with read-only resource checks. Its cloud.py calls must use ProviderNoRbac as explained in README/PORTAL-ONLY. Do not change the onboarding script to use the low-privilege preview app.

## Troubleshooting

| Problem | Next action |
|---|---|
| az not found | Install approved Azure CLI, then close/reopen Git Bash |
| Config rejected | Replace placeholders; use plain KEY=value; remove duplicates/unknown keys |
| Wrong tenant | Stop and sign in to the specified client's tenant |
| App client ID supplied | Copy Object ID from Enterprise applications |
| Cannot verify principal | Check client directory, SP existence and the onboarding user's directory-read access |
| Workspace mismatch | Recheck subscription, resource group and workspace resource name |
| AuthorizationFailed during validation | Check the onboarding user's role-definition, role-assignment and deployment permissions |
| ARM preview on runtime identity fails | Check ProviderNoRbac and runtime CLI version; do not grant preview the writer role |
| Runtime preview runs are skipped | The supplied sentinel.yml restricts prepare to main; use the actual workflow's supported branch |
| Apply fails after create starts | Inspect Azure deployment operations for any partial changes |

## Validation status

The script passed Bash syntax validation and 16 offline tests using a simulated Azure CLI. No Azure tenant was contacted during those tests. Azure provider validation and a real onboarding pilot still occur when you run the script with your authorized account.

Sources: [Azure CLI installation](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows), [deployment CLI](https://learn.microsoft.com/en-us/cli/azure/deployment/group), [service principal lookup](https://learn.microsoft.com/en-us/cli/azure/ad/sp), [Git Bash path-conversion issue documented in Azure CLI](https://github.com/Azure/azure-cli/issues/30132).
