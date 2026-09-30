# Repository catalog pilot

This is the first read-only increment of the repository UI design. Existing onboarding and live Sentinel views remain available. The new **Repository catalog** page reads a detection repository without executing its scripts. No commits, pull requests, workflow dispatches or Azure changes are made by the catalog.

## Start the development copy

Run these commands from the development repository in Git Bash:

```bash
cd /g/multi-tenet-tool/multi-tenet-login
py -m pip install -r requirements.txt
py desktop_app.py
```

Use Python 3.10+ with Tkinter. Keep the working onboarding copy at its known working revision. Development data is stored in this clone's ignored `desktop-data` directory. Both copies can still access real cloud resources through the existing onboarding pages.

## Browse clients and rules

1. Open **Repository catalog**.
2. Choose a local detection repository root containing `clients/` and `rules/sentinel/`, or select **GitHub repository** and enter `owner/name`.
3. Select **Refresh catalog**. Local mode shows working files, including uncommitted changes. GitHub mode reads the default branch at one commit and shows its SHA and signed-in account.
4. Switch between Clients and Rules, search, filter configured state and select a row to see assignments and raw configuration.
5. Check **Show catalog issues** if any file is unavailable or malformed. A loaded catalog is not a pipeline validation result and does not establish deployment state.

For this pilot GitHub mode uses the existing GitHub CLI login (`gh auth login --hostname github.com`); local mode requires no GitHub or Azure sign-in. The planned GitHub App device login, request history and installable distribution are subsequent work, not implemented here. No new personal access token is required by this feature.

The catalog reads YAML workspace manifests recursively, including secondary workspace filenames, and YAML rules under `rules/sentinel/`. It supports the supplied version 1 manifest shape, reports duplicate target IDs, missing assigned rules and unreadable files, and rejects unsafe YAML and linked directories/files. It is a display adapter, not a replacement for the repository's authoritative validators. An unspecified rule enabled value remains unspecified; it is never inferred from lifecycle status.

## Limits and next checks

Snapshots stay in memory and are refreshed manually. No automatic fetch or local repository updates occur. Remote loads make one blob request per relevant file; large inventories should use local mode until caching is added. Trees truncated by GitHub fail visibly rather than producing a complete-looking inventory. Limits: 2 MB per file, 32 MB combined, 2,000 catalog files. Alias-based YAML is reported as unsupported.

The actual private detection repository must be checked before claiming schema compatibility. Its existing automatic GitHub dropdown update should remain intact. Confirm the current rule format, assignment semantics and authentication flow against that repository before adding writes.

Validation commands:

```bash
py -m unittest discover -s tests -p 'test_*.py' -v
```

All new test data is synthetic. GitHub API reads are mocked in offline tests. No live-client deployment test is part of this increment.
