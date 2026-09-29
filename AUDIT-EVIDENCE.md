# Sentinel Configuration & Logs (v3)

Use Discover to sign in and select the client's workspace, then open Sentinel
Audit. Collection is read-only and uses the current account's visibility.

## Default: current configuration only

The four checked categories are analytics rules, data connectors, automation
rules, and available Sentinel product settings. Uncheck any category you do not
need. The export contains a readable report plus original returned records in
JSON and flattened CSV. Expand a configuration in the report to inspect its
query, scheduling, thresholds, incident grouping, automation conditions/actions,
and all returned fields.

Configuration is a snapshot taken now; selecting log dates does not reconstruct
historical settings. Connector resources do not prove ingestion or inventory all
source systems. The product settings endpoint uses a preview API and does not
expose every portal setting, including all health/audit diagnostic configuration.
Playbook references are included in automation actions; full Logic Apps workflow
definitions and their execution logs are outside this collection.

## Optional logs

- Select configuration-change logs (SentinelAudit) and/or operational health logs
  (SentinelHealth). These collect the selected period with explicit limits.
  They cover supported Sentinel activity, not every action in the portal.
  Availability depends on enabled monitoring, retention and access.
- Choose security source logs to load the workspace's table inventory. Ctrl/Shift
  selects multiple tables, up to 20. Nothing is selected automatically.
- Preview selected counts retrieves only record counts and latest event times
  for those tables in the selected period. A denied/failed query is not zero.
  Inventory results can themselves be partial; the picker shows their status.
- Use selected tables, then choose sample or period. Sample defaults to the 100
  most recent records per table (adjustable 1–1000). It is neither a random sample
  nor a complete period export. Period exports attempt all matching records
  within the limits below. Source samples/periods retain the returned event fields.

The default log period is seven UTC calendar days including today. End dates are
inclusive; the current day ends at collection time. Dates are ignored for
configuration-only exports. Switching workspaces clears log selections and the
previous export link. Saved v2 requirements are not loaded or deleted.

Table discovery does not imply that a table contains events. Queries currently
use the Analytics query endpoint. Basic/Auxiliary tables and unavailable sources
may reject queries; failures remain explicit. No search jobs are created and no
monitoring settings or table plans are changed.

## Export package

Exports are stored under sentinel-exports / tenant GUID / workspace GUID / timestamp-and-run-ID.
The sentinel-exports directory is ignored by Git.
Each package contains:

- report.html: selected categories, collection status, and configuration details.
  Source event payloads stay in the export files rather than flooding the report.
- One JSON file per selected category: original records plus source requests,
  timestamps, query text, errors and unresolved intervals where applicable.
- CSV for each category that returned records. Nested values are JSON-encoded;
  spreadsheet formula prefixes are escaped. JSON retains original values.
- manifest.json: tenant/workspace identity, selections, log dates (if applicable),
  statuses and SHA-256 hashes for the other files.

Samples say sampled; successful empty collections say no records. Unavailable,
access denied, error and partial remain distinct. Hashes detect later file
changes; they are not independent attestations.

No Azure access inventory, usage/billing summaries, incident dumps, all-table
retention exports, requirement comparisons, findings or daily coverage dashboard
are produced by this workflow. The legacy analysis module remains for older
consumers but is not called by v3 exports.

## Limits and live verification

Configuration pagination is limited to 200 pages per category. Period log queries
split oversized responses into disjoint intervals down to one minute, with up to
127 requests and 100,000 exported records per table. Unresolved intervals and
limits mark the result incomplete. Narrow the dates to retry. These limits also
apply to Sentinel activity logs. No claim of completeness is made beyond the
signed-in account's visibility, source availability and returned query results.

Before relying on a client export:

1. Compare each selected configuration category against Sentinel in the same
   workspace, especially rule queries/states and automation order/actions.
2. Confirm that a configuration-only run makes no log queries and exports only
   the selected categories.
3. Select a known source table and compare preview count/latest timestamp and
   exported sample against Logs with identical UTC bounds and identity.
4. Check Sentinel activity logs over a short known period. Review unavailable,
   denied or partial statuses; these do not mean no activity occurred.
5. Inspect manifest identity, query bounds, sample labels and incomplete ranges.

Offline tests use synthetic responses. Live tenant permissions and API behavior
still require this pilot. Client evidence should stay outside the repository.

API references: [analytics rules](https://learn.microsoft.com/en-us/rest/api/securityinsights/alert-rules/list?view=rest-securityinsights-2025-06-01),
[automation rules](https://learn.microsoft.com/en-us/rest/api/securityinsights/automation-rules/list?view=rest-securityinsights-2025-06-01),
[product settings](https://learn.microsoft.com/en-us/rest/api/securityinsights/product-settings/list?view=rest-securityinsights-2025-07-01-preview),
[health and audit](https://learn.microsoft.com/en-us/azure/sentinel/health-audit),
[query format](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/request-format).
