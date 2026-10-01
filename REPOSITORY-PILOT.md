# Repository UI pilot

This development branch includes the read-only catalog and local rule authoring. Existing onboarding and live Sentinel views remain available. The new **Repository catalog** page reads a detection repository without executing its scripts. Browsing the catalog makes no changes. The separate rule-review, client-preview and deployment dialogs require an explicit submission action.

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
public application repository. Guided draft PR submission is available below; client assignment remains a subsequent increment; selected-rule previews and reviewed deployment controls are available below.

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

## Preview a rule for selected clients

1. Load the **GitHub repository** catalog for the private detection repository.
   Resolve any catalog issues first. Local working files cannot start workflow runs.
2. Choose **Rules**, select a rule, and click **Preview / deploy selected rule for clients**.
3. Click client rows (or press Space) to select them. Search by client, workspace or
   target. **Select all eligible** selects all enabled targets that already reference
   this rule, including those hidden by the search. Disabled or unassigned targets
   show why they cannot be selected. This does not add assignments.
4. Choose **Prepare request**. This reads GitHub and verifies the current catalog,
   private repository, account, default branch and workflow inputs. Review the exact
   clients, rule, revision and number of batches. Preparing starts no workflow.
5. Choose **Start previews** to dispatch the private repository's configured workflow
   with `mode: preview` on `main`. This button only sends preview mode. Deployment requires the separate review below. The existing
   workflow remains responsible for its Azure operations and preview artifacts.
6. **Refresh status** reads the exact run IDs returned by GitHub. Select a run and
   choose **Open GitHub** to inspect jobs, errors and artifacts. Submission is not a
   successful preview. If GitHub returns no run ID, open the workflow's Actions page;
   the app does not guess which recent run is yours.

The adapter reads `.github/workflows/sentinel-multi-workspace.yml` at the reviewed
revision. It supports the supplied `targets` string contract and the dropdown
`target`, `target_2`, ... contract, together with `rule_path` and a `preview` mode
choice. Unknown input contracts stop visibly instead of guessing. For dropdowns,
the exact client IDs must already exist in the choices; run your existing dropdown
sync workflow if needed. The app splits selections larger than the available slots
into concrete batches. It never substitutes the workflow's **All enabled clients**
value. Up to 100 workspaces can be selected per request.

The default branch must be `main`. The app rechecks its revision and account before
each batch, but GitHub resolves the branch when a run starts. This is not a pinned
commit deployment mechanism. Status refresh flags a run using a different revision.

Receipts in ignored `desktop-data/preview-requests/` record each batch before it is
sent. If submission times out, it may still have started. Further batches stop and
the same request cannot be dispatched again. Check Actions before creating another
request. After an app restart, use Actions for existing runs; receipts retain their
run IDs and URLs, but an in-app receipt browser is not implemented in this increment.

GitHub CLI uses your existing authorized login; no new personal token is needed.
GitHub must allow that identity to read the repository and write Actions. Azure
permissions, environment approvals, and policies are still enforced by the workflow.
Private-repository and live-workspace testing remain required; development tests
use synthetic files and mocked dispatch responses.

API contract: [GitHub workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
using API version `2026-03-10` for returned run IDs. The user reported a successful
rule creation, merge and workspace deployment pilot; the new in-app deployment
controls still need a pilot against the private repository.

## Export rules during a client audit

After signing in and choosing a workspace, open **Analytics rules** and click
**Export all rules**. Save the ZIP containing `rules.json` (all original rule
configuration fields plus collection metadata), `rules.csv` (readable state and
flattened configuration columns), and `manifest.json` (workspace identifiers,
collection/export times, count, completeness, and SHA-256 file hashes).

This exports the inventory already loaded, including enabled, disabled and
unspecified states. Search and state filters do not restrict it, and export does
not call Azure again. Use **Refresh rules** first when you need a newer snapshot.
An incomplete collection requires acknowledgment and remains marked incomplete
in the package; a failed collection with no records cannot be exported. A
successful empty collection can be exported. Switching workspace or signing out
clears the cached inventory. Completeness refers to the API collection within
the account's visibility, not a compliance verdict or a history of rule changes.

Numeric client slugs retain their string identity in generated YAML. Existing
private detection repositories must also apply the validator patch and quote
previously generated numeric manifests; see `detection-as-code/README.md`.

## Deploy a previewed rule from the app

1. In **Repository catalog**, load the private GitHub repository, select a rule,
   and choose **Preview / deploy selected rule for clients**.
2. Select assigned, enabled client targets, prepare the exact request, and start
   previews. Use **Open GitHub** to review each run's checks and what-if artifacts.
3. Select **Refresh status**. **Review deployment** becomes available only after
   all confirmed preview runs finish successfully at the reviewed main revision.
   A partial submission, unknown run ID, failed run, or different revision blocks it.
4. **Review deployment** reads GitHub again without submitting anything. Review
   the repository, account, rule, exact clients/workspaces, subscription/resource
   group, configured enabled/disabled state, assignment overrides and preview runs.
5. Select **Start reviewed deployment** to send `mode: deploy` through the existing
   `sentinel-multi-workspace.yml`, using the same selected rule and concrete targets.
   Selections larger than the workflow's slots are split into batches. The app
   never sends the **All enabled clients** sentinel or assigns additional clients.
6. **Refresh status** reports each returned run; **Open GitHub** opens its jobs,
   artifacts and any environment approvals. Submission is not deployment success.
   Reopening the review in the same preview dialog retains the submitted request
   and cannot dispatch it a second time.

The supplied workflow already supports deploy mode, performs new previews, and
uses the same per-run deployment bundle for its production job. No workflow file
replacement is needed for this increment if that contract is unchanged. GitHub
uses its configured environment protections; the app does not approve environments,
change protection rules or bypass Azure permissions. If production has no approval
requirement, a submitted deployment can proceed without another prompt.

The app rechecks preview run identity, successful completion and revision, current
account/repository/workflow, and main before submission and each additional batch.
A newer main revision requires a refreshed catalog and new previews. This check
is not atomic with GitHub dispatch: GitHub resolves main when starting each run.
Do not treat this as a pinned-commit deployment. Review each deployment run's actual
revision and fresh preview artifacts before approving production; status refresh
flags revision mismatches. No automatic rollback or cancellation is performed.

Deployment receipts are saved in ignored `desktop-data/deployment-requests/` as
`.deployment.json`, including the original preview evidence. An uncertain response
stops further batches and cannot be automatically retried; check Actions before
starting a new request. After restarting the app, follow the saved run IDs in GitHub
Actions. An in-app deployment history browser is a subsequent increment.

Development validation uses mocked GitHub APIs and synthetic tenants only. No
private workflow or live Azure deployment is started by the development tests.
