# Repository UI pilot

This development branch includes the read-only catalog and local rule authoring. Existing onboarding and live Sentinel views remain available. The new **Repository catalog** page reads a detection repository without executing its scripts. No commits, pull requests, workflow dispatches or Azure changes are made by the catalog.

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

For this pilot GitHub mode uses the existing GitHub CLI login (`gh auth login --hostname github.com`); local mode requires no GitHub or Azure sign-in. The planned GitHub App device login, live review/check status and installable distribution are subsequent work, not implemented here. No new personal access token is required by this feature.

The catalog reads YAML workspace manifests recursively, including secondary workspace filenames, and YAML rules under `rules/sentinel/`. It supports the supplied version 1 manifest shape, reports duplicate target IDs, missing assigned rules and unreadable files, and rejects unsafe YAML and linked directories/files. It is a display adapter, not a replacement for the repository's authoritative validators. An unspecified rule enabled value remains unspecified; it is never inferred from lifecycle status.

## Limits and next checks

Snapshots stay in memory and are refreshed manually. No automatic fetch or local repository updates occur. Remote loads make one blob request per relevant file; large inventories should use local mode until caching is added. Trees truncated by GitHub fail visibly rather than producing a complete-looking inventory. Limits: 2 MB per file, 32 MB combined, 2,000 catalog files. Alias-based YAML is reported as unsupported.

The builder schema was reviewed against the supplied source snapshot; live private-repository compatibility still requires a pilot. The existing automatic GitHub dropdown update remains in onboarding. Client assignment, authentication and workflow execution must be checked against the private repository before adding those actions to the new UI.

Validation commands:

```bash
py -m unittest discover -s tests -p 'test_*.py' -v
```

All new test data is synthetic. GitHub API reads are mocked in offline tests. No live-client deployment test is part of this increment.

## Create or edit a rule

1. Open **Rule builder**, or select a rule in the catalog and choose **Edit selected rule as draft**.
2. Fill in Basics, Query & schedule, and MITRE. A new rule has a fresh ID and starts disabled.
   Editing keeps the existing ID. New draft is the way to start a distinct detection.
3. For existing rules, optional entity mappings, custom details, suppression, grouping and
   template metadata remain under Advanced. Unknown properties remain visible and block export
   until the schema supports them or you explicitly correct them.
4. **Save draft** keeps incomplete work in a local `.rule-draft.json` file. The default folder
   is ignored `desktop-data/drafts/`. **Open draft** restores it, including its original ID.
5. **Validate & review** checks the reviewed repository schema and displays the YAML.
   Changes clear that preview. A missing-MITRE migration exception must also be allowed by
   each destination's workspace manifest; this editor does not change those settings.
6. **Export YAML** validates again and saves the file you select. Add it under `rules/sentinel/`
   in the private detection repository through your existing review process. Exporting does
   not assign clients, open a PR, preview against Azure, or deploy anything.

The validator is a bundled, reviewed copy of your uploaded rules.py; see [schema provenance](RULE-SCHEMA.md).
KQL still needs a workspace query check. Keep local drafts and private queries out of this
public application repository. Guided draft PR submission is available below; client assignment and workflow execution are subsequent increments.

## Submit a rule for repository review

1. Sign in to GitHub CLI with the account authorized for the private detection repository.
   This pilot uses that existing login; it does not require creating a new personal token.
2. For an existing rule, refresh the **GitHub repository** catalog and choose
   **Edit selected rule as draft**. Keep the original rule ID and path. Draft saves retain
   this repository source. A rule opened only from a local YAML file cannot overwrite an
   existing remote rule; open that rule from the GitHub catalog first.
3. In **Rule builder**, finish validation and select **Review for GitHub**.
4. Check the private repository and file path, then choose **Prepare review**. This only
   reads GitHub. Review the exact diff, signed-in account, base revision, existing client
   references and their overrides. New files do not automatically get client assignments.
5. Select **Create / recover draft PR** to create one rule-file commit on a new branch and
   a draft pull request. The app never writes to the default branch, merges the request,
   or dispatches an Azure deployment. Creating a branch or PR can trigger workflows already
   configured in your repository. Use **Open pull request** for its checks and review.
6. If a response times out, retry in the same dialog. After an app restart, use
   **Saved review requests** to search and reopen a request. For files copied from another computer, use **Open request file** and select the matching file from
   `desktop-data/review-requests/`. Requests include private YAML and are ignored by Git.
   Keep these app-created files unchanged. Completed requests open their existing PR;
   changed review branches are refused rather than overwritten.

Submitting requires write access to the private repository and permission to create pull
requests. Repository rulesets and organization policies still apply. The preview refuses
incomplete catalogs, duplicate shared IDs and stale source revisions; existing target
migration exceptions and overrides are validated separately. If main changes before the
first branch write, refresh the catalog and prepare a new review.

This is schema validation, not live KQL execution or certification of every ARM property.
The private repository's CI, human review, and deployment approvals remain authoritative.
Tests use synthetic GitHub responses, including lost-write responses; no private rules
have been uploaded or live tenant deployments performed during development.

API references: [Git trees](https://docs.github.com/en/rest/git/trees),
[Git references](https://docs.github.com/en/rest/git/refs),
[pull requests](https://docs.github.com/en/rest/pulls/pulls).

## Find a saved review

In **Rule builder**, select **Saved review requests**. The list reads this computer's
`desktop-data/review-requests/` folder, newest first. Search by rule name, repository,
rule file path or request ID, then choose **Open saved review** to inspect the original
diff and recover an interrupted submission or open its PR. Your current draft stays intact.

**PR link saved** means a URL was recorded locally; it does not indicate current GitHub
checks, review or merge status. **Submission unconfirmed** means the request may need
recovery; it does not mean GitHub received nothing. Opening a saved review never submits
it automatically. Use its explicit recovery button if needed.

Unreadable files are reported and left unchanged. The list shows up to 200 recent files;
use **Open request file** for older files. A new laptop has an empty list unless you copy
its private review request files into that laptop's ignored app data folder.
