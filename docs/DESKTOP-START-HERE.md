# Sentinel Workspace — desktop guide

Double-click Start-Sentinel.cmd. Python 3.10+ with Tkinter and Azure CLI 2.76+ are required.
GitHub CLI is needed only for the rule library and for submitting rules for approval.

The app has three sections in the sidebar: **Connect**, **Rules** and **Rule builder**
(Ctrl+1, Ctrl+2 and Ctrl+3). Sentinel audit exports and the onboarding/review screens are
not part of the desktop app at present; onboarding remains available from the command line
(`py -m sentinel_app.lighthouse_onboarding --help`). The sidebar shows the app version, and
a welcome banner on Connect points a new analyst at the two things to set up until a client
or a rule library exists.

## Connect

1. Choose a saved **Client**, or select **New client** to add one with a name and an
   optional tenant ID or domain. The **⋯** menu saves an edited tenant to the client or
   removes the client (only the local entry). Switching clients while connected asks first.
2. The tenant line shows the client's saved tenant; select **Change** to edit it. Leave it
   blank to discover every directory your account can access. Choose **Browser** or
   **Windows account** as the sign-in method.
3. Select **Sign in with Microsoft** (or press Enter in the tenant field) and complete
   sign-in and MFA. While Microsoft is waiting, a panel offers **Bring window forward**
   and **Cancel sign-in**.
4. Select the subscription and Log Analytics workspace. Workspaces refresh when the
   subscription changes. The **Workspace** card lists the tenant, subscription, resource
   group, workspace name and workspace ID; **Copy details** copies them. The lists,
   **Copy details** and **View analytics rules** become available once there is something
   to select. **Refresh** beside each list asks Azure again (for example after access was
   just granted), and **Can't see a subscription?** runs a read-only check for a
   subscription ID copied from the portal.

**Open CyberQP** at the foot of the Sign in card opens the CyberQP portal. Activate JIT
access through your organization's normal process before connecting; opening CyberQP does
not sign the app in. The app uses Azure CLI and never collects your password. **Sign out**
is in the account panel at the bottom of the sidebar.

**Rule library** stores the `organization/repository` of your team's private detection
repository. The Rules page's library view and **Submit for approval** use it. Select
**Save** to keep it, and **Sign in to GitHub** if the card says you are not signed in; the
one-time device code appears in **Session activity**.

Contributor setup, Azure access management and setup-access checks were part of client
onboarding and are no longer in the desktop app.

## Rules

The switch at the top chooses **Workspace rules** or **Rule library**; the choice is
remembered between sessions.

**Workspace rules** reads every configured analytics rule in the selected workspace,
including disabled rules, and summarises totals by state. Search by name, rule ID,
severity or type (Ctrl+F), filter by state, and select a row to see its description,
schedule, MITRE mapping, query and raw configuration. **Refresh** (F5) reads Azure again;
**Export all…** writes a ZIP with raw JSON, CSV and collection details for every loaded
rule. Missing enabled flags are shown as Not specified; incomplete or denied reads are
labelled. This view does not list generated alerts or modify rules. Azure public cloud only.

**Rule library** reads clients, rules and assignments from the GitHub repository set on
Connect (**GitHub**) or from a **Folder on this PC**. It needs no Azure sign-in.
**Refresh library** (F5) reads it again. Select a rule and choose **Edit selected rule**,
or double-click it, to open it in the Rule builder. The Library section offers
**Assign to clients…**, **Preview and deploy…**, **Library issues**, **Open saved
assignment…** and **Preview & deployment history…**; see
[the repository pilot guide](REPOSITORY-PILOT.md).

## Rule builder

Start with **New rule** (Ctrl+N), **Edit existing rule…** (opens the rule library, loaded
and ready to pick from) or **Open…**, which lists your recent drafts and can open a rule
file or a draft file from disk. When a rule file's ID matches a rule in the library, the
file is linked to that rule and submitted as an update.

Work through the five steps — Basics, Query & schedule, MITRE ATT&CK, Advanced and
Review. Run every and Look back are entered as a number and a unit. The pill beside the
title shows whether the draft is new, saved, has unsaved changes, has been checked or has
been submitted. **Save draft** (Ctrl+S) keeps the draft under its rule name in
`desktop-data/drafts` without asking for a file name. **Check rule** checks the repository
schema and shows the file; **Export rule file…** saves it. **Submit for approval** checks
the rule against the library, shows exactly what will change (added lines green, removed
lines red) and the clients that use the rule, asks why the rule is changing, then opens a
pull request that is ready for a teammate's review; your note becomes its description.
The library and file path sit under **Where it will be saved**. New rules start disabled.
Closing the app asks before discarding an unsaved draft.

When a check fails, the builder opens the step with the problem, outlines the field in red
and explains the fix under it. On opening the Rule builder, the app checks GitHub sign-in:
a bar offers **Sign in to GitHub** (or **Go to Connect** when no library is set), and the
footer shows the signed-in account. **My submissions…** lists what you submitted from this
PC and asks GitHub for each one's current state (GET requests only): waiting for review,
changes requested, approved, merged or closed. **View changes** reopens one and **Open in
GitHub** opens its pull request.

## Navigation and storage

Tab navigates controls; Enter/Space activates a focused sidebar item or option, and the
arrow keys move within a view switch. Dialogs close with Esc and run their main action
with Enter. Pages scroll at smaller window sizes; the window size is remembered. The
header shows the selected workspace and tenant, and the sidebar shows the signed-in
account. The status bar reports Ready or Working; a thin progress line appears under the
header while work runs. The status bar shows a compact one-line message so a long
diagnostic cannot push content offscreen; full errors remain in their dialogs and Session
activity.

Client profiles, settings, drafts and submission records stay in desktop-data. Exports are
written to the folder you choose.

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
forward. If the window remains hidden, click **Bring window forward** in the
waiting panel below Sign in with Microsoft. It is available while authentication is running.
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

After signing in, verify the account, subscription and workspace, then start the
operation again. The failed operation is never replayed automatically. Earlier steps
in a failed operation may already have completed.
The app captures Azure warnings because CLI can emit its claims-challenge command
as a warning. A scoped sign-in and a subsequent sign-in carrying the resource's
actual challenge are separate, bounded recovery attempts. Repeated challenge
payloads cannot cause an endless prompt loop. A Windows sign-in component failure
offers browser recovery; the sign-in method changes only if you accept.

If authentication still fails, update Azure CLI (2.76.0 or later is required),
restart the app and check the client's Entra sign-in logs using the correlation ID
and UTC timestamp shown in the error. Inspect Authentication Details and Conditional
Access results for Azure CLI / Windows Azure Service Management API. A successful
preview does not prove an actual Lighthouse deployment will satisfy MFA. Your
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

## Account and tenant errors

For `AADSTS50020`, `AADSTS50034`, or `AADSTS51004`, verify the saved tenant and the
current CyberQP account. Select **Use another account** in Microsoft's window if
it selected the wrong account. These are account/tenant failures, not RBAC
propagation. The app preserves Microsoft's error code for troubleshooting and
cannot create or reactivate an account missing from that tenant.
