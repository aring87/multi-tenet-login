# SOP: Sentinel client onboarding entirely through the websites

Version 1.1 | Applies to the existing private MSSP YOUR-DETECTION-REPOSITORY repository.

This procedure sets up a new client workspace through Microsoft Entra, Azure portal, and GitHub. No terminal is required. the operator can use the separate GIT-BASH-QUICKSTART.md to replace the Azure permission-deployment step while retaining the rest of this procedure.

## 1. Define the target

Use one target per Sentinel workspace. Example:

| Item | Value |
|---|---|
| Client folder and client field | acme |
| Workspace label | primary |
| Target | acme-primary |
| Target file | clients/acme/workspace.yml |
| Preview GitHub environment | acme-primary-preview |
| Production GitHub environment | acme-primary-production |
| Preview application | github-sentinel-acme-primary-preview |
| Deployment application | github-sentinel-acme-primary-deploy |

Another workspace for the same client uses a different target/file, such as acme-secondary and clients/acme/workspace-secondary.yml. Record the actual workspace resource name independently; it need not match the target.

## 2. Verify Azure and GitHub prerequisites

Activate the approved client CyberQP/JIT access. In Azure portal, select the correct directory before opening the subscription.

Confirm:
- The workspace already exists and Microsoft Sentinel is enabled.
- The onboarding operator can read the workspace, manage the required Entra apps, create Azure custom roles, assign Azure roles, and deploy a template at the resource group.
- Application-management permission in Entra does not itself grant Azure resource-management permission.
- Azure custom-role creation requires roleDefinitions/write over the assignable scope; role assignment requires roleAssignments/write. Use the client's approved onboarding administrator for these tasks.
- GitHub repository administrators can manage environments and branch protection; engineers who run workflows need appropriate repository access.
- Required reviewers are available under the organization's GitHub plan and enterprise policy.
- The current approved pipeline files are available. This onboarding package supplies Azure RBAC, not replacement rule-deployment code.

The runtime preview and deployment applications do not receive permission to create roles or assign access.

Record the tenant ID, subscription ID, resource group, workspace resource name and workspace GUID. Find tenant ID in Entra Overview; subscription in Azure Subscriptions; workspace values in Log Analytics workspace Overview/Properties. The target YAML's workspace_id is the workspace GUID, while this ARM template's workspaceName is the resource name.

## 3. Human users and groups

Use existing client-approved JIT identities and access groups when available. This pipeline does not require a new permanent human user or a shared password account.

If a client-approved human group is needed:
1. Open Microsoft Entra admin center > Entra ID > Groups > All groups > New group.
2. Choose Security, a descriptive client-specific name, and Assigned membership.
3. For a group used only for Azure resource RBAC, leave the option to assign Microsoft Entra directory roles disabled; Azure RBAC does not require a directory-role-assignable group.
4. Assign approved owners and add approved human members. Respect the client's JIT lifecycle; do not turn temporary access into permanent membership outside its process.
5. Create the group.
6. In Azure portal, open the intended resource group or workspace > Access control (IAM) > Add role assignment.
7. Select the approved human role. Sentinel Reader is for viewing; Sentinel Responder is for incident handling; Contributor includes resource/content changes. Use only the role needed by that group.
8. Select the group, review the scope, and assign.

Groups for humans are separate from the two pipeline applications. This package assigns runtime permissions directly to each application service principal. GitHub teams are also distinct from Entra groups unless your organization has deliberately configured synchronization.

## 4. Create the two application identities

In the client tenant:
1. Open Entra ID > App registrations > New registration.
2. Name the first app github-sentinel-acme-primary-preview.
3. Select accounts in this organizational directory only for this per-client design.
4. Leave redirect URI empty; register the application.
5. Record Application (client) ID and Directory (tenant) ID.
6. Assign the client-approved application owners.
7. Follow Managed application in local directory to the Enterprise application; record its Object ID.
8. Repeat for github-sentinel-acme-primary-deploy.

Use the IDs as follows:

| ID | Where it is used |
|---|---|
| Application (client) ID | GitHub environment AZURE_CLIENT_ID |
| Enterprise application Object ID | Azure role assignment / onboarding template |
| App registration Object ID | Application administration; do not paste it into this RBAC template |
| Directory (tenant) ID | Target YAML tenant_id |

The preview and deployment applications must be different. No client secret is needed for the OIDC login design. No Microsoft Graph application permission is needed merely to read/write Sentinel rules through Azure RBAC.

## 5. Prepare the private GitHub repository

For the existing repository, keep using its approved pipeline. Do not create a second repository for the client.

If recreating the repository:
1. Have an organization administrator create a private repository under the correct organization.
2. Add the approved pipeline files through Add file > Upload files, preserving .github/workflows, scripts, rules, clients and dependency files. Extract archives first.
3. Place manual workflow definitions on the default branch so the Run workflow button can be available.
4. In Settings > Actions > General, allow the approved actions according to enterprise policy. The Azure login jobs require id-token: write in their workflow YAML; the supplied workflow already declares it.
5. Assign engineering access and the reviewer team through the organization's normal repository/team access process.

Configure main protection under Settings > Rules > Rulesets (or the organization's existing branch-protection policy): require pull requests, the appropriate approvals, and the actual Sentinel CI check. Let CI run once so you can select its real check name. A production environment reviewer approval is separate from pull-request approval.

## 6. Add the client target on a working branch

Create and select a working branch. Use Add file > Create new file and enter clients/acme/workspace.yml.

Copy the schema from the working target, replacing all destination values:

```yaml
version: 1
target: acme-primary
client: acme
enabled: true
allow_missing_mitre: true
azure:
  tenant_id: REPLACE_WITH_CLIENT_TENANT_GUID
  subscription_id: REPLACE_WITH_SUBSCRIPTION_GUID
  resource_group: REPLACE_WITH_RESOURCE_GROUP
  workspace_name: REPLACE_WITH_WORKSPACE_RESOURCE_NAME
  workspace_id: REPLACE_WITH_WORKSPACE_GUID
rules: []
```

The client folder, client field and target prefix must agree. Use only one rules key.

allow_missing_mitre is an existing pipeline option, not an Azure permission. Keep it only where the team's approved exception policy allows missing MITRE metadata.

Start with an empty list while configuring access. Before the first selected-workspace run, select one approved disabled test rule: the supplied local sentinel.yml requires a rule path.

The supplied target input is free text. If your actual workflow was changed to a dropdown, update its allowed targets in the same pull request.

## 7. Create GitHub environments

Open Settings > Environments > New environment. Create acme-primary-preview and acme-primary-production before running workflows.

| Setting | Preview | Production |
|---|---|---|
| Azure client ID | Preview app client ID | Deployment app client ID |
| Branches | main for the supplied main-only workflow | main |
| Required reviewers | Per organization policy | Approved reviewer/team |
| Prevent self-review | Per policy | Enable when a separate reviewer is available |
| Administrator bypass | Per policy | Normally disabled |
| Wait timer | Only if policy requires it | Only if policy requires it |

Select Selected branches and tags and actually add a Branch rule for main. Selecting the dropdown without adding a rule does not impose that restriction.

The supplied local workflow restricts its prepare job to main. If the current repository intentionally supports branch previews, configure preview branch rules to match those branches as well. Do not assume changing the environment's branch rule will override a workflow's own main-only condition.

Under each environment's Variables, add AZURE_CLIENT_ID with the matching application client ID. The supplied workflow reads tenant and subscription IDs from the target YAML. Check your current YAML before adding redundant variables or secrets.

Save protection and branch rules. GitHub required-reviewer lists need only one listed reviewer to approve; they are not a requirement for every listed person to approve.

## 8. Configure OIDC trust

Use the existing Inspect OIDC workflow to obtain the issuer, audience and subject for each environment. Inspecting GitHub token claims should not require a successful Azure login; use the diagnostic workflow's intended inputs. Never copy a full token into repository files or tickets.

For each Entra application:
1. Open App registrations > application > Certificates & secrets > Federated credentials > Add credential.
2. Use the GitHub scenario or Other issuer/explicit subject entry path supported by the portal.
3. Enter issuer https://token.actions.githubusercontent.com.
4. Enter audience api://AzureADTokenExchange.
5. Enter the exact subject from the matching GitHub environment.
6. Use a unique credential name, review and save.

Your repository previously used an immutable subject containing organization and repository IDs, in this form:

```text
repo:<OWNER>@<OWNER-ID>/<REPOSITORY>@<REPOSITORY-ID>:environment:acme-primary-preview
```

Production must end with acme-primary-production and be trusted by the deployment app. Do not derive trust from the credential's friendly name: the subject field controls matching. Use the actual diagnostic claim even if the portal's wizard generated another format. An issuer can be shared across different credentials; issuer/subject uniqueness constraints and duplicate credentials should be checked when Azure reports a conflict.

Use explicit subject matching for this per-environment setup. Wildcard claims expressions are not needed here.

## 9. Deploy the permission template through Azure portal

This is the step the operator can instead run using Git Bash and Azure CLI.

1. Download/extract this package. Open Azure portal in the client directory.
2. Search Deploy a custom template > Build your own template in the editor.
3. Load azuredeploy.json, or paste its complete content, and select Save.
4. Select the correct subscription and the EXISTING resource group containing the workspace.
5. Enter targetName, workspaceName, previewPrincipalObjectId and deploymentPrincipalObjectId.
6. Use Enterprise application Object IDs for both principal parameters. Verify they differ.
7. Select Review + create and review the destination and identities.
8. Select Create.
9. After success, save the deployment Outputs, including four custom-role IDs.
10. Confirm the assignments in workspace and resource-group IAM.

The template creates four roles and four assignments:

| Role | Recipient | Assignment scope |
|---|---|---|
| Sentinel Preview | Preview application | Selected workspace |
| Sentinel Rule Writer | Deployment application | Selected workspace |
| Sentinel ARM Preview | Preview application | Resource group |
| Sentinel ARM Deployment | Deployment application | Resource group |

The writer role contains the operator's six confirmed actions:

```text
Microsoft.SecurityInsights/alertRules/read
Microsoft.SecurityInsights/alertRules/write
Microsoft.OperationalInsights/workspaces/read
Microsoft.OperationalInsights/workspaces/query/read
Microsoft.OperationalInsights/workspaces/query/*/read
Microsoft.OperationalInsights/workspaces/analytics/query/action
```

The workspace preview role uses the same list without alertRules/write. Neither workspace role grants rule deletion. ARM preview provides deployment read/validate/what-if operations; ARM deployment provides resource-group read and deployments/*.

Review effective access, including inherited and group-based grants. Adding a read/query role does not remove a preexisting Contributor or writer grant. Different workspaces in the same resource group share visibility of resource-group deployment metadata through the ARM roles; the rule/query assignments remain workspace-specific.

## 10. Confirm preview's validation mode

The supplied workflow runs cloud.py preview and saves a what-if artifact. Before testing the new preview identity, inspect scripts/cloud.py using the GitHub website.

For ARM what-if/validate calls performed by preview, confirm that the code supplies --validation-level ProviderNoRbac with Azure CLI 2.76.0 or later. This lets the provider check resource-read permissions. Default Provider validation can request rule-write permission.

The onboarding administrator's template validation is different: the administrator is actually granting RBAC, and should have the required write privileges. Do not change the normal deployment create call to read-only validation.

The matching cloud.py was not available when this package was prepared. No Python or workflow edits are included. If its argument construction is unclear, review that file before proceeding instead of granting preview the deployment identity's rights.

## 11. Test through GitHub

1. On the working branch, add one client-approved disabled test rule to the target's rules list.
2. Keep the same rule ID when modifying an existing test rule; generate a new ID only for a new rule identity.
3. Commit, open a pull request, and let Sentinel CI pass.
4. If the supplied main-only workflow is current, merge after review, then open Actions > Sentinel - selected workspace > Run workflow.
5. Select main, target acme-primary, the exact selected rule path, and mode preview.
6. Review successful authentication, query checks and what-if output.
7. Run the same target/rule from main with mode deploy.
8. Review the production environment approval and approve through the authorized reviewer.
9. Verify the rule in the intended Sentinel workspace and confirm it is disabled.
10. Record the pull request, workflow run, onboarding deployment and verification result.

If your actual workflow supports working-branch preview, that preview may occur before merge. Production deployment remains from main. CI passing alone does not mean a rule was deployed.

## 12. Repeat, update and remove access

Repeat for another target with its own identities and environment trust. Template IDs are deterministic: rerunning unchanged scope/principal values updates the managed roles rather than creating random duplicates.

Changing a principal or workspace does not revoke old assignments. Use IAM to remove superseded assignments once the replacement is verified. Deleting deployment history does not remove RBAC.

Human group membership remains governed by the client's access/JIT process. Do not use the runtime deployment identity for onboarding other identities.

## Completion record

Record: client; target; tenant/subscription; resource group/workspace; both app client IDs; both service principal Object IDs; app owners; GitHub environment names; exact federated subjects; four role IDs; onboarding run/deployment; pilot rule ID; verifier/date.

## References

- [Azure portal template deployment](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/quickstart-create-templates-use-the-portal)
- [Azure custom-role setup](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles-portal)
- [Entra group management](https://learn.microsoft.com/en-us/azure/active-directory/fundamentals/how-to-manage-groups)
- [OIDC federation in Entra](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust)
- [GitHub environment setup](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [GitHub manual workflow runs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [ARM what-if validation](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-what-if)
