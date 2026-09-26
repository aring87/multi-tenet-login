"""Deterministic evidence coverage and observations, never compliance scores."""
import html
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from full_onboarding import require

COMPLETE = {"collected", "no_records"}


def validate_expectations(value=None):
    value = value or {}
    require(isinstance(value, dict), "Invalid audit requirements.")
    days = value.get("minimum_retention_days")
    require(days is None or type(days) is int and 1 <= days <= 4383,
            "Minimum searchable retention must be a whole number from 1 to 4383, or blank.")
    result = {"minimum_retention_days": days}
    for field, limit in (("expected_tables", 20), ("critical_rules", 50)):
        values = value.get(field, [])
        require(isinstance(values, list) and len(values) <= limit, f"Use at most {limit} {field.replace('_',' ')}.")
        require(all(isinstance(x, str) and x.strip() and len(x) <= 256 for x in values), "Requirement names must be nonempty text, up to 256 characters.")
        values = list(dict.fromkeys(x.strip() for x in values))
        if field == "expected_tables":
            require(all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", x) for x in values),
                    "Expected log tables must be table names only, for example SecurityEvent or Heartbeat.")
        result[field] = values
    return result


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def coverage_for(result, start, end, now):
    first = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    after = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)+timedelta(days=1)
    cutoff = min(after, timestamp(result.get("period_end_utc_exclusive")) or now)
    daily = { (first+timedelta(days=i)).date().isoformat():0 for i in range((after-first).days) }
    observed, invalid = [], 0
    for row in result["records"]:
        if result.get("record_kind") == "daily_summary":
            day, low, high = timestamp(row.get("Day")), timestamp(row.get("FirstRecord")), timestamp(row.get("LastRecord"))
            count = row.get("Records")
            valid = (day and low and high and type(count) is int and count > 0 and low <= high
                     and first <= low <= high < cutoff and low.date() == high.date() == day.date())
        else:
            day = low = high = timestamp(row.get("TimeGenerated"))
            count = 1
            valid = day and first <= day < cutoff
        if not valid:
            invalid += 1
            continue
        daily[day.date().isoformat()] += count
        observed.extend([low, high])
    segments = result.get("segments", [])
    days = []
    for day, count in daily.items():
        lo = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        hi = min(lo+timedelta(days=1), cutoff)
        intervals = []
        for segment in segments:
            a,b = timestamp(segment.get("start_utc")),timestamp(segment.get("end_utc_exclusive"))
            if a and b and a < hi and b > lo and segment["status"] in COMPLETE:
                intervals.append((max(a,lo),min(b,hi)))
        cursor = lo
        for a,b in sorted(intervals):
            if a > cursor: break
            cursor = max(cursor,b)
        complete = hi > lo and cursor >= hi and not invalid
        state = "records_observed" if complete and count else "no_records" if complete else "incomplete"
        days.append(dict(date_utc=day, records=count, state=state,
                         query_complete=complete, current_day=lo.date()==now.date()))
    return dict(key=result["key"], title=result["title"], table=result.get("table"),
                collection_status=result["status"], first_record_utc=min(observed).isoformat() if observed else None,
                last_record_utc=max(observed).isoformat() if observed else None,
                records_observed=sum(daily.values()), invalid_date_records=invalid,
                requested_days=len(days), days_with_records=sum(d["records"]>0 for d in days),
                complete_query_days=sum(d["query_complete"] for d in days),
                no_record_days=sum(d["state"]=="no_records" for d in days),
                incomplete_days=sum(d["state"]=="incomplete" for d in days), days=days,
                note="Counts describe returned records, not continuous monitoring. Current day is only queried through collection start. Usage counts are usage records, not source events.")


def analyze(results, start, end, expectations=None):
    expectations = validate_expectations(expectations)
    now = datetime.now(timezone.utc)
    coverage = [coverage_for(r,start,end,now) for r in results if "segments" in r]
    findings = []
    by_key = {r["key"]:r for r in results}

    def add(kind, title, observation, next_step, refs):
        findings.append(dict(id=f"F-{len(findings)+1:03}", classification=kind, title=title,
                             observation=observation, next_step=next_step, evidence=refs))

    def ref(key, index=None):
        return {"category":key,"record_index":index}

    for result in results:
        if result["status"] not in COMPLETE:
            add("Insufficient evidence", result["title"]+": collection incomplete",
                f"Collection status: {result['status']}. {result.get('error','')}",
                "Review the failed requests/intervals; check approved read access and available monitoring. Recollect or narrow dates.", [ref(result["key"])])
    for item in coverage:
        if item["invalid_date_records"]:
            add("Insufficient evidence",item["title"]+": unclassified record dates",
                f"{item['invalid_date_records']} records could not be assigned safely to the requested UTC dates.",
                "Review original records and queries before relying on the daily counts.",[ref(item["key"])])
        if item["no_record_days"]:
            add("Needs review",item["title"]+": days with no matching records",
                f"{item['no_record_days']} day(s) returned no matching records. Earliest observed: {item['first_record_utc'] or 'none'}. No cause is established.",
                "Compare with expected activity, retention, enabled monitoring and account visibility; absence alone does not establish an outage.",[ref(item["key"])])

    workspace = by_key.get("workspace",{}).get("records",[])
    workspace_days = workspace[0].get("properties",{}).get("retentionInDays") if workspace else None
    minimum = expectations["minimum_retention_days"]
    if minimum is not None:
        checks = [("Workspace default", workspace_days, [ref("workspace",0)] if workspace else [ref("workspace")])]
        tables = by_key.get("tables",{}).get("records",[])
        for name in expectations["expected_tables"]:
            matches = [(i,row) for i,row in enumerate(tables) if row.get("name","").rsplit("/",1)[-1] == name]
            if len(matches)!=1:
                add("Insufficient evidence",name+": retention not established", "A unique matching table setting was not returned.","Check table visibility and the exact table name.",[ref("tables")])
                continue
            index,row = matches[0]
            props=row.get("properties",{})
            retention=props.get("retentionInDays")
            refs=[ref("tables",index)]
            if retention == -1 or props.get("retentionInDaysAsDefault") is True:
                retention=workspace_days
                refs.append(ref("workspace",0))
            checks.append((name,retention,refs))
        for name, actual, refs in checks:
            if type(actual) is not int or actual < 0:
                add("Insufficient evidence",name+": searchable retention unknown", f"Client minimum: {minimum} days; a usable configured value was not returned.","Confirm current searchable retention in Azure. Archive/total retention is a separate setting.",refs)
            elif actual < minimum:
                add("Observed issue",name+": searchable retention below requirement",f"Current setting: {actual} days. Client-provided minimum: {minimum} days. This is a configuration comparison, not proof of historical data availability.","Review the client requirement and retention settings through the normal change process.",refs)

    rule_result=by_key.get("rules",{})
    rules=rule_result.get("records",[])
    critical_indexes=set()
    for wanted in expectations["critical_rules"]:
        matches=[(i,r) for i,r in enumerate(rules) if wanted.casefold() in
                 {str(r.get("id","")).casefold(),str(r.get("name","")).casefold(),str(r.get("properties",{}).get("displayName","")).casefold()}]
        if len(matches)!=1:
            add("Needs review" if not matches and rule_result.get("status") in COMPLETE else "Insufficient evidence",
                "Critical rule not uniquely identified: "+wanted,
                f"{len(matches)} matching rules returned. Absence from the returned inventory does not establish absence from the workspace.",
                "Use the exact rule ID or unique display name and verify account visibility.",[ref("rules")])
            continue
        index,row=matches[0];critical_indexes.add(index)
        enabled=row.get("properties",{}).get("enabled")
        if enabled is not True:
            add("Observed issue" if enabled is False else "Insufficient evidence",
                "Critical rule disabled: "+wanted if enabled is False else "Critical rule enabled state unknown: "+wanted,
                "The client requirement is that this rule is enabled. The snapshot reports "+str(enabled)+".",
                "Review whether an approved exception exists; investigate before making changes.",[ref("rules",index)])
    disabled=[i for i,r in enumerate(rules) if r.get("properties",{}).get("enabled") is False and i not in critical_indexes]
    if disabled:
        add("Needs review","Disabled analytics rules",f"{len(disabled)} rule(s) are explicitly disabled. Disabled rules may be intentional; no universal requirement is assumed.","Review the linked rule records with the client's monitoring owner.",[ref("rules",i) for i in disabled])

    health=by_key.get("health",{}).get("records",[])
    failures=[i for i,r in enumerate(health) if str(r.get("Status","")).casefold() in ("failure","failed","partial success")]
    if failures:
        add("Observed issue","Sentinel health operations reported failures",f"{len(failures)} returned health event(s) report Failure, Failed or Partial success. This describes events during the period, not a current outage.","Inspect resource type, timestamps, reason and description; confirm remediation or recovery.",[ref("health",i) for i in failures])
    if not any((minimum,expectations["expected_tables"],expectations["critical_rules"])):
        add("Needs review","Client requirements not supplied","No retention minimum, expected log tables or critical enabled rules were supplied.","Enter approved client requirements to enable targeted comparisons. Framework defaults are not inferred.",[])
    counts=Counter(f["classification"] for f in findings)
    return dict(coverage=coverage, findings=findings, summary=dict(
        findings=len(findings), observed_issues=counts["Observed issue"], needs_review=counts["Needs review"],
        insufficient_evidence=counts["Insufficient evidence"],
        categories=len(results), incomplete_categories=sum(r["status"] not in COMPLETE for r in results),
        sources_with_incomplete_days=sum(c["incomplete_days"]>0 for c in coverage)))


def evidence_href(ref):
    key=ref["category"]
    return f"evidence.html#{key}-{ref['record_index']}" if ref["record_index"] is not None else key+".json"


def render_evidence_links(results, findings):
    esc=lambda x:html.escape(str(x),quote=True)
    by_key={r["key"]:r for r in results}
    refs=sorted({(r["category"],r["record_index"]) for f in findings for r in f["evidence"] if r["record_index"] is not None})
    body="<!doctype html><html lang='en'><meta charset='utf-8'><title>Supporting evidence</title><style>body{font:16px Segoe UI;margin:32px;color:#233248}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f9;padding:20px}section{margin-bottom:40px}</style><h1>Supporting evidence</h1><p><a href='report.html'>Return to report</a>. Record indexes are zero-based positions in the category JSON records array.</p>"
    for key,index in refs:
        records=by_key.get(key,{}).get("records",[])
        if 0<=index<len(records):
            body+=f"<section id='{esc(key)}-{index}'><h2>{esc(key)} · record {index}</h2><a href='{esc(key)}.json'>Full source evidence and collection details</a><pre>{esc(json.dumps(records[index],indent=2,ensure_ascii=False))}</pre></section>"
    return body+"</html>"


def render_dashboard(assessment, expectations):
    esc=lambda x:html.escape(str(x),quote=True)
    s=assessment["summary"]
    body="""<style>.metrics{display:flex;flex-wrap:wrap;gap:12px}.metric{padding:18px;background:white;border:1px solid #d7dee8;border-radius:8px;min-width:150px}.metric b{display:block;font-size:28px}.timeline{display:flex;flex-wrap:wrap;gap:3px;margin:12px 0}.day{width:14px;height:20px;background:#dce3eb;border:1px solid #aab6c6}.day.records_observed{background:#83b8f2}.day.incomplete{background:#ffcc80}.day.current{border:2px dashed #7e55a9}.coverage{padding:20px;background:white;margin:16px 0;border:1px solid #d7dee8;border-radius:8px}details{margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}td{overflow-wrap:anywhere}.finding{border-left:4px solid #b38532;padding:14px;background:white;margin:12px 0}.finding h3{margin-top:4px}select{padding:8px;font:inherit}summary{cursor:pointer;color:#245de9}section{scroll-margin-top:20px}</style>
<nav><a href='#coverage'>Coverage</a> · <a href='#findings'>Findings</a> · <a href='requirements.json'>Client requirements</a> · <a href='findings.csv'>Findings CSV</a></nav><h2>Evidence review dashboard</h2><div class='metrics'>"""
    for label,key in [("Observed issues","observed_issues"),("Needs review","needs_review"),("Insufficient evidence","insufficient_evidence"),("Incomplete collections","incomplete_categories")]:
        body+=f"<div class='metric'><b>{s[key]}</b>{label}</div>"
    body+="</div><p>These are observations and evidence limitations, not control pass/fail results. Zero findings does not establish compliance or complete monitoring.</p>"
    body+=f"<details><summary>Requirements used for this collection</summary><p>Minimum searchable retention: {esc(expectations['minimum_retention_days'] or 'Not specified')} days (workspace default and expected tables only). Total/archive retention is not compared.</p><p>Expected log tables: {esc(', '.join(expectations['expected_tables']) or 'Not specified')}</p><p>Critical rules required enabled: {esc('; '.join(expectations['critical_rules']) or 'Not specified')}</p></details>"
    body+="<section id='coverage'><h2>Daily evidence coverage</h2><p>Blue: records observed. Gray: query completed with no matching records. Amber: collection incomplete or record dates invalid. Dashed: current UTC day, still in progress. Each square is one day; hover for detail. None of these states proves continuous monitoring or an outage.</p>"
    for item in assessment["coverage"]:
        body+=f"<article class='coverage'><h3>{esc(item['title'])}</h3><p>Collection: {esc(item['collection_status'].replace('_',' '))} · {item['days_with_records']} / {item['requested_days']} days with observed records · {item['incomplete_days']} incomplete days</p><p>First observed: {esc(item['first_record_utc'] or 'None')}<br>Last observed: {esc(item['last_record_utc'] or 'None')}</p><div class='timeline'>"
        for day in item["days"]:
            label=f"{day['date_utc']}: {day['state'].replace('_',' ')}, {day['records']} returned records"+("; current day so far" if day['current_day'] else "")
            body+=f"<span tabindex='0' role='img' aria-label='{esc(label)}' title='{esc(label)}' class='day {day['state']} {'current' if day['current_day'] else ''}'></span>"
        body+=f"</div><a href='{esc(item['key'])}.json'>Source records, queries and intervals</a><details><summary>Daily counts and collection status</summary><table><tr><th>UTC day</th><th>Returned records</th><th>Evidence state</th></tr>"
        body+="".join(f"<tr><td>{d['date_utc']}{' (so far)' if d['current_day'] else ''}</td><td>{d['records']}</td><td>{esc(d['state'].replace('_',' '))}</td></tr>" for d in item["days"])
        body+="</table></details></article>"
    body+="</section><section id='findings'><h2>Findings and review items</h2><label for='finding-filter'>Show: </label><select id='finding-filter'><option value='all'>All findings</option><option>Observed issue</option><option>Needs review</option><option>Insufficient evidence</option></select>"
    for f in assessment["findings"]:
        body+=f"<article class='finding' data-kind='{esc(f['classification'])}'><small>{f['id']} · {esc(f['classification'])}</small><h3>{esc(f['title'])}</h3><p>{esc(f['observation'])}</p><p><b>Next step:</b> {esc(f['next_step'])}</p>"
        if f["evidence"]:
            body+="<details><summary>Supporting evidence</summary><ul>"+"".join(f"<li><a href='{esc(evidence_href(r))}'>{esc(r['category'])}{' — record '+str(r['record_index']) if r['record_index'] is not None else ' — collection details'}</a></li>" for r in f["evidence"])+"</ul></details>"
        body+="</article>"
    if not assessment["findings"]:body+="<p>No observations generated by the implemented checks. Review collection scope and limitations.</p>"
    return body+"</section><script>document.getElementById('finding-filter').addEventListener('change',function(){document.querySelectorAll('.finding').forEach(el=>el.hidden=this.value!=='all'&&el.dataset.kind!==this.value);});</script>"
