# Azure / Sentinel audit evidence (v2)

Use **Discover** to sign in and select the client's actual workspace. Open
**Audit Evidence**, enter the start/end dates, and choose **Collect evidence &
save package**. Select an approved local evidence folder. Use **Open coverage & findings** to
review results. No terminal commands or GitHub access are needed.

This is read-only technical evidence collection for Azure public cloud. It does
not configure monitoring, grant permissions, change rules, or determine SOC 2,
ISO 27001 or CMMC compliance. Framework control mapping is deferred until the
Azure/Sentinel collection has been validated against live client workspaces.

## Collected evidence

- Current workspace settings and table settings, including retention.
- Azure role assignments at or above the workspace (IDs, scopes, conditions).
- Current Sentinel analytics rules and connector resources.
- Current workspace diagnostic settings.
- Current incident records for incidents **created** during the selected period.
- Available SentinelHealth and SentinelAudit events during that period.
- Daily Usage table summaries grouped by day, data type and quantity unit.
- Direct daily record counts for optionally specified expected log tables.

Dates are UTC and the end date is inclusive. Configuration is a snapshot taken
now, not configuration as of the audit period. Incidents created earlier are not
included even if active during the selected period. Incident comments, historical
status transitions, group membership, PIM eligibility, deny assignments, Entra,
Microsoft 365, endpoint evidence and GitHub records remain outside this version.

## Client requirements and findings

Choose **Edit client requirements (optional)** on the Audit Evidence page to enter:

- **Minimum searchable retention:** a whole number of days. Compared against the
  current workspace default and the explicitly listed expected tables. This does
  not compare archive/total retention or establish that historical records exist.
- **Expected log tables:** comma-separated exact table names, up to 20. Each gets
  its own read-only daily-count query. Use table names, not connector display names
  or KQL. For example, `Heartbeat, SecurityEvent` if the client expects those tables.
- **Critical rules required enabled:** exact rule IDs or unique display names,
  separated by semicolons, up to 50. Ambiguous names are flagged for review.

Blank fields skip their corresponding requirement comparisons. The app supplies
no framework defaults. Use approved client requirements, not guessed thresholds.
**Save requirements for this workspace** stores them locally under desktop-data,
keyed by tenant and workspace GUID. Selecting another workspace loads only its
own saved requirements; unsaved edits are cleared on workspace changes. Every
export includes the exact requirements used, whether or not they were saved.

Findings use three classifications:

- **Observed issue:** a reported health failure, explicitly disabled required
  critical rule, or configured searchable retention below the supplied minimum.
- **Needs review:** disabled rules with no required-enabled designation, days with
  no matching records, a critical rule not found in returned inventory, or absent
  client requirements. These do not prove a control failure or outage.
- **Insufficient evidence:** failed/partial collection, ambiguous rule matches,
  unknown retention or enabled state, or records with unusable dates.

Findings include an observation, next step and links to supporting source records
or collection details. The report filter can show one classification at a time.
Health failures describe events in the selected period; they do not establish an
ongoing outage. The app performs no remediation.

## Daily coverage dashboard

The report distinguishes completed requests from observed records for each day:

- Blue: records observed in a fully queried interval.
- Gray: query completed with no matching records.
- Amber: query coverage is incomplete or record dates could not be classified.
- Dashed border: current UTC day, which is still in progress.

Expand daily counts for the dates and counts behind each calendar. First/last
observed timestamps show the returned evidence range; they do not establish
continuous monitoring. Quiet days can reflect normal inactivity, retention,
permissions, missing monitoring or another cause. The app does not infer which.
If partial results contain records, their counts are a lower bound and the day
remains incomplete. Usage counts describe Usage records, not underlying events.
Expected-table counts directly query that table within the account's visibility.

## Access and availability

Use approved existing Azure read access. Collection uses ARM resource reads,
Microsoft.SecurityInsights read operations, Microsoft.Authorization/roleAssignments/read,
workspace/table/diagnostic settings reads and Log Analytics query access.
The app does not request additional grants or reuse the deployment service
principal. A workspace being discoverable does not prove every evidence source
is readable. Category failures remain visible while other categories continue.

An account/subscription/tenant/workspace mismatch stops collection. Non-public
Azure clouds are explicitly unsupported. Sign-in and MFA remain in the
existing Microsoft authentication flow.

Monitoring must already have been enabled, and logs must remain within retention.
Connector configuration does not prove ingestion. Usage summaries are not a full
source coverage or ingestion-gap analysis. Role assignments do not establish
effective access for every user.

## Export and interpretation

Each run creates a unique evidence-<workspace GUID>-<timestamp>-<random> folder:

- `report.html`: coverage dashboard, filterable findings and collection inventory.
- `coverage.json`: daily counts, query completeness and observed date bounds.
- `findings.json` / `findings.csv`: observations, next steps and source references.
- `requirements.json`: client requirements used for this specific run.
- `evidence.html`: readable original records referenced by findings; references
  use zero-based indexes into each category JSON's records array.
- `<category>.json`: returned records, source requests, queries and collection status.
- `<category>.csv`: flattened records where records exist; nested arrays remain JSON.
- `manifest.json`: workspace identity, dates, collector version, statuses and
  SHA-256 hashes for every other package file. These hashes are not signatures.

Statuses are **collected**, **no records**, **access denied**, **unavailable**,
**partial**, or **error**. None is a control pass/fail verdict. A successful query
only represents the signed-in account's permitted visibility. Narrowly scoped
table/row permissions can limit the returned data without an access error.

ARM lists follow pagination, with a 200-page safety limit. Log requests retrieve
up to 10,001 rows to detect overflow. Oversized or partial queries, and recognized
query timeouts, split into smaller disjoint time intervals. Only final intervals
contribute records, so parent retry results are not counted twice. Summary rows
from separate intervals are combined for the daily dashboard.

Per log category, collection stops at 127 requests, 100,000 exported rows, or an
unsplittable interval of one minute or less. Limit hits remain explicit as partial
or unqueried intervals. Every attempt, interval, query and error is preserved.
Denied/missing sources are not retried by splitting. The current day is queried
only through the run's initial log cutoff. Collection is not an atomic snapshot
of changing cloud records. Narrow dates when a result remains partial.

JSON preserves returned data. CSV neutralizes leading spreadsheet-formula
characters; use JSON for exact original values. Reports escape cloud text.
Packages can contain client-sensitive identities, incident details and queries.
Store/share through approved local evidence handling processes. No package is
uploaded by this feature. Generated evidence folders and desktop-data are ignored
by Git; do not rename/copy evidence into tracked source files.

## Live acceptance check

1. Select one approved client workspace after a fresh Microsoft sign-in.
2. Collect a short period with known incidents or monitoring events.
3. Compare workspace/table retention, rule states, connector resources, access
   assignments and incident IDs against the Azure portal for that exact workspace.
4. Compare the saved log queries against workspace Logs with the same UTC bounds
   and identity. Verify no-record/unavailable statuses are explained.
5. Confirm the exported manifest identity and hashes, and review every partial or
   denied category. Repeat with a restricted account if available.
6. Compare daily counts with the saved queries in Azure Logs. Verify a quiet day
   appears as no records, and a denied query appears as incomplete, not quiet.
7. Supply a known client retention minimum and critical rule ID. Check that each
   finding links to the correct record and distinguishes issues from uncertainty.
8. On an approved busy period, verify that split queries recover overflow without
   duplicating records. Review any remaining limits before relying on the export.

Offline tests simulate Azure. They do not establish that live permissions, API
availability, tenant policy or record completeness work for every client.

Reference APIs: [Sentinel incidents](https://learn.microsoft.com/en-us/rest/api/securityinsights/incidents/list?view=rest-securityinsights-2025-06-01),
[Azure role assignments](https://learn.microsoft.com/en-us/rest/api/authorization/role-assignments/list-for-scope?view=rest-authorization-2022-04-01),
[Log query format](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/request-format),
[Sentinel health and audit](https://learn.microsoft.com/en-us/azure/sentinel/health-audit).

Additional references: [Sentinel health schema](https://learn.microsoft.com/en-us/azure/sentinel/health-table-reference),
[Table retention properties](https://learn.microsoft.com/en-us/rest/api/loganalytics/tables/get?view=rest-loganalytics-2025-07-01),
[Partial query responses](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/response-format).
