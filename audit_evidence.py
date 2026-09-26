"""Read-only Azure/Sentinel evidence collection; no compliance verdicts."""
import csv
import hashlib
import html
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, unquote

from desktop_backend import Stop, require, workspace_record

VERSION = "1.0"
ARM = "https://management.azure.com"
LOGS = "https://api.loganalytics.io"
MAX_PAGES = 200
LOG_LIMIT = 10000


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def date_range(start, end):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        require(first <= last, "Start date must be on or before end date.")
        require(last <= datetime.now(timezone.utc).date(), "End date cannot be in the future.")
        require((last-first).days < 366, "Collect up to 366 days per package.")
        return first.isoformat()+"T00:00:00Z", (last+timedelta(days=1)).isoformat()+"T00:00:00Z"
    except ValueError:
        raise Stop("Use valid dates in YYYY-MM-DD format.")


def error_status(error):
    text = str(error).lower()
    if any(x in text for x in ("authorizationfailed", "forbidden", "403", "insufficientaccess", "authorization_requestdenied")):
        return "access_denied"
    if any(x in text for x in ("failed to resolve", "tablenotfound", "notfound", "404")):
        return "unavailable"
    return "error"


def safe_error(error):
    # Never retain token-shaped strings or authorization headers from CLI diagnostics.
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", str(error))
    return re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted]", text)[:2000]


class Collector:
    def __init__(self, session, workspace):
        self.session, self.workspace = session, dict(workspace)
        self.base = ARM + quote(workspace["resource_id"], safe="/")
        self.results = []

    def get(self, url, original=None):
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and parsed.netloc in ("management.azure.com", "api.loganalytics.io")
                and not parsed.fragment, "Blocked unexpected evidence endpoint.")
        if original:
            expected = urlsplit(original)
            require(parsed.netloc == expected.netloc and
                    unquote(parsed.path).lower() == unquote(expected.path).lower(),
                    "Blocked pagination outside the selected resource collection.")
        return self.session.az("rest", "--method", "get", "--url", url,
                               "--resource", ARM+"/" if parsed.netloc == "management.azure.com" else LOGS)

    def collect(self, key, title, url, note, listing=True, query=None):
        self.session.log("Collecting evidence: "+title)
        result = dict(key=key, title=title, status="error", collected_at_utc=utcnow(),
                      source=url, note=note, records=[], requests=[], query=query)
        self.results.append(result)
        try:
            if query is not None:
                response = self.get(url)
                require(isinstance(response, dict), "Invalid log query response.")
                tables = response.get("tables", [])
                primary = next((t for t in tables if t.get("name") == "PrimaryResult"), None)
                if primary is None:
                    require(not response.get("error"), json.dumps(response.get("error")))
                    raise Stop("Log query returned no primary result table.")
                columns = [c["name"] for c in primary["columns"]]
                require(all(len(row) == len(columns) for row in primary["rows"]), "Invalid log result row.")
                result["records"] = [dict(zip(columns, row)) for row in primary["rows"][:LOG_LIMIT]]
                result["requests"].append(dict(url=url, retrieved_at_utc=utcnow()))
                if response.get("error") or len(primary["rows"]) > LOG_LIMIT:
                    result["status"] = "partial"
                    result["error"] = safe_error(json.dumps(response.get("error") or
                        {"message": f"More than {LOG_LIMIT} rows; narrow the date range."}))
                else:
                    result["status"] = "collected" if result["records"] else "no_records"
            else:
                seen, current = set(), url
                while current:
                    require(current not in seen, "Repeated pagination link; collection incomplete.")
                    require(len(seen) < MAX_PAGES, "Page limit reached; collection incomplete.")
                    seen.add(current)
                    response = self.get(current, url)
                    require(isinstance(response, dict), "Invalid Azure response.")
                    require(not response.get("error"), json.dumps(response.get("error")))
                    rows = response.get("value") if listing else [response]
                    require(isinstance(rows, list) and all(isinstance(x, dict) for x in rows),
                            "Azure response did not contain the expected records.")
                    result["records"].extend(rows)
                    result["requests"].append(dict(url=current, retrieved_at_utc=utcnow()))
                    current = response.get("nextLink") if listing else None
                result["status"] = "collected" if result["records"] else "no_records"
        except Exception as error:
            result["status"] = "partial" if result["records"] else error_status(error)
            result["error"] = safe_error(error)
        result["finished_at_utc"] = utcnow()
        return result

    def run(self, start, end):
        first, after = date_range(start, end)
        self.session.verify(self.workspace)
        cloud = self.session.az("cloud", "show")
        require(cloud.get("name") == "AzureCloud", "Evidence v1 supports Azure public cloud only.")
        current = self.get(self.base+"?api-version=2025-07-01")
        verified = workspace_record(dict(id=current.get("id"), customerId=current.get("properties", {}).get("customerId")),
                                    self.workspace["subscription_id"], self.workspace["tenant_id"])
        require(verified == self.workspace or all(verified[k].lower() == self.workspace[k].lower() for k in verified),
                "Workspace identity changed since discovery. Discover it again.")
        self.results.append(dict(key="workspace", title="Workspace settings", status="collected",
            collected_at_utc=utcnow(), source=self.base+"?api-version=2025-07-01", records=[current],
            note="Current configuration snapshot, including workspace retention; not historical configuration."))
        items = [
            ("tables", "Table settings and retention", "/tables?api-version=2025-07-01",
             "Current table settings. Inherited retention must be interpreted with workspace settings."),
            ("access", "Azure role assignments", "/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&%24filter=atScope%28%29",
             "Assignments at or above this workspace. Principal/role IDs are retained. Does not expand group membership, PIM eligibility, deny assignments or effective user access."),
            ("rules", "Sentinel analytics rules", "/providers/Microsoft.SecurityInsights/alertRules?api-version=2025-06-01",
             "Current rule configuration, including enabled state where provided; not proof of historical execution."),
            ("connectors", "Sentinel data connectors", "/providers/Microsoft.SecurityInsights/dataConnectors?api-version=2025-06-01",
             "Configured connector resources. Presence does not establish successful ingestion or full source coverage."),
            ("diagnostics", "Workspace diagnostic settings", "/providers/Microsoft.Insights/diagnosticSettings?api-version=2021-05-01-preview",
             "Current diagnostic destinations and enabled categories."),
        ]
        for key, title, suffix, note in items:
            self.collect(key, title, self.base+suffix, note)
        incident_filter = f"properties/createdTimeUtc ge {first} and properties/createdTimeUtc lt {after}"
        self.collect("incidents", "Incidents created in the selected period", self.base+
            "/providers/Microsoft.SecurityInsights/incidents?api-version=2025-06-01&%24filter="+quote(incident_filter, safe=""),
            "Current incident records filtered by creation date (UTC); excludes incidents created earlier, even if active in the period. Does not collect comments or reconstruct status history.")
        for key, title, table in [("health", "Sentinel health events", "SentinelHealth"),
                                   ("audit", "Sentinel audit events", "SentinelAudit")]:
            query = f"{table} | where TimeGenerated >= datetime({first}) and TimeGenerated < datetime({after}) | order by TimeGenerated desc | take {LOG_LIMIT+1}"
            self.collect(key, title, LOGS+"/v1/workspaces/"+self.workspace["workspace_id"]+"/query?query="+quote(query, safe=""),
                "Requires previously enabled monitoring and retained data. At most 10,000 rows exported; larger results are marked partial.", query=query)
        query = f"Usage | where TimeGenerated >= datetime({first}) and TimeGenerated < datetime({after}) | summarize UsageRecords=count(), FirstUsageRecord=min(TimeGenerated), LastUsageRecord=max(TimeGenerated), Quantity=sum(Quantity) by DataType, QuantityUnit | order by DataType asc | take {LOG_LIMIT+1}"
        self.collect("usage", "Available table usage summary", LOGS+"/v1/workspaces/"+self.workspace["workspace_id"]+"/query?query="+quote(query, safe=""),
            "Summary of available Usage records, not a complete ingestion or connector-gap assessment. Zero rows does not prove no ingestion.", query=query)
        self.session.verify(self.workspace)
        return self.results


def flatten(value, prefix=""):
    result = {}
    for key, item in value.items():
        name = prefix+str(key)
        if isinstance(item, dict):
            result.update(flatten(item, name+"."))
        else:
            result[name] = json.dumps(item, ensure_ascii=False) if isinstance(item, list) else item
    return result


def csv_value(value):
    text = "" if value is None else str(value)
    return "'"+text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def collect_evidence(session, workspace, start, end, destination):
    date_range(start, end)
    started = utcnow()
    collector = Collector(session, workspace)
    results = collector.run(start, end)
    folder = Path(destination) / ("evidence-"+workspace["workspace_id"]+"-"+
                                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema_version=VERSION, started_at_utc=started, finished_at_utc=utcnow(),
                    workspace=dict(workspace), period=dict(start_date_utc=start, end_date_utc_inclusive=end),
                    scope="Azure/Sentinel technical evidence only; no compliance determination or framework control mapping.",
                    results=[], files={})
    def write_json(path, value):
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    for result in results:
        write_json(folder/(result["key"]+".json"), result)
        rows = [flatten(row) for row in result["records"]]
        if rows:
            with (folder/(result["key"]+".csv")).open("w", newline="", encoding="utf-8-sig") as output:
                keys = sorted({key for row in rows for key in row})
                writer = csv.writer(output)
                writer.writerow([csv_value(k) for k in keys])
                writer.writerows([[csv_value(row.get(k)) for k in keys] for row in rows])
        manifest["results"].append({k: v for k, v in result.items() if k != "records"} | {"record_count": len(rows)})
    esc = lambda x: html.escape(str(x), quote=True)
    sections = "".join(f"<tr><td>{esc(r['title'])}</td><td>{esc(r['status'].replace('_',' '))}</td>"
                       f"<td>{len(r['records'])}</td><td>{esc(r['note'])}<p>{esc(r.get('error',''))}</p>"
                       f"<a href='{r['key']}.json'>JSON evidence</a></td></tr>" for r in results)
    report = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Azure / Sentinel Audit Evidence</title><style>body{font:16px Segoe UI,sans-serif;color:#233248;background:#f3f5f9;margin:32px;line-height:1.5}main{max-width:1200px;margin:auto}h1{color:#14263e}table{border-collapse:collapse;background:white;width:100%}td,th{padding:14px;text-align:left;vertical-align:top;border-bottom:1px solid #d7dee8}th{background:#e4eaf3}code{overflow-wrap:anywhere}a{color:#245de9}</style><main><h1>Azure / Sentinel Audit Evidence</h1>"""
    report += f"<p><b>Workspace:</b> {esc(workspace['workspace_name'])}<br><b>Tenant:</b> {esc(workspace['tenant_id'])}<br><b>Resource:</b> <code>{esc(workspace['resource_id'])}</code><br><b>Period:</b> {esc(start)} through {esc(end)} (UTC, inclusive)<br><b>Collected:</b> {esc(manifest['finished_at_utc'])}</p>"
    report += "<p>Read-only technical evidence. Configuration is a current snapshot; logs and incident creation dates use the selected period. This package does not establish SOC 2, ISO 27001 or CMMC compliance.</p><p>Collected means the request succeeded within the signed-in account's visibility, not that a control passed. No records, unavailable, access denied, error and partial results require review. Retention, enabled monitoring and access can limit evidence. Client-sensitive data may be present; store and share through approved channels.</p>"
    report += "<table><tr><th>Evidence</th><th>Collection status</th><th>Records</th><th>Scope and limitations</th></tr>"+sections+"</table><p>manifest.json records scope, timestamps, queries, requests and SHA-256 file hashes. Hashes detect later changes; they are not signatures or independent attestations. CSV cells are protected against spreadsheet formula execution; JSON retains original values.</p></main></html>"
    (folder/"report.html").write_text(report, encoding="utf-8")
    for path in sorted(folder.iterdir()):
        manifest["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(folder/"manifest.json", manifest)
    return dict(folder=str(folder), manifest=manifest)
