"""Coverage, bounded collection and evidence-based findings tests."""
import copy
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import audit_analysis as analysis
from full_onboarding import Stop as LegacyAnalysisStop
import audit_evidence as audit
from desktop_backend import Stop
from test_audit_evidence import FakeSession
from test_desktop import WORKSPACE

START=datetime(2024,1,1,tzinfo=timezone.utc)
END=START+timedelta(days=2)


def response(rows, error=None):
    keys=list(rows[0]) if rows else ["TimeGenerated"]
    result=dict(tables=[dict(name="PrimaryResult",columns=[dict(name=k) for k in keys],rows=[[row.get(k) for k in keys] for row in rows])])
    if error:result["error"]=dict(code=error)
    return result


def evidence(key, records=None, status="collected", **extra):
    return dict(key=key,title=key,status=status,records=records or [],note="Test",**extra)


def log_evidence(rows=None, segments=None, **extra):
    return evidence("health",rows,segments=segments if segments is not None else [dict(start_utc=START.isoformat(),end_utc_exclusive=END.isoformat(),status="collected")],
                    table="SentinelHealth",record_kind="events",period_end_utc_exclusive=END.isoformat(),**extra)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.session=FakeSession()
        self.collector=audit.Collector(self.session,WORKSPACE)

    def collect(self,summary=False):
        return self.collector.collect_log("health","Health","SentinelHealth",START,END,summary)

    def test_oversize_parent_is_replaced_by_disjoint_children(self):
        row1={"TimeGenerated":"2024-01-01T12:00:00Z"}
        row2={"TimeGenerated":"2024-01-02T12:00:00Z"}
        with patch.object(audit,"LOG_LIMIT",1),patch.object(self.session,"az",side_effect=[response([row1,row2]),response([row1]),response([row2])]):
            result=self.collect()
        self.assertEqual(result["records"],[row1,row2])
        self.assertEqual(result["status"],"collected")
        self.assertTrue(result["requests"][0]["superseded_by_split"])
        self.assertEqual(result["segments"][0]["end_utc_exclusive"],result["segments"][1]["start_utc"])
        self.assertEqual(len(result["requests"]),3)

    def test_partial_error_retried_then_recovers(self):
        with patch.object(self.session,"az",side_effect=[response([],"PartialError"),response([]),response([])]):result=self.collect()
        self.assertEqual(result["status"],"no_records")
        self.assertEqual(len(result["segments"]),2)

    def test_denied_query_is_not_repeated(self):
        with patch.object(self.session,"az",side_effect=Stop("403 Forbidden")) as az:result=self.collect()
        self.assertEqual(result["status"],"access_denied")
        self.assertEqual(az.call_count,1)

    def test_missing_table_is_not_repeated(self):
        with patch.object(self.session,"az",side_effect=Stop("Failed to resolve table")) as az:result=self.collect()
        self.assertEqual(result["status"],"unavailable")
        self.assertEqual(az.call_count,1)

    def test_request_budget_retains_unqueried_interval(self):
        rows=[{"TimeGenerated":"2024-01-01T01:00:00Z"},{"TimeGenerated":"2024-01-02T01:00:00Z"}]
        with patch.object(audit,"LOG_LIMIT",1),patch.object(audit,"MAX_LOG_REQUESTS",2),patch.object(self.session,"az",side_effect=[response(rows),response(rows[:1])]):result=self.collect()
        self.assertEqual(result["status"],"partial")
        self.assertEqual(len(result["requests"]),2)
        self.assertEqual(result["segments"][-1]["status"],"not_queried")

    def test_record_budget_never_claims_complete(self):
        with patch.object(audit,"MAX_LOG_RECORDS",1),patch.object(self.session,"az",return_value=response([{"TimeGenerated":"2024-01-01T01:00:00Z"}]*2)):result=self.collect()
        self.assertEqual(len(result["records"]),1)
        self.assertEqual(result["status"],"partial")

    def test_minimum_window_stops_splitting(self):
        with patch.object(audit,"MIN_LOG_WINDOW",timedelta(days=3)),patch.object(self.session,"az",return_value=response([],"PartialError")) as az:result=self.collect()
        self.assertEqual(az.call_count,1)
        self.assertEqual(result["status"],"partial")

    def test_failed_child_does_not_discard_successful_child(self):
        row={"TimeGenerated":"2024-01-01T01:00:00Z"}
        with patch.object(self.session,"az",side_effect=[response([],"PartialError"),response([row]),Stop("network error")]):result=self.collect()
        self.assertEqual(result["records"],[row])
        self.assertEqual(result["status"],"partial")

    def test_timeout_is_bounded_and_retried(self):
        with patch.object(self.session,"az",side_effect=[Stop("operation timed out"),response([]),response([])]):result=self.collect()
        self.assertEqual(result["status"],"no_records")
        self.assertEqual(len(result["requests"]),3)

    def test_selected_period_tables_export_events(self):
        results=self.collector.run("2024-01-01","2024-01-02",dict(source_tables=["Heartbeat"],source_mode="period"))
        result=results[-1]
        self.assertEqual(result["table"],"Heartbeat")
        self.assertNotIn("summarize",result["requests"][0]["query"])
        self.assertIn("order by TimeGenerated asc",result["requests"][0]["query"])

    def test_invalid_source_table_stops_before_network(self):
        with self.assertRaises(Stop):self.collector.run("2024-01-01","2024-01-02",dict(source_tables=["Heartbeat | take 1"]))
        self.assertFalse(self.session.calls)


class AnalysisTests(unittest.TestCase):
    def analyze(self,results,requirements=None):
        return analysis.analyze(results,"2024-01-01","2024-01-02",requirements)

    def test_quiet_day_is_not_a_confirmed_outage(self):
        report=self.analyze([log_evidence([{"TimeGenerated":"2024-01-02T12:00:00Z"}])])
        item=report["coverage"][0]
        self.assertEqual(item["no_record_days"],1)
        self.assertEqual(item["complete_query_days"],2)
        self.assertFalse(any(f["classification"]=="Observed issue" for f in report["findings"]))
        self.assertEqual(item["first_record_utc"],"2024-01-02T12:00:00+00:00")

    def test_failed_interval_is_unknown_not_empty(self):
        log=log_evidence(segments=[dict(start_utc=START.isoformat(),end_utc_exclusive=END.isoformat(),status="access_denied")])
        item=self.analyze([log])["coverage"][0]
        self.assertEqual(item["incomplete_days"],2)
        self.assertEqual(item["no_record_days"],0)

    def test_gap_between_successful_segments_is_incomplete(self):
        log=log_evidence(segments=[dict(start_utc=START.isoformat(),end_utc_exclusive=(START+timedelta(hours=5)).isoformat(),status="collected"),dict(start_utc=(START+timedelta(hours=6)).isoformat(),end_utc_exclusive=END.isoformat(),status="collected")])
        item=self.analyze([log])["coverage"][0]
        self.assertEqual([d["query_complete"] for d in item["days"]],[False,True])

    def test_current_day_uses_collection_cutoff_not_analysis_time(self):
        current=datetime.now(timezone.utc)
        start=current.replace(hour=0,minute=0,second=0,microsecond=0)
        log=log_evidence(segments=[dict(start_utc=start.isoformat(),end_utc_exclusive=current.isoformat(),status="no_records")])
        log["period_end_utc_exclusive"]=current.isoformat()
        item=analysis.coverage_for(log,start.date().isoformat(),start.date().isoformat(),current+timedelta(seconds=2))
        self.assertTrue(item["days"][0]["current_day"])
        self.assertTrue(item["days"][0]["query_complete"])

    def test_invalid_dates_are_not_silently_ignored(self):
        item=self.analyze([log_evidence([{"TimeGenerated":"nonsense"}])])["coverage"][0]
        self.assertEqual(item["invalid_date_records"],1)
        self.assertEqual(item["incomplete_days"],2)

    def test_summary_fragments_sum_without_double_counting(self):
        log=log_evidence([dict(Day="2024-01-01T00:00:00Z",Records=3,FirstRecord="2024-01-01T01:00:00Z",LastRecord="2024-01-01T02:00:00Z"),dict(Day="2024-01-01T00:00:00Z",Records=4,FirstRecord="2024-01-01T13:00:00Z",LastRecord="2024-01-01T14:00:00Z")])
        log["record_kind"]="daily_summary"
        item=self.analyze([log])["coverage"][0]
        self.assertEqual(item["days"][0]["records"],7)

    def test_disabled_rule_without_requirement_is_review(self):
        result=self.analyze([evidence("rules",[dict(properties=dict(enabled=False,displayName="Example"))])])
        finding=next(f for f in result["findings"] if f["title"]=="Disabled analytics rules")
        self.assertEqual(finding["classification"],"Needs review")
        self.assertEqual(finding["evidence"],[dict(category="rules",record_index=0)])

    def test_critical_disabled_rule_is_observed_issue(self):
        result=self.analyze([evidence("rules",[dict(name="r1",properties=dict(enabled=False,displayName="Critical"))])],dict(critical_rules=["r1"]))
        self.assertEqual(result["findings"][0]["classification"],"Observed issue")

    def test_ambiguous_critical_name_does_not_select_arbitrarily(self):
        rows=[dict(name=str(i),properties=dict(enabled=False,displayName="Duplicate")) for i in range(2)]
        result=self.analyze([evidence("rules",rows)],dict(critical_rules=["Duplicate"]))
        self.assertEqual(result["findings"][0]["classification"],"Insufficient evidence")

    def test_missing_critical_rule_is_not_definitive_absence(self):
        result=self.analyze([evidence("rules",status="no_records")],dict(critical_rules=["Expected"]))
        self.assertEqual(result["findings"][0]["classification"],"Needs review")

    def test_retention_needs_client_threshold(self):
        result=self.analyze([evidence("workspace",[dict(properties=dict(retentionInDays=7))])])
        self.assertFalse(any(f["classification"]=="Observed issue" for f in result["findings"]))

    def test_retention_inheritance_and_archive_are_distinct(self):
        rows=[evidence("workspace",[dict(properties=dict(retentionInDays=30))]),evidence("tables",[dict(name="Heartbeat",properties=dict(retentionInDays=-1,totalRetentionInDays=365))])]
        result=self.analyze(rows,dict(minimum_retention_days=90,expected_tables=["Heartbeat"]))
        issues=[f for f in result["findings"] if f["classification"]=="Observed issue"]
        self.assertEqual(len(issues),2)
        self.assertIn("30 days",issues[1]["observation"])

    def test_unknown_retention_is_insufficient_not_violation(self):
        result=self.analyze([evidence("workspace",[dict(properties={})])],dict(minimum_retention_days=90))
        self.assertEqual(result["findings"][0]["classification"],"Insufficient evidence")

    def test_health_failures_keep_source_refs(self):
        result=self.analyze([evidence("health",[dict(Status="Success"),dict(Status="Failure",SentinelResourceType="Data connector")])])
        item=next(f for f in result["findings"] if f["classification"]=="Observed issue")
        self.assertEqual(item["evidence"],[dict(category="health",record_index=1)])
        self.assertIn("not a current outage",item["observation"])

    def test_requirement_bounds_and_injection(self):
        for value in [dict(minimum_retention_days=True),dict(minimum_retention_days=0),dict(expected_tables=["x; print secret"]),dict(expected_tables=["x"]*21),dict(critical_rules=[""])]:
            with self.assertRaises(LegacyAnalysisStop):analysis.validate_expectations(value)

    def test_new_export_omits_legacy_analysis_and_escapes_configuration(self):
        results=[evidence("rules",[dict(properties=dict(enabled=False,displayName="<script>bad</script>"))])]
        with tempfile.TemporaryDirectory() as folder,patch.object(audit.Collector,"run",return_value=results):
            output=audit.collect_evidence(FakeSession(),WORKSPACE,"2024-01-01","2024-01-02",folder)
            path=Path(output["folder"])
            for name in ("findings.json","findings.csv","coverage.json","requirements.json","evidence.html"):
                self.assertFalse((path/name).exists())
            report=(path/"report.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>bad</script>",report)
            self.assertIn("&lt;script&gt;bad&lt;/script&gt;",report)
            self.assertNotIn("finding-filter",report)
            self.assertIn("Disabled",report)


if __name__=="__main__":unittest.main()
