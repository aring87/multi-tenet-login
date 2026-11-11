# Sentinel Workspace

A Windows desktop app for SOC analysts who work in client Microsoft Sentinel workspaces. It
signs in to a client tenant, finds its Log Analytics workspaces, reads the live analytics
rules, browses the team's detection library on GitHub, and builds new or changed rules that go
out only through a teammate's review. Current version: **2.3.0**.

The app never deploys a rule directly: every change is a pull request in the private detection
repository, and the repository's pipeline deploys it after review. It never asks for or stores
passwords; Microsoft and GitHub CLI handle sign-in.

## For analysts: install the app

Use the packaged app; you do not need Python, Git or this repository.

1. Get **Sentinel Workspace 2.3.0.zip** from the team share and extract it somewhere you can
   write, for example `Documents\Sentinel Workspace` (not Program Files). Keep the
   `desktop-data` folder next to the exe: it holds the client list and the rule library
   setting. Upgrading? Replace only `Sentinel Workspace.exe` and keep your `desktop-data`.
2. Install Azure CLI (2.76 or newer) and GitHub CLI once, then reopen Terminal:

   ```text
   winget install -e --id Microsoft.AzureCLI
   winget install -e --id GitHub.cli
   ```

3. Double-click **Sentinel Workspace.exe**. If Windows shows *Windows protected your PC*,
   select **More info**, then **Run anyway**.

Inside the app, **F1** opens the full guide ([docs/DESKTOP-START-HERE.md](docs/DESKTOP-START-HERE.md)),
and **Check this PC** at the foot of the sidebar checks Azure CLI, GitHub CLI, GitHub sign-in
and the data folder. The step-by-step team procedure is the SOP that comes in the zip.

## What it does

- **Connect:** saved clients with their tenant ID or domain; Microsoft sign-in in the browser
  or with the Windows account window, including JIT accounts; subscriptions and Log Analytics
  workspaces discovered automatically, with the last workspace remembered per client. The
  header shows the client and workspace on every page. Azure Government (GCC High) tenants
  are detected and signed in to automatically.
- **Rules > Workspace rules:** every analytics rule in the selected workspace, enabled or
  disabled, with search, state filters, sorting, details beside the list and a ZIP export.
- **Rules > Rule library:** clients, rules and assignments in the team's detection repository,
  with assignment, preview and deployment requests that go through review.
- **Rules > Drift:** what the library says the connected client should have against what is
  live in its workspace: missing rules, different enabled state or severity, and live rules
  the library doesn't assign.
- **Rule builder:** five guided steps (Back/Next), a KQL check as you type (unclosed quotes
  and brackets, misspelt steps and tables, with the line and column), **Run test query**
  against the connected workspace (read-only, row-capped), a schema check that flags every
  problem at once, and
  **Submit for approval**, which opens a ready-for-review pull request. **My submissions**
  follows each one to merge. Unsaved work is autosaved and offered back after a crash.
- **Support:** **Check this PC**, **Copy support info** (versions, checks and recent errors,
  without client data) and an optional notice when a newer version is in a shared folder.
  Unexpected errors are written to `desktop-data/logs/errors.log`.
- Plain-language error messages with **Copy details**, keyboard shortcuts (see the guide),
  one running copy at a time, and a window that fits any screen and display scaling.

Not in the desktop app: Sentinel audit evidence export and commercial client onboarding
(Lighthouse onboarding is a command-line tool, below). **Onboard GCC High…** is held back
until policy allows it: it appears only when `desktop-data/settings.json` contains
`"gcc_high_onboarding": "on"`.

## Connect to a client workspace

1. Use **Open CyberQP** if your process needs it and activate the client's JIT account there.
   Opening CyberQP does not sign the app in.
2. Choose the client (or **+ New client…**). Its saved tenant shows beneath; select **Change**
   to edit it, or leave it blank to discover every directory your account can reach.
3. Select **Sign in to <client>** and complete sign-in and MFA with the client-authorized
   account (choose **Use another account** if the wrong one is suggested). If sign-in fails,
   the Sign in card explains why, with **Copy details** for the full message.
4. Check the **Subscription** and **Log Analytics workspace**. **+ Show workspace IDs** shows
   the tenant ID, subscription ID, resource group, workspace name and workspace ID (the Log
   Analytics customer GUID); right-click one to copy it, or use **Copy details** for all.
   Nothing is deployed or assigned during discovery.

**Cancel sign-in** stays available while Microsoft is waiting: the browser does not tell Azure
CLI when its tab is closed, so the app stops its own waiting process. A sign-in times out after
five minutes. Signing in to the Azure portal in your browser does not sign this app in.

Only resources the signed-in account can read are listed, and a listed workspace is not proof
that Sentinel is enabled on it. If a subscription is missing, select **↻ Refresh** beside the
list, or **Can't see a subscription?** and paste its ID from the Azure portal for a read-only
report (account, tenant, cloud, subscription state and the Azure error). It grants nothing; share
its results only with authorized people.

## Run from source

For maintainers and developers:

1. Install Python 3.12 (3.10 or newer is the minimum; Tkinter is included with the
   python.org installer), Azure CLI 2.76 or newer and GitHub CLI.
2. Clone the repository (`git clone https://github.com/aring87/sentinel-workspace.git`), then
   in its folder run `py -m pip install -r requirements.txt` (PyYAML and ruamel.yaml).
3. Double-click **Start-Sentinel.cmd**, or run `py -m sentinel_app`.

Run from source, the app keeps its data in `desktop-data/` in the repository and starts with
an empty client list and blank GitHub settings.

## Build the exe and the analyst package

`Build-Exe.cmd` installs PyInstaller and `requirements.txt`, then builds
`dist\Sentinel Workspace.exe`. It first runs `scripts\make_build_assets.py`, which draws the
icon (`assets\sentinel-workspace.ico`) and writes the Windows version resource
(`assets\version-info.txt`) from the app's own brand module and version, so Explorer, the
taskbar and the file's Details tab match the running app. The guide, the templates and the
LICENSE are bundled inside the exe, and it is built without UPX compression, which antivirus
tools often flag. Build from a clean checkout of a commit, so uncommitted work is not bundled.

The exe keeps its data in a `desktop-data` folder beside it, or under
`%LOCALAPPDATA%\Sentinel Workspace\desktop-data` when that folder can't be written.

To make the analyst package, put these in a folder named `Sentinel Workspace` and zip it:

- the new `Sentinel Workspace.exe`, `READ ME FIRST.txt` and the SOP as a PDF;
- `desktop-data\clients.json`, copied from your own desktop-data folder;
- `desktop-data\settings.json` holding only the rule library:
  `{"github_owner": "isi-security", "github_repo": "detection-as-code"}`;
- optionally, your organization's logo for the foot of the sidebar, made from one large PNG
  (use the version for dark backgrounds, usually white):
  `py scripts\make_logo_sizes.py <logo.png> "Sentinel Workspace\desktop-data\branding"`.
  The app shows the size that matches each display's scaling; without the folder it shows no logo.

Never include the rest of your `desktop-data` folder: `sessions` holds your Azure sign-in
sessions, `review-requests` holds private rule content, and `access-history.json` and `runs`
are your own records. `clients.json` has client names and tenant IDs (no credentials); share it
only through approved channels.

To have analysts' apps announce a new version, keep the package in one folder on the team share
with a `latest.json` beside it, for example
`{"version": "2.3.0", "notes": "KQL check as you type in the Rule builder."}`. Analysts set
that folder once in **Check this PC** > **Set update folder…**. The app only reads the file; it
never downloads or installs anything.

## Command-line onboarding

Onboarding is not part of the desktop app. Two setup modes are available from the command line:

| Mode | Purpose |
|---|---|
| Same-tenant registration | Verify existing group roles and pipeline membership, then open the client-target and dropdown pull request without Azure changes |
| Lighthouse onboarding | Delegate the client subscription to existing managing-tenant groups and open a client-target pull request |

Lighthouse onboarding is `py -m sentinel_app.lighthouse_onboarding --help`. It uses two
existing shared GitHub environments, each with an `AZURE_CLIENT_ID` variable, requires a
successful matching preview before Apply, and does not create the Sentinel workspace,
connectors, pipeline or analytics rules. See [the current onboarding guide](docs/APP-ONBOARDING.md).
Azure access management (elevating a Global Administrator) and Contributor setup are
onboarding steps done in Microsoft Entra ID:
[the access-management steps](docs/APP-ONBOARDING.md#enable-azure-resource-access-during-first-time-sign-in).

This application repository is separate from the private detection repository, which holds the
rules, the client targets and the deployment pipeline.

## Local data and privacy

Runtime data lives in `desktop-data/`, which Git ignores. Do not commit or share it.

| Item | Location |
|---|---|
| Saved clients | `clients.json` |
| Settings: rule library (`github_owner`, `github_repo`), `update_folder`, `gcc_high_onboarding`, and the command-line tools' own keys | `settings.json` |
| Per-client Azure CLI sessions | `sessions/` |
| Rule drafts and the unsaved-rule recovery copy | `drafts/` |
| Submissions and saved assignment, preview and deployment requests | `review-requests/`, `assignment-requests/`, `preview-requests/` |
| Window layout, last workspace per client and view choices | `ui-state.json` |
| Organization logo for the sidebar (optional) | `branding/logo-<height>.png` |
| Unexpected errors (kept under 1 MB) | `logs/errors.log` |

**Remove client…** (in the client **⋯** menu) removes only the saved entry; cloud resources and
onboarding state are not changed. Signing out of the app's Azure session does not deactivate
access at your privileged-access provider or sign out other browser or Windows sessions.

The app is independent of any employer, customer or privileged-access provider; CyberQP is
optional. Examples use fictional names and placeholder identifiers. Reference uploads in
`detection-as-code/` must be reviewed and sanitized separately before publication; see
[Privacy and publication notes](docs/PRIVACY.md).

## Development and checks

```text
py -m unittest discover -s tests -p "test_*.py"
py scripts\capture_screens.py <folder>
```

The tests fake every Azure and GitHub call and never contact a tenant. Desktop tests need
Tkinter and a Windows desktop session. GitHub Actions runs them on Windows for every push and
pull request; layout tests give the app a large virtual screen (`tests/roomy_screen.py`), so
they pass on the runner's small screen too. `capture_screens.py` drives the app through every
screen and dialog with sample data, saves a PNG of each, and exits non-zero if any hidden Tk
error occurs.

Live sign-in, tenant policies, API permissions and Run test query still need checking in an
authorized environment. The app is an initial implementation, not a claim of production
certification.

## Repository layout

| Location | Purpose |
|---|---|
| `Start-Sentinel.cmd` | Start the desktop app from source |
| `Build-Exe.cmd` | Build `dist\Sentinel Workspace.exe` |
| `sentinel_app/` | Application code (see the [project map](docs/PROJECT-MAP.md)) |
| `docs/` | Desktop guide, onboarding guides, project map, validation and privacy notes |
| `templates/` | Azure deployment templates |
| `examples/` | Placeholder configuration files |
| `scripts/` | Build assets, screenshots, CLI launchers and maintenance tools |
| `tests/` | Offline tests and fixtures |
| `detection-as-code/` | Sanitized private-repository reference uploads |
| `desktop-data/` | Your local clients, settings and sessions; ignored by Git |

## Documentation

- [Desktop guide](docs/DESKTOP-START-HERE.md) (also the in-app Help, F1)
- [Repository catalog, rule builder, reviews and deployments](docs/REPOSITORY-PILOT.md)
- [Current Lighthouse onboarding](docs/APP-ONBOARDING.md)
- [Same-tenant workspace registration](docs/SAME-TENANT-ONBOARDING.md)
- [Project file map and command-line entry points](docs/PROJECT-MAP.md)
- [Verification notes](docs/VALIDATION.md)
- [Rule schema provenance](docs/RULE-SCHEMA.md)
- [Privacy and publication](docs/PRIVACY.md)
- [Detection repository reference uploads](detection-as-code/README.md)
- Older setup routes: [per-client identity onboarding](docs/FULL-ONBOARDING.md),
  [permissions-only Git Bash](docs/GIT-BASH-QUICKSTART.md), [Azure portal only](docs/PORTAL-ONLY.md)

## License

Licensed under the [MIT License](LICENSE).
