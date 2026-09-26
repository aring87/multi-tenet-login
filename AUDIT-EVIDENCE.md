# Azure / Sentinel audit evidence (v1)

Use **Discover** to sign in and select the client's actual workspace. Open
**Audit Evidence**, enter the start/end dates, and choose **Collect evidence &
save package**. Select an approved local evidence folder. Use **Open report** to
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
- Available Usage table summaries grouped by data type and quantity unit.

Dates are UTC and the end date is inclusive. Configuration is a snapshot taken
now, not configuration as of the audit period. Incidents created earlier are not
included even if active during the selected period. Incident comments, historical
status transitions, group membership, PIM eligibility, deny assignments, Entra,
Microsoft 365, endpoint evidence and GitHub records are outside v1.

## Access and availability

Use approved existing Azure read access. Collection uses ARM resource reads,
Microsoft.SecurityInsights read operations, Microsoft.Authorization/roleAssignments/read,
workspace/table/diagnostic settings reads and Log Analytics query access.
The app does not request additional grants or reuse the deployment service
principal. A workspace being discoverable does not prove every evidence source
is readable. Category failures remain visible while other categories continue.

An account/subscription/tenant/workspace mismatch stops collection. Non-public
Azure clouds are explicitly unsupported in v1. Sign-in and MFA remain in the
existing Microsoft authentication flow.

Monitoring must already have been enabled, and logs must remain within retention.
Connector configuration does not prove ingestion. Usage summaries are not a full
source coverage or ingestion-gap analysis. Role assignments do not establish
effective access for every user.

## Export and interpretation

Each run creates a unique evidence-<workspace GUID>-<timestamp>-<random> folder:

- `report.html`: readable collection summary, scope and limitations.
- `<category>.json`: returned records, source requests, queries and collection status.
- `<category>.csv`: flattened records where records exist; nested arrays remain JSON.
- `manifest.json`: workspace identity, dates, collector version, statuses and
  SHA-256 hashes for every other package file. These hashes are not signatures.

Statuses are **collected**, **no records**, **access denied**, **unavailable**,
**partial**, or **error**. None is a control pass/fail verdict. A successful query
only represents the signed-in account's permitted visibility. Narrowly scoped
table/row permissions can limit the returned data without an access error.

ARM lists follow pagination, with a 200-page safety limit. Log queries request
10,001 records and export up to 10,000. Extra rows, partial API errors, a failed
later page or pagination limits produce a partial result. Narrow the date range
when a log export is partial. The collector does not silently treat missing
tables, denied access or invalid responses as empty evidence.

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

Offline tests simulate Azure. They do not establish that live permissions, API
availability, tenant policy or record completeness work for every client.

Reference APIs: [Sentinel incidents](https://learn.microsoft.com/en-us/rest/api/securityinsights/incidents/list?view=rest-securityinsights-2025-06-01),
[Azure role assignments](https://learn.microsoft.com/en-us/rest/api/authorization/role-assignments/list-for-scope?view=rest-authorization-2022-04-01),
[Log query format](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/request-format),
[Sentinel health and audit](https://learn.microsoft.com/en-us/azure/sentinel/health-audit).
