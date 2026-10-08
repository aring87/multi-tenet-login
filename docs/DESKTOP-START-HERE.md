# Sentinel Workspace — desktop guide

Double-click Start-Sentinel.cmd. Python 3.10+ with Tkinter and Azure CLI 2.76+
are required. GitHub CLI is needed for onboarding and GitHub repository actions.

## Workspaces

1. In **Choose a client**, select a saved client on the left, or use **Add client**.
   **Open CyberQP** stays below **Manage saved profile** for temporary-access activation.
2. In **Connect to Azure** on the right, confirm the tenant and select **Sign in &
   discover**. Browser sign-in is the default; **Sign-in options** also offers the
   Windows account window. The tenant can be blank for directory discovery.
3. Complete Microsoft sign-in and MFA. **Show Microsoft sign-in** and **Cancel
   sign-in** appear only while sign-in is running.
4. In **Choose a workspace**, select the subscription and Log Analytics workspace.
   Then choose **View analytics rules**, **Export audit evidence**, or **Onboard
   workspace**. These actions become available after a workspace is selected.

**Manage saved profile** contains the client slug, Save profile changes, Import
configuration and Delete saved client. Deleting removes only the local profile.
**Workspace identifiers** expands the full account, tenant and resource details
with a copy button.

The **Access & support** card contains three expandable sections:

- **First-time access setup**: optional access-management review after sign-in,
  Azure access-management elevation and reviewed Contributor setup.
- **Connection troubleshooting**: access/session refresh, subscription/workspace
  refresh, missing-subscription checks, Azure portal, Sign out and CyberQP region.
- **Saved access activity**: persistent setup timestamps and last recorded results.

Activate JIT access through your organization's normal process before connecting.
Browser shortcuts do not authenticate the app. The app never collects your password.
Sidebar sections group connection, analyst tasks, and engineer/auditor tasks; they
are navigation groups, not permission roles or access enforcement.

## Onboarding

Set the workspace identity label and optional initial rule. Managing tenant
defaults are grouped into Repository, Delegated access, and Shared environments.
Save defaults reuses these local settings for future clients. Optional delegation
details contains the offer name and deployment location.

Onboarding prepares an Azure Lighthouse delegation and a target-file pull request.
Use groups from the managing tenant and existing GitHub environments. Review the
current onboarding documentation and authorization requirements before applying.

## Review onboarding

Run Preview setup. Inspect the destination summary and Operation activity before
choosing Apply reviewed setup. Apply requires a successful matching preview less
than 15 minutes old; changed or expired configurations require another preview.
The destination confirmation and existing deployment checks remain in place.

The footer shows READY or WORKING and the current status. The progress bar appears
only while work is running. Sign out ends this app's Azure session.

## Sentinel audit

Current configuration is selected by default. Add logs expands optional Sentinel
activity, UTC dates, and security source-table selection. The selection summary
stays visible when the section is collapsed; collapsing does not clear selections.

Choose source tables, preview counts, and select a recent sample or bounded period
export. Export evidence produces the configuration report, raw JSON/CSV and a
manifest. Open report or Open export folder from the results card.

See [Sentinel Configuration & Logs](AUDIT-EVIDENCE.md) for scope, limits, and live
validation. Configuration snapshots do not reproduce every portal screen.

## Navigation and storage

The sidebar opens each screen independently. Tab navigates controls; Enter/Space
activates a focused sidebar item. Pages scroll at smaller window sizes. The
header identifies the selected workspace and tenant across screens.

Client profiles and defaults stay in desktop-data. Exports are written to your
chosen folder under sentinel-exports / tenant / workspace / collection. Keep
client evidence in your approved storage location.


### Analytics rules and setup access

Select a client subscription and workspace. **Analytics rules** automatically reads its configured Sentinel rules, including enabled and disabled rules. Search by name/type/severity/ID, filter by state, and select a row to inspect raw configuration. Refresh rules reads Azure again. Missing enabled flags are shown as Not specified; incomplete or denied reads are explicitly labeled. This page does not list generated security alerts or modify rules. Azure public cloud only.

Under **Workspaces > Access & support > First-time access setup > Check setup access / Contributor**, the app checks the signed-in user's subscription permissions. If provider registration is already permitted, no role is added. Otherwise, an account with role-assignment rights can review and create an active Contributor assignment for itself on that selected client subscription. Azure enforces conditions and policies. Contributor does not grant permission to assign roles or replace Lighthouse onboarding permissions.

CyberQP activation remains in CyberQP. The Contributor assignment is persistent, not a CyberQP-managed timed activation; remove it through Azure IAM when no longer needed. Allow Azure permission propagation before retrying Preview setup. The app does not grant access automatically during sign-in.


### Client slug and workspace label length

Onboarding uses `<client-slug>-<workspace-label>` as its target. The combined
value, including the separating hyphen, supports up to 64 characters. Each
component allows up to 62 characters, leaving room for a hyphen and a one-character
other component. Names use lowercase letters, digits and single hyphens, starting
and ending with a letter or digit.

Azure deployment names also allow 64 characters. Existing short deployment names
are preserved. When `lighthouse-onboard-` plus the target exceeds that limit, the
app uses a readable shortened target plus a stable hash suffix for the Azure
deployment name only. The complete target stays in repository manifests, client
paths and local state. For example, the 49-character target
`explosive-countermeasures-international-workspace` is now accepted unchanged.
This does not verify any independent naming restrictions in your detection pipeline.


### Finding the Microsoft sign-in window

On Windows, the app attempts to bring newly opened Microsoft sign-in windows
forward. If the window remains hidden, click **Bring sign-in window forward**
below Sign in & discover. That button is available while authentication is running.
Windows focus restrictions may still require Alt+Tab. Automatic detection covers
Microsoft broker hosts and recognized English Microsoft sign-in titles in Edge,
Chrome, Firefox and Brave; it does not pin your browser permanently above other
applications. No sign-in credentials or page contents are read by this helper.

## Microsoft asks for MFA after sign-in

If Azure returns AADSTS50076, InteractionRequired, or a recognized revoked-session
error, the app offers **Complete Microsoft authentication**. Accept to open a fresh
app session for the affected client, using the selected browser/account-window
method and the appropriate Azure management or Microsoft Graph scope. If Azure CLI
provides a supported claims challenge, it is passed through without logging its payload.
Microsoft chooses the required authentication method; the app cannot force a code
entry screen or bypass tenant policies.

After signing in, verify the account, subscription and workspace. Run Preview again
before Apply. The failed operation is never replayed automatically, and any previous
preview is invalidated. Earlier steps in a failed Apply may already have completed.
The app captures Azure warnings because CLI can emit its claims-challenge command
as a warning. A scoped sign-in and a subsequent sign-in carrying the resource's
actual challenge are separate, bounded recovery attempts. Repeated challenge
payloads cannot cause an endless prompt loop. A Windows sign-in component failure
offers browser recovery; the sign-in method changes only if you accept.

If authentication still fails, update Azure CLI (2.76.0 or later is required),
restart the app and check the client's Entra sign-in logs using the correlation ID
and UTC timestamp shown in the error. Inspect Authentication Details and Conditional
Access results for Azure CLI / Windows Azure Service Management API. A successful
preview does not prove the actual Lighthouse deployment will satisfy MFA. Your
administrator may need to enforce MFA at sign-in or resolve the account's MFA or
cross-tenant policy requirements; do not disable those requirements to complete setup.

An empty subscription list does not prove the account has no Azure permissions.
The app retains CLI sign-in warnings, including failures for particular tenants.
Enter the client tenant ID and use the same authorized identity as in Azure portal.
When CLI sign-in itself succeeds but returns no subscriptions, **Check missing
subscription** remains available using the subscription ID from the portal.
A terminal az login uses a separate cache and does not refresh this app's session.

References: [Azure CLI interactive sign-in](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-interactively),
[login scopes and claims challenges](https://learn.microsoft.com/en-us/cli/azure/reference-index#az-login).

MFA challenge troubleshooting: [Microsoft guidance](https://learn.microsoft.com/en-us/cli/azure/use-azure-cli-successfully-troubleshooting#troubleshooting-multifactor-authentication-mfa).

## Refreshing setup access and tracking Contributor setup

Under **Workspaces > Access & support > Connection troubleshooting**, use **Refresh setup access** to
read current subscription permissions and validate an empty subscription template.
It keeps the selected workspace and changes no roles or resources. It checks
provider registration and deployment actions; **Preview setup** still performs
the complete onboarding check. **Refresh subscriptions** only reloads discovery.

The selected subscription's access activity shows first/latest Contributor setup
clicks, Azure's assignment acceptance time, and the latest access-check time and
account. Times include the local timezone and persist in ignored
`desktop-data/access-history.json`. A click is not proof a role was granted;
timestamps begin with this version and cannot reconstruct earlier attempts.
Saved results are historical, not a guarantee that another session has access.

After a recent accepted assignment and a failed check, the app says propagation
is possible, not confirmed. It does not assume every authorization denial is
propagation or automatically retry grants. If access is still denied, check the
account, subscription scope, and active CyberQP access.

**Sign in again / refresh session** starts a fresh app session for the selected
client and explicitly requests Azure management authentication. It avoids a
separate Sign out action, but Microsoft may still require interactive sign-in or
MFA. It clears the previous onboarding preview and never replays a deployment.
It does not clear your browser's Microsoft cookies or Windows accounts.

For `AADSTS50020`, `AADSTS50034`, or `AADSTS51004`, verify the saved tenant and the
current CyberQP account. Select **Use another account** in Microsoft's window if
it selected the wrong account. These are account/tenant failures, not RBAC
propagation. The app preserves Microsoft's error code for troubleshooting and
cannot create or reactivate an account missing from that tenant.

The footer shows a compact status so a long diagnostic cannot push the workspace
offscreen. Full operation errors remain in their dialogs and Operation activity.
Repository catalog actions are grouped into selected-rule actions and saved activity.
