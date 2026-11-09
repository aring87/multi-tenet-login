# Sentinel Workspace — desktop guide

Double-click Start-Sentinel.cmd. Python 3.10+ with Tkinter and Azure CLI 2.76+ are required.
GitHub CLI is needed only for the rule library and for submitting rules for approval.

The app has three sections in the sidebar: **Connect**, **Rules** and **Rule builder**
(Ctrl+1, Ctrl+2 and Ctrl+3). Sentinel audit exports and the onboarding/review screens are
not part of the desktop app at present; onboarding remains available from the command line
(`py -m sentinel_app.lighthouse_onboarding --help`). The status bar shows the app version,
**About Sentinel Workspace** at the foot of the sidebar shows the version, the Python and Tk
builds (Tk with its patch level, such as 8.6.15) and the app data folder, and a welcome banner
on Connect points a new analyst at the two things to set up until a client or a rule library
exists. **Help & prerequisites** (or F1) opens this guide inside the app, in the packaged exe
too; **Open license** in About shows the license the same way.

Only one copy of the app runs at a time: starting it again brings the open window forward.

## Connect

1. Choose a saved **Client**, or select **New client** to add one with a name and an
   optional tenant ID or domain. With the list focused, type the first letters of a client's
   name to jump to it. The **⋯** menu saves an edited tenant to the client or removes the
   client (only the local entry, after a red **Remove** confirmation). Switching clients
   clears the selection without asking; Session activity notes the switch.
2. The tenant line shows the client's saved tenant; select **Change** to edit it. Leave it
   blank to discover every directory your account can access. Choose **Browser** or
   **Windows account** as the sign-in method.
3. Select **Sign in to <client>** (**Sign in with Microsoft** when no client is chosen), or
   press Enter in the tenant field, and complete sign-in and MFA. While Microsoft is
   waiting, a panel offers **Bring window forward** and **Cancel sign-in**. If sign-in
   fails, a red banner under the button explains why in plain words; **Copy details**
   copies Microsoft's full message.
4. Select the subscription and Log Analytics workspace. Workspaces refresh when the
   subscription changes. After sign-in the app picks the subscription and workspace you
   used last for that client (remembered on this PC), or else the default subscription and
   its first workspace. **Copy details**, **View analytics rules** (the one blue button
   once you are connected) and, for Azure Government, **Onboard GCC High…** sit right
   under the lists. **+ Show workspace IDs** opens the tenant, subscription, resource group,
   workspace name and workspace ID (the app remembers whether it is open); right-click an
   ID to copy just that one, or use **Copy details** for all of them. **Refresh** beside
   each list asks Azure again (for example after access was just granted), and **Can't see
   a subscription?** runs a read-only check for a subscription ID copied from the portal.

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
labelled, with a plain explanation when the reason is known (for example, which role your
account needs). This view does not list generated alerts or modify rules. Azure public
cloud only. Right-click a value in the details, such as the rule ID, to copy it.

**Rule library** reads clients, rules and assignments from the GitHub repository set on
Connect (**GitHub**) or from a **Folder on this PC**. It needs no Azure sign-in.
**Refresh library** (F5) reads it again; if it can't be read, a red banner in the source
card says why and how to fix it. Select a rule and choose **Edit selected rule**,
or double-click it, to open it in the Rule builder. The Library section offers
**Assign to clients…**, **Preview and deploy…**, **Library issues**, **Open saved
assignment…** and **Preview & deployment history…**; see
[the repository pilot guide](REPOSITORY-PILOT.md). The history lists request and check times
in your local time; point at a time to see it in UTC, as the receipts store it.

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
Closing the app asks before discarding an unsaved draft. Every 30 seconds the builder also
keeps a recovery copy of unsaved work (`desktop-data/drafts/.autosave.rule-draft.json`). If
the app or the PC stops before you save, the next start offers to restore it once; saving the
draft or closing the app normally removes the copy.

When a check fails, the builder opens the step with the problem, outlines the field in red
and explains the fix under it. On opening the Rule builder, the app checks GitHub sign-in: a
pill in the toolbar shows **GitHub: <account>** (the Rule library card on Connect says the
same) or **GitHub: not signed in** with a **Sign in to GitHub** action (**Go to Connect**
when no library is set). The form stays editable while the app reads something in the
background. **My submissions…** lists what you submitted from this
PC and asks GitHub for each one's current state (GET requests only): waiting for review,
changes requested, approved, merged or closed. **View changes** reopens one and **Open in
GitHub** opens its pull request.

## Onboard a GCC High client

GCC High tenants live in Azure Government, which commercial Lighthouse can't reach, so each
one gets its own pair of pipeline app registrations inside the client's tenant.

1. Activate the client's JIT Global Administrator account in CyberQP. In the client's Entra
   admin center (entra.microsoft.us) turn on **Properties > Access management for Azure
   resources** so the account can grant roles.
2. On **Connect**, set the client's tenant to its `.onmicrosoft.us` domain or tenant ID and
   sign in with the JIT account. The app asks Entra which cloud the tenant uses and signs in to
   Azure Government automatically; Session activity says "GCC High tenant detected".
3. Choose the subscription and workspace, then select **Onboard GCC High…** on the Workspace
   card. The button appears only for Azure Government workspaces.
4. Check the client folder name (for example `aosense`) and select **Preview**. The plan lists
   what already exists and what will be created. Nothing changes.
5. Select **Apply reviewed setup**. The app creates `ISI Sentinel Pipeline - Preview` and
   `ISI Sentinel Pipeline - Deploy`, adds one GitHub trust to each (sentinel-preview and
   sentinel-production), assigns Microsoft Sentinel Reader and Log Analytics Reader to preview
   and Microsoft Sentinel Contributor to deploy on the workspace resource group, and opens the
   target pull request with `tenant_id`, `cloud`, `preview_client_id` and `deploy_client_id`
   filled in. **Open pull request** opens it.
6. Turn **Access management for Azure resources** off again. Merge the PR, then run preview
   for the new target on main.

If a step fails, fix the cause and apply again: finished steps are found and skipped. No
secrets are created. The GitHub trust subject uses the organization's immutable-ID format; if
login fails with AADSTS700213, compare it with **Actions > Inspect Azure trust subject**.

## Navigation and storage

Tab navigates controls; Enter/Space activates a focused sidebar item or option, and the
arrow keys move within a view switch. Dialogs close with Esc and run their main action
with Enter. Pages scroll at smaller window sizes, smoothly with a touchpad. The window
always fits the screen's work area at any display scaling (it opens maximised when the
default size doesn't fit), reopens where you left it on whichever monitor that was, and
remembers whether it was maximised. The header names the client and workspace on every
page (point at it for the tenant and subscription IDs), the window title and taskbar name
the client once a workspace is chosen, and the sidebar shows the signed-in Microsoft
account. The status bar reports Ready or Working; tasks that take more than a moment show a
thin activity line under the header and grey the buttons that start work, while search
boxes and the Rule builder's fields stay usable. Lists show the same line in place of their
rows while they load. The status bar keeps to one line: a long message is shortened with an
ellipsis, and pointing at it shows the full text. When a task fails, a dialog explains what
to do in plain words, with the full message under **Copy details** and in Session activity.
Empty lists and text areas say what will appear in them and, where it helps, offer the
action that fills them.

If something unexpected goes wrong inside the app, the status bar says so, Session activity
shows the details, and they are also written to `desktop-data/logs/errors.log` (kept under
1 MB) for whoever supports you.

Client profiles, settings, drafts and submission records stay in desktop-data. Exports are
written to the folder you choose. The window layout, the last workspace per client and
similar view choices are kept in `desktop-data/ui-state.json`.

## Keyboard shortcuts

- **F1** opens this guide.
- **Ctrl+1**, **Ctrl+2** and **Ctrl+3** open Connect, Rules and Rule builder.
- **Ctrl+F** goes to the search box on Rules, or to the client list on Connect.
- **F5** refreshes the current page: subscriptions on Connect, the rules or the library on
  Rules, and the GitHub sign-in check in the Rule builder.
- **Ctrl+N** starts a new rule, and **Ctrl+S** saves the draft in the Rule builder.
- **Ctrl+Page Down** and **Ctrl+Page Up** move to the next and previous Rule builder step.
- In the Query box, **Tab** indents and **Ctrl+Tab** moves on; in other text boxes **Tab**
  moves on.
- In a list, the arrow keys move the selection, **Enter** opens the row's details (in the rule
  library, the rule in the Rule builder) and **Ctrl+C** copies the selected rows.
- In the client list, type the first letters of a name to choose that client.
- In a dialog, **Enter** runs the blue button and **Esc** closes the dialog.
- Right-click a text box for Cut, Copy, Paste and Select all, a list row for its copy actions,
  or an ID in a details panel to copy it.

## Build a Windows app (.exe)

`Build-Exe.cmd` in the repository root builds `dist\Sentinel Workspace.exe` with
PyInstaller. It first runs `scripts\make_build_assets.py`, which draws the app icon
(`assets\sentinel-workspace.ico`) and writes the Windows version resource
(`assets\version-info.txt`) from the same brand module and version number the running app
uses, so the icon in Explorer, the taskbar and the window title bar match and the file's
Details tab shows the product name and version. The .exe keeps its data in a `desktop-data`
folder beside it (or under `%LOCALAPPDATA%\Sentinel Workspace` when that folder is
read-only). The guide, the templates and the LICENSE are bundled inside it, and it is built
without UPX compression, which antivirus tools often flag. Azure CLI and GitHub CLI are
still needed on each PC.

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
error, the app asks **Complete Microsoft authentication?**. Select **Open sign-in** to open a fresh
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
