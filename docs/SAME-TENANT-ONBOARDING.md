# Add a workspace hosted in your managing tenant

Use this for a client workspace that is already in the managing tenant, such as
an internal or hosted client workspace. The app detects matching tenant IDs and
chooses **Same-tenant workspace registration** automatically.

## What happens

1. Sign in to the managing tenant and select the actual subscription and workspace.
2. Enter the client slug and workspace label. Keep one folder per client, for example
   client `413`, label `workspace-gcc` produces `clients/413/413-workspace-gcc.yml`.
3. Select **Preview setup**. The app checks the destination, security groups,
   pipeline applications' group membership, shared environments, existing role
   assignments and repository catalog.
4. Review the existing roles and their scopes in Operation activity. The preview
   makes no GitHub or Azure changes. Missing permissions stop it with the expected
   roles, group IDs and resource-group scope.
5. Select **Apply reviewed setup** within 15 minutes. The app repeats the checks
   and opens the client target-file and dropdown pull request. It does not create
   a Lighthouse delegation or modify Azure resources, roles, groups or identities.
6. Review and merge the PR, refresh the repository catalog, then run a pipeline
   preview on main. Any initial rule added by onboarding is explicitly disabled.

## Existing access prerequisites

Use the same groups and shared GitHub environments configured in app settings.
The signed-in operator needs access to read the workspace, role assignments and
directory objects, plus admin access to the private detection repository as required
by the existing onboarding flow. Directory queries may require interactive MFA.

| Group | Expected built-in roles |
| --- | --- |
| Deploy | Microsoft Sentinel Contributor |
| Preview | Microsoft Sentinel Reader; Log Analytics Reader |
| Engineers | Microsoft Sentinel Reader; Reader |

The expected unconditional role assignments must apply at the workspace's resource
group or be inherited from its subscription or root. The app reports the actual
scope it found. It does not evaluate equivalent custom roles, conditions,
management-group assignments, or alternative broader roles; a stopped check means
the documented access model could not be confirmed, not proof the identity has no access.
Have the access administrator review the assignments rather than granting broader
access merely to pass the check.

The preview and production `AZURE_CLIENT_ID` values must identify different existing
applications whose service principals belong to the configured preview and deploy
groups respectively. No group members are added automatically.

Registration checks do not execute KQL or impersonate the pipeline identities.
Live preview and, when authorized, an approved deployment remain necessary to
verify effective preview and production access, including deny assignments and
connector/table availability.

## Existing targets and retrying

If this workspace is already registered under another target or filename, the app
shows that path and stops before creating a duplicate. Use the existing target.
An exact matching target at the expected path is reused; existing configuration
differences are never overwritten. Branch ownership and changed-file protections
are the same as the Lighthouse path. If a GitHub operation fails, review any
created branch/PR before retrying; no Azure changes were made by this path.

## Commercial and GCC

This path supports workspaces in Azure Public (`AzureCloud`). A `gcc` label does
not select Azure Government. GCC customers may have resources in either Azure
cloud; verify the actual subscription. Azure Government is rejected by this path
and needs separate cloud configuration.

For workspaces in another tenant, the existing subscription-scope Lighthouse flow
still applies. It cannot delegate a subscription to the tenant that already owns it.
