# Sentinel Workspace — desktop guide

Open **Sentinel Workspace.exe** (from a copy of the repository, Start-Sentinel.cmd). Azure CLI
2.76+ must be installed on the PC. GitHub CLI is needed only for the rule library and for
submitting rules for approval.

The app has three sections in the sidebar: **Connect**, **Rules** and **Rule builder**
(Ctrl+1, Ctrl+2 and Ctrl+3). The status bar shows the app version,
**About Sentinel Workspace** at the foot of the sidebar shows the version, the Python and Tk
builds (Tk with its patch level, such as 8.6.15) and the app data folder, and a welcome banner
on Connect points a new analyst at the two things to set up until a client or a rule library
exists. **Help & prerequisites** (or F1) opens this guide inside the app, in the packaged exe
too; **Open license** in About shows the license the same way. If something isn't working,
start with **Check this PC**, also at the foot of the sidebar, and send **Copy support info**
to whoever supports you (see Check this PC and support info, below).

Only one copy of the app runs at a time: starting it again brings the open window forward. If
the open copy was started with **Run as administrator**, the app says so instead; switch to it
from the taskbar.

## Connect

1. Choose a saved **Client**, or select **New client** to add one with a name and an
   optional tenant ID or domain. With the list focused, type the first letters of a client's
   name to jump to it. The **⋯** menu saves an edited tenant to the client or removes the
   client (only the local entry, after a red **Remove** confirmation). Switching clients
   clears the selection without asking; Session activity notes the switch. Switching back
   to the client you signed in for lists its subscriptions again and returns to its last
   workspace, without another sign-in.
2. The tenant line shows the client's saved tenant; select **Change** to edit it. Leave it
   blank to discover every directory your account can access. Choose **Browser** or
   **Windows account** as the sign-in method.
3. Select **Sign in to <client>** (**Sign in with Microsoft** when no client is chosen), or
   press Enter in the tenant field, and complete sign-in and MFA. While Microsoft is
   waiting, a panel says where to finish (your browser, or the Windows account window for
   **Windows account**) and offers **Bring window forward** and **Cancel sign-in**. If
   sign-in fails, a red banner under the button explains why in plain words; **Copy
   details** copies Microsoft's full message.
4. Select the subscription and Log Analytics workspace. Each subscription shows its name, its
   state when it isn't Enabled, and a shortened ID. Workspaces refresh when the
   subscription changes. After sign-in the app picks the subscription and workspace you
   used last for that client (remembered on this PC), or else the default subscription and
   its first workspace. **Copy details** and **View analytics rules** (the one blue button
   once you are connected) sit right under the lists. **+ Show workspace IDs** opens the tenant, subscription, resource group,
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

## Rules

The switch at the top chooses **Workspace rules**, **Rule library** or **Drift**; the
choice is remembered between sessions.

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
or double-click it, to open it in the Rule builder. **Assign to clients…** and **Preview
and deploy…** sit above the list; **Library issues**, **Open saved assignment…** and
**Preview & deployment history…** are in the **Library ⋯** menu on the Rule library card.
In the client lists of those dialogs, Space ticks or clears a client. The history lists
request and check times in your local time; point at a time to see it in UTC, as the
receipts store it.

**Drift** compares the two for the connected client, using only what the other two views
have read: it starts no Azure or GitHub read of its own. It finds the client target in the
library that deploys to the selected workspace — by workspace ID, or else by subscription,
resource group and workspace name, or else by tenant and workspace name (a tenant alone
never matches, because Lighthouse targets record the managing tenant) — and matches rules
by rule ID. It lists three kinds of drift: **Missing** (assigned to the target but not in
the workspace), **Different** (the workspace's enabled state or severity differs from the
library's, after the target's overrides) and **Only in workspace** (live rules that are not
in the library, or are in it but not assigned to this target). Other settings, such as the
schedule, don't count as drift. Search and filter as in the other views, and select a rule to
see the library's and the workspace's settings side by side (differences in red), its rule
ID, file, target and overrides, and both full configurations. A banner warns when the target
is switched off in the library, when not every workspace rule could be read, or when an
assigned rule file is missing from the library; the list says so when no target, or more
than one, deploys to the workspace. **Refresh** (F5) reads the workspace rules again and
**Refresh library** reads the library again. Opening Drift reads the saved GitHub library
once if none is loaded, as Rule library does. Right-click a rule to copy its name or ID, or
to open it in Workspace rules or Rule library.

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
keeps a recovery copy of unsaved work (`desktop-data/drafts/.autosave.rule-recovery.json`),
and again when Windows signs out or restarts with the app open. If the app or the PC stops
before you save, the next start offers to restore it: **Restore** opens it as unsaved work,
**Discard** deletes it, and Esc (or closing the question) keeps it for the next start unless
you start new work first. Saving the draft or closing the app normally removes the copy.

When a check fails, the builder opens the step with the problem, outlines the field in red
and explains the fix under it. On opening the Rule builder, the app checks GitHub sign-in: a
pill in the toolbar shows **GitHub: <account>** (the Rule library card on Connect says the
same), **GitHub: not signed in** with a **Sign in to GitHub** action, or **Rule library not
set** with **Go to Connect** when no library is set. The form stays editable while the app
reads something in the
background. **My submissions…** lists what you submitted from this
PC and asks GitHub for each one's current state (GET requests only): waiting for review,
changes requested, approved, merged or closed. **View changes** reopens one and **Open in
GitHub** opens its pull request.

## Onboard a GCC High client

GCC High tenants live in Azure Government, which commercial Lighthouse can't reach, so each
one gets its own pair of pipeline app registrations inside the client's tenant.

This is held back until policy allows it: **Onboard GCC High…** and this section appear only
when `desktop-data/settings.json` has `"gcc_high_onboarding": "on"`. Signing in to a GCC High
tenant works either way.

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

## Check this PC and support info

**Check this PC** at the foot of the sidebar checks what the app needs, without changing
anything on the PC or in any tenant:

- **Azure CLI**: installed, and version 2.76 or later. Azure CLI is asked for its version with
  an empty, temporary settings folder, so your own Azure CLI sign-ins are not touched.
- **GitHub CLI**: installed, and its version. It is needed only for the rule library and for
  submitting rules for approval.
- **GitHub sign-in**: whether GitHub CLI has a signed-in github.com account (GitHub CLI asks
  GitHub to confirm it).
- **Data folder**: the app can save in its data folder (it writes and removes a tiny test file).
- **App version**: the version you are running and, when an update folder is set, whether a
  newer one is waiting there.

Each line says **OK** or **Needs action**, with what to do in plain words, for example asking
your administrator to update Azure CLI. After a tool is installed or updated, close and reopen
the app, then select **Check again**. **Sign in to GitHub**, **Open folder** and **Set update
folder…** appear on the lines they help with.

**Copy support info** (in the sidebar, or at the foot of Check this PC) copies what a support
ticket needs: the app version, the Windows, Python and Tk versions, the data folder, the result
of each check (Azure CLI and GitHub CLI versions included) and the last 100 lines of
`desktop-data/logs/errors.log`. It never includes client names, tenant or subscription IDs,
accounts, tokens or rules: from the error log it keeps each error's type, where in the app it
happened and the first line of its message, with IDs, accounts, quoted names and tokens
removed. A window shows exactly what was copied; paste it into the ticket.

### Update notice

Your team can keep new versions of the app in one folder, such as a team share. Select **Set
update folder…** on the App version line of Check this PC and enter the folder, for example
`\\fileserver\SOC\Sentinel Workspace` (it is kept in `desktop-data/settings.json` as
`update_folder`). Each time the app starts it reads `latest.json` in that folder and, when it
names a newer version than yours, shows a quiet notice at the top of Connect with the folder
and **Open folder**. Get the new version from there, then close the old copy and start the new
one. The app never downloads or installs anything and makes no web requests for this; if the
folder can't be read (off the VPN, say), Session activity says so and nothing else happens.

Whoever publishes a new version copies it to the folder and updates `latest.json`:

```
{"version": "2.2.0", "notes": "Check this PC and Copy support info in the sidebar."}
```

`notes` is optional; up to 240 characters of it are shown after the notice. Clear the folder
in Check this PC to stop checking.

## Navigation and storage

Tab moves through each window in reading order, top to bottom and left to right; read-only
boxes such as Session activity and this guide are Tab stops too, so the arrow and Page keys
scroll them. Enter/Space activates a focused sidebar item or option, and the arrow keys move
within a view switch. Dialogs close with Esc; Enter presses the blue button, except that it
never starts a deployment or a setup that changes a client tenant. Pages scroll at smaller window sizes,
smoothly with a touchpad. The window always fits the screen's work area at any display
scaling (it opens maximised when the default size doesn't fit), reopens where you left it on
whichever monitor that was, and remembers whether it was maximised, even when it is closed
while minimised or Windows signs out. Dialogs open over the window, on its monitor. The header
names the client and workspace on every page (point at it for the tenant and subscription
IDs), the window title and taskbar name the client once a workspace is chosen, and the
sidebar shows the signed-in Microsoft account. The status bar reports Ready or Working; tasks that take more than a moment show a
thin activity line under the header and grey the buttons that start work, while search
boxes and the Rule builder's fields stay usable. Lists show the same line in place of their
rows while they load. The status bar keeps to one line: a long message is shortened with an
ellipsis, and pointing at it shows the full text. When a task fails, a dialog explains what
to do in plain words, with the full message under **Copy details** and in Session activity.
Empty lists and text areas say what will appear in them and, where it helps, offer the
action that fills them.

If something unexpected goes wrong inside the app, the status bar says so, Session activity
shows the details, and they are also written to `desktop-data/logs/errors.log` (kept under
1 MB) for whoever supports you; **Copy support info** includes its last 100 lines, without
client details. If the app can't start at all, a message says so and the
details go to the same log (or, when that folder can't be written, to `Sentinel Workspace
errors.log` in your temp folder).

Client profiles, settings, drafts and submission records stay in desktop-data. Exports are
written to the folder you choose. The window layout, the last workspace per client and
similar view choices are kept in `desktop-data/ui-state.json`. The exe keeps desktop-data
beside the .exe, or under `%LOCALAPPDATA%\Sentinel Workspace\desktop-data` when that
folder can't be written (for example under Program Files).

## Keyboard shortcuts

- **F1** opens this guide.
- **Ctrl+1**, **Ctrl+2** and **Ctrl+3** open Connect, Rules and Rule builder.
- **Ctrl+F** goes to the search box on Rules, or to the client list on Connect.
- **F5** refreshes the current page: subscriptions on Connect, the rules or the library on
  Rules (the workspace rules on Drift), and the GitHub sign-in check in the Rule builder.
- **Ctrl+N** starts a new rule, and **Ctrl+S** saves the draft in the Rule builder.
- **Ctrl+Page Down** and **Ctrl+Page Up** move to the next and previous Rule builder step.
- In the Query box, **Tab** indents and **Ctrl+Tab** moves on; in other text boxes **Tab**
  moves on.
- In the Rules page lists, the arrow keys move the selection and **Enter** opens the selected
  rule (Workspace rules and Drift: moves into its details; Rule library: opens it in the Rule
  builder);
  **Ctrl+C** copies the selected rows.
- In a search box, **Enter** or **Down** moves to the first result and **Esc** clears the search.
- In the client list on Connect, type the first letters of a name to choose that client.
- In a dialog, **Enter** presses the blue button (a focused button presses itself) and **Esc**
  closes it. Dialogs that deploy or change a client tenant never start anything from Enter, and
  Enter in a dialog's client list or search box presses nothing.
- Right-click a text box for Cut, Copy, Paste and Select all, a list row for its copy actions,
  or an ID in a details panel to copy it.

## Finding the Microsoft sign-in window

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
Access results for Azure CLI / Windows Azure Service Management API. Your
administrator may need to enforce MFA at sign-in or resolve the account's MFA or
cross-tenant policy requirements; do not disable those requirements to complete setup.

An empty subscription list does not prove the account has no Azure permissions.
The app retains CLI sign-in warnings, including failures for particular tenants.
Enter the client tenant ID and use the same authorized identity as in Azure portal.
When CLI sign-in itself succeeds but returns no subscriptions, **Can't see a
subscription?** remains available using the subscription ID from the portal.
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
