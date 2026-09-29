# Sentinel Workspace — desktop guide

Double-click Start-Sentinel.cmd. Python 3.10+ with Tkinter and Azure CLI 2.76+
are required. GitHub CLI is needed only for client onboarding.

## Workspaces

1. Enter an optional tenant ID/domain, or leave it blank to discover available
   directories. Choose Browser or Windows account window for authentication.
2. Select Sign in & discover and complete Microsoft sign-in and MFA.
3. Select the subscription and Log Analytics workspace. Workspaces refresh when
   the subscription changes. Review the Selected workspace details.
4. Copy details, open Sentinel audit, or continue to client onboarding.

Client profiles are optional. Add a client, enter its slug, and save the profile
for future visits. The tenant field belongs to the selected profile; clear it
when connecting to a different client. Manage saved profile contains Delete saved
client, which removes only the local profile.

Access & discovery tools contains CyberQP, Azure portal, refresh actions, and the
missing-subscription diagnostic. Activate JIT access through your organization's
normal process before connecting. Browser shortcuts do not authenticate the app.
The app uses Azure CLI and never collects your password.

## Onboarding

Set the workspace identity label and optional initial rule. Managing tenant
defaults are grouped into Repository, Delegated access, and Shared environments.
Save defaults reuses these local settings for future clients. Optional delegation
details contains the offer name and deployment location.

Onboarding prepares an Azure Lighthouse delegation and a target-file pull request.
Use groups from the managing tenant and existing GitHub environments. Review the
current onboarding documentation and authorization requirements before applying.

## Review & apply

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

Under **Workspaces > Access & discovery tools > Check setup access / Contributor**, the app checks the signed-in user's subscription permissions. If provider registration is already permitted, no role is added. Otherwise, an account with role-assignment rights can review and create an active Contributor assignment for itself on that selected client subscription. Azure enforces conditions and policies. Contributor does not grant permission to assign roles or replace Lighthouse onboarding permissions.

CyberQP activation remains in CyberQP. The Contributor assignment is persistent, not a CyberQP-managed timed activation; remove it through Azure IAM when no longer needed. Allow Azure permission propagation before retrying Preview setup. The app does not grant access automatically during sign-in.
