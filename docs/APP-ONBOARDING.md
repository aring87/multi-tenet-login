# Onboarding a client with the desktop app

The app chooses an onboarding path from the selected workspace tenant:

- **Client tenant:** Azure Lighthouse delegation and a target-file pull request.
- **Managing tenant:** verify existing direct group access and create the target-file
  and dropdown pull request. No Azure resources or permissions are changed.

No per-client app registrations, federated credentials, GitHub environments or
custom role definitions are created. See [same-tenant registration](SAME-TENANT-ONBOARDING.md)
for the direct-access prerequisites. The steps below describe client-tenant Lighthouse onboarding.

---

## Before the first client, once

These are done by hand and never repeated.

1. **Three groups in the managing tenant** — a deploy group, a preview group and
   an engineers group. Record all three Object IDs.
2. **Two app registrations in the managing tenant**, each with one federated
   credential whose subject is
   `repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<environment-name>`.
   One for the preview environment, one for production. Record both
   Application (client) IDs.
3. **Two GitHub environments**, shared by every client. `AZURE_CLIENT_ID` as an
   environment *variable* in each — preview app in the preview environment,
   deploy app in production. Required reviewers on production only.
4. Confirm the pipeline workflow references those two environment names as
   literals, not values derived per client.

The app verifies 3 and 4 on every run and refuses to create them. A missing
environment is a setup error, not something to provision per client.

---

## Before each client

- **JIT / PIM access into the client tenant**, activated, with **Owner** or
  **User Access Administrator** on the target subscription at *subscription*
  scope. Contributor and any Sentinel role are not sufficient — creating a
  delegation is a role assignment in the client's directory.
- If the role was activated in the last few minutes, sign out of any existing
  session first. Role and group membership changes may need time to propagate or a fresh
  sign-in. Use the app to refresh its isolated session. A permission denial can
  also mean the required subscription permissions are missing.
- A **client slug**, lowercase and hyphenated. It becomes the folder name, the
  `client` field and the target prefix, and the pipeline's validator requires
  all three to agree.

---

## Step 1 — Start the app and set the managing-tenant defaults

Double-click **Start-Sentinel.cmd**, or run `python -m sentinel_app`.

Open **Configure** in the left sidebar. In the **Managing tenant (set once)**
card, fill in:

| Field | What it is |
|---|---|
| GitHub organization / repository | the private detection repo |
| Managing tenant ID | your own tenant — the one *receiving* delegated access |
| Deploy group Object ID | group holding the deploy service principal |
| Preview group Object ID | group holding the preview service principal |
| Engineers group Object ID | human read access group |
| Shared preview environment | the one preview environment, e.g. `sentinel-preview` |
| Shared production environment | e.g. `sentinel-production` |
| Offer name | optional; shown to the client under Service providers |
| Delegation location | optional; any region, the delegation is not regional |

Click **Save as defaults**. These persist in `desktop-data/settings.json` and
reload on every start, so this step is done once rather than per client.

Click **Sign in to GitHub** if `gh` is not already authenticated.

## Step 2 — Activate access to the client tenant

Back on **Discover**. Pick your CyberQP region and click **Sign in to CyberQP**
if you use it, activate the client's JIT account there, and return.

Then activate the Azure role if it is PIM-eligible rather than permanently
active. The app cannot do this for you — it is a human approval workflow.

## Step 3 — Sign in and discover

Optionally select or add a saved client in the **Client profile** card. Set the
**Client slug**; the tenant field can stay blank.

Leave **App sign-in method** on Browser unless your organization requires the
Windows broker. Click **Sign in & discover** and complete Microsoft
authentication with the client's authorized account.

The app selects the default accessible subscription and loads its Log Analytics
workspaces. Check the **Subscription** dropdown — it shows the tenant ID, and
selecting a different subscription reloads its workspaces.

## Step 4 — Select the workspace

Pick the client's workspace in **Log Analytics workspace**. The **Workspace
details** box then shows the five values the target file needs, including the
Workspace ID, which is the Log Analytics customer GUID rather than the ARM
resource path.

Two things to verify before continuing, because a wrong selection here
silently points the pipeline at the wrong workspace:

- The subscription ID matches the client you intend to onboard. Workspace and
  resource group names are often generic (`Sentinel-Instance`, `SentinelRG`)
  and collide across tenants; the subscription ID and workspace GUID do not.
- A `DefaultWorkspace-<subscription>-<region>` entry is usually a Defender for
  Cloud artifact with no Sentinel on it. Not the one you want.

Discovery lists workspaces; it does not assert Sentinel is enabled on them. If
in doubt, confirm in the portal that the workspace has a Sentinel instance.

## Step 5 — Configure this client

Open **Configure**. In the **This client** card:

- **Workspace identity label** — `primary` for a client's first workspace. It
  writes `clients/<slug>/workspace.yml`. Any other label writes
  `workspace-<label>.yml` and produces a separate target.
- **Initial rule path** — optional. Use the pipeline connection-test rule for a
  first onboarding; it is committed disabled either way. Leave blank to create
  the target with no rules selected and `enabled: false`.
- **Allow missing MITRE metadata** — tick only if that exception is approved
  for this repo.

## Step 6 — Preview

Open **Review** and click **Preview setup**. Nothing is changed. The plan prints
to the Activity log and reports:

- client tenant, managing tenant, subscription, workspace and workspace GUID
- the roles your signed-in account holds — Owner or User Access Administrator
  must appear
- whether `Microsoft.ManagedServices` is registered
- how many delegation assignments already exist on the subscription
- that both shared environments exist and carry `AZURE_CLIENT_ID`

Read it rather than skimming. Two failures worth recognizing:

**Roles insufficient** — the activation did not complete, has expired, or is
scoped to a resource group rather than the subscription. Reactivate, sign out,
sign in again.

**Shared environment missing** — the once-only setup was not completed, or the
environment name in settings does not match GitHub exactly.

## Step 7 — Apply

Click **Apply reviewed setup** within 15 minutes of the preview; plans expire,
and applying a configuration that differs from the previewed one is refused.

Confirm the destination in the dialog. The app then:

1. registers `Microsoft.ManagedServices` if needed
2. validates and deploys the subscription-scope delegation
3. verifies a registration *assignment* exists, not just a definition
4. creates a branch, commits the target file and opens a pull request

Step 3 matters: the delegation is two resources, and a deployment can report
success with the definition created and nothing actually delegated. If the app
stops there, bind it in the client's portal under **Service providers → Service
provider offers → Delegate subscriptions**, or rerun.

The pull request URL is printed in the Activity log.

## Step 8 — Verify the delegation

In the client's tenant: **Service providers → Service provider offers** should
list your offer with the subscription under Delegations.

In your own tenant: **Azure Lighthouse → My customers → Customers** should list
the client. If it does not appear within a few minutes, sign out and back in —
delegation is cached in the access token.

Note that "My role" on that blade shows *your* effective access, which comes
from the engineers group. It is not what the pipeline identities hold.

## Step 9 — Merge and run the pipeline

The app stops at the pull request by design; deployment goes through the normal
reviewed pipeline.

1. Review the PR. The target file's `tenant_id` is the **managing** tenant —
   that is correct and deliberate, not a mistake to fix.
2. Merge after CI passes.
3. Run the pipeline in **preview** mode for the new target. Green means OIDC,
   the shared credential and the delegation all work.
4. Run it in **deploy** mode and approve at the gate.
5. Confirm the rule landed in the client's workspace under Microsoft Sentinel →
   Analytics.

## Step 10 — Close out

Sign out of the app session. Deactivate the JIT access at your privileged-access
provider — signing out of the app does not do that.

Set `enabled: true` in the target file when you are ready to add that client's
real rules.

---

## What the app does not do

- Create the Sentinel workspace, data connectors or the pipeline
- Create or modify the two shared GitHub environments
- Deploy analytics rules — that stays in the reviewed pipeline
- Activate JIT or PIM access
- Delete anything in Azure or GitHub

## Multiple workspaces for one client

Repeat from step 3 with a different **Workspace identity label**. The
delegation is per subscription, so a second workspace in the same subscription
needs no new delegation — the app will report the existing assignment and
create only the new target file.

## If something fails mid-apply

State lives in `desktop-data/runs/<target>/onboarding.state.json`. Correct the
cause and rerun; the app resumes and does not roll back or delete. A stale
`onboarding.lock` in the same folder blocks reruns — check no run is active
before removing it.


## Retrying a closed onboarding pull request for a numeric client

Closing a pull request does not delete its onboarding branch. Keep the local
onboarding state so the app can recognize the branch it created.

Older versions wrote numeric client identifiers without YAML quotes. For example,
`client: 413` now needs to be `client: "413"`; the generator also quotes
`target: "413-workspace"`. If the only differences are these identifier quotes,
**Preview setup** reports the repair without changing anything. **Apply reviewed
setup** updates the existing branch with the correctly quoted manifest and opens
a replacement PR if no open PR exists. Merge that PR after its checks pass.

The app rechecks the file before updating it and supplies its GitHub blob SHA,
so an intervening edit cannot be silently overwritten. A different tenant,
workspace, subscription, rule selection, or other content still requires manual
review. The error names the branch and file to compare with your selected client
settings. Files already on main and branches not owned by the saved onboarding
state are not automatically repaired. Do not delete your state to bypass a mismatch.

This repair is part of the desktop application; it does not require another
pipeline update or a replacement detection-repository ZIP.


## Enable Azure resource access during first-time sign-in

If you normally enable **Access management for Azure resources** in Microsoft
Entra ID, you can request the same action in the app:

1. Activate your approved Global Administrator access for the client.
2. On **Connect**, enter the client's directory **tenant ID (GUID)**. This option
   requires an explicit ID, even though normal discovery accepts a domain or blank field.
3. Check **Review Azure access management after sign-in**, then select **Sign in & discover**.
4. Complete Microsoft authentication. This optional flow requests Azure Resource Manager
   authentication and can continue with a tenant-level account before subscriptions are visible.
5. Review the signed-in account, user object ID, and client tenant, then confirm.
6. After Azure accepts the change, the app refreshes subscriptions for that tenant and
   continues workspace discovery. If propagation is delayed, refresh again or sign out
   and back in. The app does not repeatedly submit the elevation request.

Already signed in? Expand **Access & discovery tools** and select **Enable Azure access
management**. This action does not require a workspace or subscription selection.

The setting grants the current user **User Access Administrator at root scope `/`**,
covering all subscriptions and management groups in the selected tenant. Azure checks
that the account is an active Global Administrator. This is separate from the app's
subscription-level **Check setup access / Contributor** action.

The checkbox is off by default and applies to one sign-in attempt. It is not saved to
client profiles or replayed after cancellation or MFA recovery. Declining confirmation
continues normal discovery without elevating access. The app checks the tenant and
user again immediately before sending the request. The elevation request keeps its
bearer token in memory; it does not add it to plans, app logs, temporary files, or
subprocess arguments. Azure CLI continues to use the existing isolated authentication cache.

Signing out, closing the app, or ending CyberQP access does not remove the Azure grant.
When finished, set **Microsoft Entra ID > Properties > Access management for Azure
resources** back to **No** using your authorized account. The app does not turn it off
automatically. This feature currently supports Azure public cloud; GCCH/Azure Government
remains outside this flow. No private detection-repository changes are required.

The local identity check requires a readable ARM user token; if its format cannot be
confirmed, the app stops before sending the request. Offline checks exercise fake
responses; actual tenant policy and permission behavior still needs an authorized pilot.

Microsoft references: [Global Administrator access elevation](https://learn.microsoft.com/en-us/azure/role-based-access-control/elevate-access-global-admin)
and the [elevateAccess API](https://learn.microsoft.com/en-us/rest/api/authorization/global-administrator/elevate-access?view=rest-authorization-2015-07-01).
