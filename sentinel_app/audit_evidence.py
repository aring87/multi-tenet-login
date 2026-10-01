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

from .desktop_backend import Stop, require, workspace_record

VERSION = "3.0"
ARM = "https://management.azure.com"
LOGS = "https://api.loganalytics.io"
MAX_PAGES = 200
LOG_LIMIT = 10000
MAX_LOG_REQUESTS = 127
MAX_LOG_RECORDS = 100000
MIN_LOG_WINDOW = timedelta(minutes=1)

CONFIGURATIONS = {
    "rules": ("Analytics rules", "alertRules", "2025-06-01"),
    "connectors": ("Data connectors", "dataConnectors", "2025-06-01"),
    "automation": ("Automation rules", "automationRules", "2025-06-01"),
    "settings": ("Sentinel settings", "settings", "2025-07-01-preview"),
}


def validate_options(value=None):
    value = {} if value is None else value
    require(isinstance(value, dict), "Invalid export selections.")
    require(not set(value)-{"configurations", "audit", "health", "source_tables", "source_mode", "sample_limit"}, "Unknown export selection.")
    configs = value.get("configurations", list(CONFIGURATIONS))
    require(isinstance(configs, list) and all(isinstance(k, str) and k in CONFIGURATIONS for k in configs), "Invalid configuration selection.")
    tables = value.get("source_tables", [])
    require(isinstance(tables, list) and len(tables) <= 20 and all(isinstance(t, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", t) for t in tables), "Select up to 20 table names; queries are not accepted.")
    require(all(type(value.get(k, False)) is bool for k in ("audit", "health")), "Invalid activity selection.")
    mode, limit = value.get("source_mode", "sample"), value.get("sample_limit", 100)
    require(mode in ("sample", "period"), "Select sample or period export.")
    require(type(limit) is int and 1 <= limit <= 1000, "Sample size must be 1–1000 records per table.")
    return dict(configurations=list(dict.fromkeys(configs)), audit=value.get("audit", False), health=value.get("health", False),
                source_tables=list(dict.fromkeys(tables)), source_mode=mode, sample_limit=limit)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def date_range(start, end):
    try:
        require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", start)) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", end)), "Use dates in YYYY-MM-DD format.")
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

    def collect_log(self, key, title, table, first, after, summary=False):
        """Split oversized/partial queries into disjoint half-open intervals.

        Only leaf responses contribute records. A discarded parent response must
        never be counted alongside its children. Attempts and unresolved ranges
        remain in the evidence even when the request/record budget is reached.
        """
        result = dict(key=key, title=title, status="error", collected_at_utc=utcnow(),
                      source=table, records=[], requests=[], segments=[], table=table,
                      period_start_utc=first.isoformat(), period_end_utc_exclusive=after.isoformat(),
                      record_kind="daily_summary" if summary else "events",
                      note="UTC period evidence within this account's visibility. Empty days do not prove an outage. Large queries are split; unresolved intervals remain explicit.")
        self.results.append(result)
        pending = [(first, after)]
        requests = 0
        while pending:
            lo, hi = pending.pop()
            segment = dict(start_utc=lo.isoformat(), end_utc_exclusive=hi.isoformat())
            if requests >= MAX_LOG_REQUESTS or len(result["records"]) >= MAX_LOG_RECORDS:
                result["segments"].append(segment | dict(status="not_queried", record_count=0, error="Collection safety limit reached; narrow the date range."))
                continue
            self.session.log(f"Collecting {table}: {lo.date()} through {hi.date()} (request {requests+1})")
            query = f"{table} | where TimeGenerated >= datetime({lo.isoformat()}) and TimeGenerated < datetime({hi.isoformat()})"
            if summary:
                query += " | summarize Records=count(), FirstRecord=min(TimeGenerated), LastRecord=max(TimeGenerated) by Day=startofday(TimeGenerated)"
                if table == "Usage":
                    query = query.replace("Records=count()", "Records=count(), Quantity=sum(Quantity)").replace("by Day=startofday(TimeGenerated)", "by Day=startofday(TimeGenerated), DataType, QuantityUnit")
                query += " | order by Day asc"
            else:
                query += " | order by TimeGenerated asc"
            query += f" | take {LOG_LIMIT+1}"
            url = LOGS+"/v1/workspaces/"+self.workspace["workspace_id"]+"/query?query="+quote(query, safe="")
            attempt = segment | dict(query=query, url=url, requested_at_utc=utcnow())
            result["requests"].append(attempt)
            requests += 1
            rows, problem, status = [], None, "error"
            try:
                response = self.get(url)
                require(isinstance(response, dict), "Invalid log query response.")
                primary = next((t for t in response.get("tables", []) if t.get("name") == "PrimaryResult"), None)
                require(primary is not None, safe_error(json.dumps(response.get("error") or "No primary result table")))
                columns = [c["name"] for c in primary["columns"]]
                require(len(set(columns)) == len(columns) and all(len(row) == len(columns) for row in primary["rows"]), "Invalid log query rows.")
                rows = [dict(zip(columns, row)) for row in primary["rows"]]
                if response.get("error") or len(rows) > LOG_LIMIT:
                    problem = safe_error(json.dumps(response.get("error") or "Row limit exceeded"))
                    status = "partial"
                else:
                    status = "collected" if rows else "no_records"
            except Exception as error:
                problem, status = safe_error(error), error_status(error)
                # Some CLI/API failures carry no body. Retry by narrowing only
                # recognized size/time limits, never denied or unavailable sources.
                if status == "error" and any(word in problem.lower() for word in ("partialerror", "e_query_result_set_too_large", "timeout", "timed out")):
                    status = "partial"
            attempt.update(status=status, returned_rows=len(rows), finished_at_utc=utcnow())
            if problem: attempt["error"] = problem
            if status == "partial" and hi-lo > MIN_LOG_WINDOW and requests < MAX_LOG_REQUESTS:
                midpoint = lo+(hi-lo)/2
                attempt["superseded_by_split"] = True
                pending.extend([(midpoint, hi), (lo, midpoint)])
                continue
            remaining = MAX_LOG_RECORDS-len(result["records"])
            accepted = rows[:min(LOG_LIMIT, remaining)]
            if len(accepted) < len(rows):
                status, problem = "partial", "Export record limit reached; narrow the date range."
            offset = len(result["records"])
            result["records"].extend(accepted)
            segment.update(status=status, record_count=len(accepted), first_record_index=offset)
            if problem: segment["error"] = problem
            result["segments"].append(segment)
        result["segments"].sort(key=lambda s:s["start_utc"])
        incomplete = [s for s in result["segments"] if s["status"] not in ("collected", "no_records")]
        if incomplete:
            statuses = {s["status"] for s in incomplete}
            result["status"] = next(iter(statuses)) if len(statuses)==1 and len(incomplete)==len(result["segments"]) and not result["records"] else "partial"
            if result["status"] == "not_queried": result["status"] = "partial"
            result["error"] = f"{len(incomplete)} time interval(s) incomplete. See segments and requests."
        else:
            result["status"] = "collected" if result["records"] else "no_records"
        result["finished_at_utc"] = utcnow()
        return result

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

    def verify_workspace(self):
        self.session.verify(self.workspace)
        require(self.session.az("cloud", "show").get("name") == "AzureCloud", "Evidence collection supports Azure public cloud only.")
        current = self.get(self.base+"?api-version=2025-07-01")
        verified = workspace_record(dict(id=current.get("id"), customerId=current.get("properties", {}).get("customerId")),
                                    self.workspace["subscription_id"], self.workspace["tenant_id"])
        require(all(verified[k].lower() == self.workspace[k].lower() for k in verified),
                "Workspace identity changed since discovery. Discover it again.")

    def query(self, key, title, query, note):
        url = LOGS+"/v1/workspaces/"+self.workspace["workspace_id"]+"/query?query="+quote(query, safe="")
        return self.collect(key, title, url, note, query=query)

    def sample(self, key, table, first, after, limit):
        query = (f"{table} | where TimeGenerated >= datetime({first.isoformat()}) and TimeGenerated < datetime({after.isoformat()})"
                 f" | top {limit} by TimeGenerated desc")
        result = self.query(key, table+" — recent sample", query, f"Up to {limit} most recent records in the selected period; not a complete period export or a random sample.")
        result.update(table=table, record_kind="sample", sample_limit=limit,
                      period_start_utc=first.isoformat(), period_end_utc_exclusive=after.isoformat())
        if result["status"] == "collected": result["status"] = "sampled"
        return result

    def run(self, start, end, options=None):
        options = validate_options(options)
        require(options["configurations"] or options["audit"] or options["health"] or options["source_tables"], "Select configuration or logs to export.")
        has_logs = options["audit"] or options["health"] or options["source_tables"]
        if has_logs:
            first, after = date_range(start, end)
            period_start = datetime.fromisoformat(first.replace("Z", "+00:00"))
            period_end = min(datetime.fromisoformat(after.replace("Z", "+00:00")), datetime.now(timezone.utc))
        self.verify_workspace()
        for key in options["configurations"]:
            title, resource, version = CONFIGURATIONS[key]
            note = "Current configuration snapshot; not historical configuration."
            if key == "connectors": note += " Connector resources do not prove ingestion or cover every source visible in the portal."
            if key == "settings": note += " Available product settings only (preview API); not every portal setting or monitoring diagnostic setting is exposed here."
            self.collect(key, title, self.base+"/providers/Microsoft.SecurityInsights/"+resource+"?api-version="+version, note)
        for key, table in (("audit", "SentinelAudit"), ("health", "SentinelHealth")):
            if options[key]: self.collect_log(key, table, table, period_start, period_end)
        for index, table in enumerate(options["source_tables"]):
            key = f"source_{index}"
            if options["source_mode"] == "sample":
                self.sample(key, table, period_start, period_end, options["sample_limit"])
            else:
                self.collect_log(key, table, table, period_start, period_end)
        self.session.verify(self.workspace)
        return self.results


def list_log_tables(session, workspace):
    collector = Collector(session, workspace)
    collector.verify_workspace()
    result = collector.collect("tables", "Available log tables", collector.base+"/tables?api-version=2025-07-01",
                               "Table inventory only; this does not establish that records exist.")
    session.verify(workspace)
    return result


def preview_log_tables(session, workspace, start, end, tables):
    options = validate_options(dict(source_tables=tables))
    first, after = date_range(start, end)
    after = min(datetime.fromisoformat(after.replace("Z", "+00:00")), datetime.now(timezone.utc)).isoformat()
    collector = Collector(session, workspace)
    collector.verify_workspace()
    for index, table in enumerate(options["source_tables"]):
        result = collector.query(f"preview_{index}", table,
            f"{table} | where TimeGenerated >= datetime({first}) and TimeGenerated < datetime({after}) | summarize Records=count(), LatestEvent=max(TimeGenerated)",
            "Count and latest event within the selected period and account visibility; no source event payloads exported.")
        result["table"] = table
    session.verify(workspace)
    return collector.results


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


def collect_evidence(session, workspace, start, end, destination, options=None):
    options = validate_options(options)
    started = utcnow()
    results = Collector(session, workspace).run(start, end, options)
    has_logs = bool(options["audit"] or options["health"] or options["source_tables"])
    # GUIDs form the directories; display names never become path components.
    tenant, workspace_id = str(uuid.UUID(workspace["tenant_id"])), str(uuid.UUID(workspace["workspace_id"]))
    folder = Path(destination) / "sentinel-exports" / tenant / workspace_id / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema_version=VERSION, started_at_utc=started, finished_at_utc=utcnow(),
                    workspace=dict(workspace), period=dict(start_date_utc=start, end_date_utc_inclusive=end) if has_logs else None,
                    scope="Selected Sentinel configuration and logs within this account's visibility.",
                    selections=options, results=[], files={})
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
    (folder/"report.html").write_text(render_report(workspace, manifest, results), encoding="utf-8")
    for path in sorted(folder.iterdir()):
        manifest["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(folder/"manifest.json", manifest)
    return dict(folder=str(folder), manifest=manifest)


def render_report(workspace, manifest, results):
    esc = lambda x: html.escape(str(x), quote=True)
    report = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Sentinel Configuration &amp; Logs</title><style>body{font:16px Segoe UI,sans-serif;color:#233248;background:#f2f5f8;margin:32px;line-height:1.5}main{max-width:1100px;margin:auto}h1{color:#172b43;font-size:30px;letter-spacing:-.6px}h2{font-size:21px}h3{font-size:16px}section{background:#fff;border:1px solid #dce4ec;padding:24px;margin:24px 0;border-radius:8px}table{border-collapse:collapse;background:white;width:100%}td,th{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #d7dee8}th{background:#eaf0f5;font-size:13px}code,td{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf2f7;padding:16px}details{background:white;padding:12px;margin:8px 0}summary{cursor:pointer}a{color:#0f766e;text-underline-offset:3px}.status{font-weight:600;color:#354b62}@media print{body{margin:0}details{break-inside:avoid}}</style><main><h1>Sentinel Configuration &amp; Logs</h1>"""
    report += f"<p><b>Workspace:</b> {esc(workspace['workspace_name'])}<br><b>Tenant:</b> {esc(workspace['tenant_id'])}<br><b>Resource:</b> <code>{esc(workspace['resource_id'])}</code><br><b>Collected:</b> {esc(manifest['finished_at_utc'])}</p>"
    period = manifest["period"]
    if period: report += f"<p><b>Log period:</b> {esc(period['start_date_utc'])} through {esc(period['end_date_utc_inclusive'])} (UTC, inclusive; today ends at collection time).</p>"
    report += "<p>Configuration is a snapshot taken now. Only selected categories are included. Samples are not complete period exports. Unavailable, denied and partial collections are shown explicitly; no records does not prove that monitoring is disabled.</p>"
    report += "<h2>Collection status</h2><table><tr><th>Selected category</th><th>Status</th><th>Records</th><th>Export</th></tr>"
    for result in results:
        key = esc(result["key"])
        report += f"<tr><td><a href='#{key}'>{esc(result['title'])}</a></td><td>{esc(result['status'].replace('_',' '))}</td><td>{len(result['records'])}</td><td><a href='{key}.json'>JSON</a>"
        if result["records"]: report += f" · <a href='{key}.csv'>CSV</a>"
        report += "</td></tr>"
    report += "</table>"
    for result in results:
        key = result["key"]
        report += f"<section id='{esc(key)}'><h2>{esc(result['title'])}</h2><p class='status'>{esc(result['status'].replace('_',' '))} · {len(result['records'])} records</p><p>{esc(result['note'])}</p>"
        if result.get("error"): report += f"<p>{esc(result['error'])}</p>"
        if key in CONFIGURATIONS:
            for row in result["records"]:
                props = row.get("properties") or {}
                name = props.get("displayName") or row.get("name") or row.get("id") or "Configuration"
                enabled = props.get("enabled", props.get("isEnabled", (props.get("triggeringLogic") or {}).get("isEnabled")))
                state = "Enabled" if enabled is True else "Disabled" if enabled is False else "State not returned"
                report += f"<details><summary>{esc(name)} · {esc(row.get('kind', ''))} · {state}</summary><table>"
                labels = {"severity":"Severity", "queryFrequency":"Run every", "queryPeriod":"Lookback", "triggerOperator":"Threshold operator", "triggerThreshold":"Threshold", "suppressionEnabled":"Suppression enabled", "suppressionDuration":"Suppression duration", "order":"Execution order"}
                for field, label in labels.items():
                    if field in props: report += f"<tr><th>{label}</th><td>{esc(props[field])}</td></tr>"
                if key in ("connectors", "settings"):
                    for field, value in props.items():
                        label = re.sub(r"(?<!^)(?=[A-Z])", " ", field).capitalize()
                        display = json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                        report += f"<tr><th>{esc(label)}</th><td><pre>{esc(display)}</pre></td></tr>"
                report += "</table>"
                if "query" in props: report += f"<h3>Rule query</h3><pre>{esc(props['query'])}</pre>"
                for field, label in (("incidentConfiguration","Incident and grouping settings"),("triggeringLogic","Triggers and conditions"),("actions","Actions and playbook references")):
                    if field in props: report += f"<h3>{label}</h3><pre>{esc(json.dumps(props[field],indent=2,ensure_ascii=False))}</pre>"
                report += f"<details><summary>All returned configuration fields</summary><pre>{esc(json.dumps(row,indent=2,ensure_ascii=False))}</pre></details></details>"
        else:
            report += f"<p>Open <a href='{esc(key)}.json'>JSON with queries and collection details</a>"
            if result["records"]: report += f" or <a href='{esc(key)}.csv'>CSV records</a>"
            report += ". Log payloads are kept out of this summary.</p>"
        report += "</section>"
    return report+"<p>manifest.json records the selections, identity, requests and SHA-256 file hashes. API snapshots do not reproduce every portal screen. Collection success is not a compliance determination.</p></main></html>"
