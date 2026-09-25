# Full client onboarding automation
Azure and GitHub onboarding reference

## What this package automates

Run this once for each client workspace. One JSON file identifies the client, workspace, app owners, and production reviewers. The script uses your signed-in Azure and GitHub accounts.

It creates two single-tenant app registrations and their enterprise applications, app ownership, environment-specific OIDC credentials, GitHub preview and production environments, main-only branch restrictions, production reviewers with self-review prevented, AZURE_CLIENT_ID environment variables, four scoped Azure roles and assignments, optional human access groups, and a client target file in a pull request.

It reuses the supplied azuredeploy.json. The web-only procedure remains available in PORTAL-ONLY.md. The permissions-only onboard-permissions.sh is an alternative for operators who have already created the identities and GitHub settings.

This automates onboarding to the existing private detection repository. It does not create the repository, Sentinel workspace, connectors, JIT accounts, GitHub teams, or the detection pipeline itself. It does not run a rule deployment, merge the pull request, or alter existing rule YAML.

## 1. Prerequisites

Install Git for Windows (Git Bash), Azure CLI 2.76.0 or newer, GitHub CLI, and Python 3.10 or newer. No Python packages or local repository clone are required. Extract the whole ZIP to a simple folder, such as C:/SentinelOnboarding; keep full_onboarding.py and azuredeploy.json together.

Activate the client-approved Azure account and the required onboarding access:
- Entra permissions to create applications/service principals and manage their owners and federated credentials. Application Administrator or Cloud Application Administrator is a possible approved assignment; existing ownership and tenant app-registration policy may allow narrower access.
- If using human groups, permission to create/manage those security groups, their owners, and membership. Groups Administrator is a possible approved assignment.
- Azure resource-group read/deployment permissions plus roleDefinitions/write and roleAssignments/write over the intended assignable scopes. An approved temporary Owner assignment at the resource group is a straightforward bootstrap option, subject to tenant policies and conditions. Contributor alone cannot administer RBAC.
- GitHub repository administrator access, permitted GitHub CLI authentication, Actions enabled, and a private Enterprise repository supporting required environment reviewers.
- At least one production reviewer with repository read access who is different from the person initiating deployment. You may specify a GitHub team by its slug instead.
- The existing pipeline must use target-preview and target-production environment names and vars.AZURE_CLIENT_ID. Full onboarding configures main-only environments; your workflow must support these conventions.

These onboarding privileges belong to the human administrator. Runtime apps receive only the scoped roles below.

Relevant guidance: [Azure custom roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles), [Entra built-in roles](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference), and [GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

## 2. Fill in one configuration file

Copy onboarding.example.json to client-onboarding.json and edit it with your normal editor. JSON uses double quotes, no comments and no trailing commas.

| Setting | Value to supply |
|---|---|
| tenant_id | Client Entra directory GUID |
| subscription_id | Subscription containing the Sentinel workspace |
| resource_group | Existing workspace resource group, exact name |
| workspace_name | Existing Log Analytics workspace name |
| client | Lowercase slug, for example acme |
| workspace_label | primary, secondary, or another lowercase slug |
| github_owner / github_repo | Your existing private organization/repository |
| oidc_subject_format | Choose immutable or legacy to match the actual OIDC subject emitted by your repository |
| app_owner_user_ids | Approved existing Entra user Object IDs in this client tenant |
| production_reviewers | GitHub User login or Team slug; these are different identities from the Azure app owners |
| initial_rule_path | Optional existing rule on main; leave empty for no initial rule |
| allow_missing_mitre | false normally; true only for an approved metadata exception |
| human_groups | Empty list unless you want the optional groups below |

The script discovers the workspace GUID and GitHub organization/repository IDs; do not copy IDs from another client. A guest user must already exist in the client directory, and you must use that directory's user Object ID. Avoid making a short-lived JIT account the only long-term app owner.

With client acme and workspace_label primary, the names are:
- Target: acme-primary
- Manifest: clients/acme/workspace.yml
- Preview app: github-sentinel-acme-primary-preview
- Deployment app: github-sentinel-acme-primary-deploy
- Environments: acme-primary-preview and acme-primary-production
- Branch: codex/onboard-acme-primary

An initial rule is selected with a target-specific enabled: false override. The manifest's enabled field controls the deployment target, not the selected analytics rule. An initial rule makes the target enabled while the rule override remains false. With no initial rule, the generated target is disabled so an empty rules list passes validation. After adding a rule selection, explicitly enable that target.

### Optional human groups

Replace human_groups: [] with an array like the following, substituting real user Object IDs. Roles are intentionally limited to Microsoft Sentinel Reader or Microsoft Sentinel Responder. These groups are for human access and are independent of the two deployment applications.

```json
"human_groups": [
  {
    "name": "sentinel-acme-readers",
    "role": "Microsoft Sentinel Reader",
    "owner_user_ids": ["REPLACE-WITH-OWNER-USER-OBJECT-ID"],
    "member_user_ids": ["REPLACE-WITH-MEMBER-USER-OBJECT-ID"]
  }
]
```

Group ownership and membership are different: list an owner as a member too if the owner needs the group's workspace access. Existing members/owners are never removed. Group assignment is direct, persistent Azure RBAC at the workspace; this does not configure PIM/JIT group activation. Leave the list empty if your access model requires a separate PIM-managed group process.

To adopt an existing group explicitly, add existing_object_id to that group. Its display name and security-group type must match. To adopt existing isolated applications, add existing_preview_app_client_id and existing_deploy_app_client_id at the top level. These are Application (client) IDs. The script refuses to adopt an object just because its name matches.

## 3. Sign in yourself

Open Git Bash in the extracted package folder. Replace both GUID placeholders below.

```bash
az login --tenant "CLIENT-TENANT-GUID"
az account set --subscription "CLIENT-SUBSCRIPTION-GUID"
gh auth login --hostname github.com --web
```

Complete the browser prompts with the authorized accounts. Organization SSO and OAuth restrictions still apply. The script does not collect passwords, store tokens, or grant itself login permission; the CLIs manage your sessions. For GitHub CLI OAuth use the approved account's private-repository access. If your company requires a fine-grained token, its repository permissions must cover Administration and Environments write, Contents and Pull requests write, Actions read, Metadata read, and organization Members read when using teams. Your underlying account still needs repository admin access.

## 4. Run the read-only plan

```bash
bash onboard-all.sh --config client-onboarding.json
```

The script checks the active tenant/subscription, workspace, private repository, Actions, reviewers, users, existing apps/groups, OIDC settings, environment protections, and target-file conflicts. It prints the target, workspace, generated subjects and intended actions.

The default run makes no Azure or GitHub changes. It is an intent/preflight plan, not a full ARM what-if: new service-principal IDs do not exist yet. It cannot prove that every eventual write is permitted. ARM validation and what-if run during apply after identities exist.

Inspect the exact target and workspace before continuing. Stop if they belong to the wrong client.

## 5. Apply onboarding

```bash
bash onboard-all.sh --config client-onboarding.json --apply
```

The sequence is:
1. Create or verify the preview and production environments with a Branch rule of main.
2. Create or reuse each app and service principal, recording IDs as each step completes; add the configured app owners.
3. Create exact environment-specific OIDC trust and AZURE_CLIENT_ID.
4. Validate the Azure permission template, print what-if, and deploy it incrementally.
5. Create or reuse optional groups, add configured owners/members, and assign their approved workspace roles.
6. Create clients/<client>/workspace.yml for the primary label, or clients/<client>/workspace-<workspace_label>.yml for additional labels on codex/onboard-<target>, then open a pull request.

Adding --apply authorizes all these steps in that run. The script does not pause after the ARM what-if.

The deployment writer role has all six rule and query actions:
- Microsoft.SecurityInsights/alertRules/read
- Microsoft.SecurityInsights/alertRules/write
- Microsoft.OperationalInsights/workspaces/read
- Microsoft.OperationalInsights/workspaces/query/read
- Microsoft.OperationalInsights/workspaces/query/*/read
- Microsoft.OperationalInsights/workspaces/analytics/query/action

Preview gets the five read/query actions, without alertRules/write. Both apps also get separate ARM permissions at the resource group: preview can read/validate/what-if; deployment can manage ARM deployments. The apps get no RBAC administration or analytics-rule delete permission. Existing and inherited grants remain additive; review them separately. ARM metadata permissions cover the resource group, so a dedicated resource group provides a tighter boundary.

## 6. Complete verification in the website

1. Open GitHub Settings > Environments > <target>-production. Verify reviewers, Prevent self-review, and main-only deployment.
2. Uncheck Allow administrators to bypass configured protection rules, then save, if your approved policy requires no administrator bypass. The script does not change this UI setting.
3. Verify preview also allows main only. This package intentionally uses main for Azure-connected preview; CI on a working branch remains offline.
4. In Azure, verify that preview and deployment are different apps and that the production credential ends in -production. Credential display names alone do not determine trust.
5. Review the pull request and successful CI; merge it through the normal review process.
6. If initial_rule_path was empty, add a disabled test rule selection and change the target's enabled field to true through another reviewed change. An empty rules list does not import existing analytics rules.
7. Run the existing OIDC inspection, if available. Confirm the actual token subject matches the generated subject exactly. Do not copy the token itself into tickets.
8. Run the selected-workspace workflow on main with the correct target, initial rule path, and preview mode.
9. Review preview results, then run deploy mode and have another authorized reviewer approve production.
10. Check the disabled analytics rule in the correct Sentinel workspace.

The repo metadata check does not mint or verify a GitHub OIDC token. Repositories using immutable subjects include @organization-id and @repository-id. For another repository verify whether immutable or legacy format applies. Custom subject templates beyond standard repo/context stop the script and need a tailored configuration.

The app does not inspect your private scripts/cloud.py implementation. Confirm that its read-only ARM preview/validation uses --validation-level ProviderNoRbac with Azure CLI 2.76.0+. Default provider validation may require resource-write permission. This onboarding package does not rewrite that runtime script or claim that its query-validation behavior was checked.

## 7. Interrupted runs and conflicts

Keep client-onboarding.state.json with its matching configuration in your approved local administration storage. It contains resource IDs and PR details, not credentials. Use the same file on every rerun. You can select a different path with --state.

Rerun the same plan/apply command after fixing an error. Created apps, service principals, credentials, group membership and pull requests are reused. ARM permissions are reapplied incrementally. Azure directory/RBAC propagation or a JIT session expiring can require a later retry.

Existing conflicting reviewers, branch restrictions, app trust, AZURE_CLIENT_ID or target YAML cause a stop; the script does not silently replace them. A matching existing main target is left alone. Do not use this tool to rotate apps or manage an already-edited target.

There is no cross-service transaction or automatic rollback. A failure may leave partial resources. A crash between a remote creation and the local state save may require explicit adoption of that object's ID. If a branch was created just before a crash, review it and its state before retrying. Never remove recorded state to force a clean run.

A .lock file prevents two apply runs using the same state path at once. If a process crashes, remove that specific lock only after confirming no run is active. Do not run concurrent onboarding for the same target with different state files.

Removing a member or group from JSON does not revoke access. Renaming targets does not remove old apps or role assignments. Use a separately reviewed offboarding procedure for removals.

## 8. Add another client/workspace

Create another configuration and state file, change the tenant/subscription/workspace and client slug, sign into that client's approved JIT session, and repeat plan then apply.

For a second workspace under the same client, keep client the same and choose another workspace_label. This creates another target and independent app/environment pair. Rules shared between clients are still selected separately by each target manifest. Onboarding does not push a rule to every client.

## Validation and references

The automated tests simulate Azure/GitHub; no live tenant or private repository was contacted. They cover plan-only behavior, creation, reruns, partial failures, group assignment, tenant isolation, identity/trust conflicts, branch restrictions, target conflicts and CLI write/error handling. Live provider/API permissions and a disabled-rule pilot remain to be validated by an authorized operator.

- [Azure CLI federated credentials](https://learn.microsoft.com/en-us/cli/azure/ad/app/federated-credential)
- [Azure CLI groups](https://learn.microsoft.com/en-us/cli/azure/ad/group)
- [GitHub environment API](https://docs.github.com/en/rest/deployments/environments)
- [GitHub environment variables API](https://docs.github.com/en/rest/actions/variables)
- [GitHub OIDC API](https://docs.github.com/en/rest/actions/oidc)
- [Azure deployment what-if](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-what-if)

Run the offline Python tests with: py -3 tests/test_full_onboarding.py

