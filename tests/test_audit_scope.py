"""Selected exports, bounded samples and source inventory: no cloud calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from sentinel_app import audit_evidence as audit
from sentinel_app.desktop_backend import Stop
from test_audit_evidence import FakeSession
from test_desktop import WORKSPACE


def response(rows, error=None):
    columns = list(rows[0]) if rows else ["TimeGenerated"]
    value = dict(tables=[dict(name="PrimaryResult", columns=[dict(name=c) for c in columns],
                              rows=[[r[c] for c in columns] for r in rows])])
    if error: value["error"] = dict(code=error)
    return value


class ScopeTests(unittest.TestCase):
    def test_invalid_selections_stop_before_cloud_calls(self):
        for options in (dict(source_tables=["SigninLogs | take 10"]), dict(source_tables=["X"]*21),
                        dict(sample_limit=True), dict(sample_limit=1001), dict(audit="true"),
                        dict(configurations=["access"]), dict(configurations=[]), dict(source_mode="all")):
            session=FakeSession()
            with self.assertRaises(Stop): audit.Collector(session,WORKSPACE).run("2024-01-01","2024-01-02",options)
            self.assertFalse(session.calls)

    def test_selected_activity_only_and_exact_period(self):
        session=FakeSession()
        results=audit.Collector(session,WORKSPACE).run("2024-01-01","2024-01-02",dict(configurations=[],audit=True))
        self.assertEqual([r["key"] for r in results],["audit"])
        query=results[0]["requests"][0]["query"]
        self.assertIn("SentinelAudit | where",query)
        self.assertIn("2024-01-03T00:00:00+00:00",query)
        self.assertNotIn("SentinelHealth",str(session.calls))

    def test_source_sample_is_labeled_and_bounded(self):
        session=FakeSession();original=session.az
        def fake(*args):
            if "/query?" in str(args): return response([dict(TimeGenerated="2024-01-02T12:00:00Z",Message="example")])
            return original(*args)
        with patch.object(session,"az",side_effect=fake):
            result=audit.Collector(session,WORKSPACE).run("2024-01-01","2024-01-02",dict(configurations=[],source_tables=["SigninLogs"],sample_limit=7))[0]
        self.assertEqual(result["status"],"sampled")
        self.assertEqual(result["sample_limit"],7)
        self.assertIn("top 7 by TimeGenerated desc",result["query"])
        self.assertIn("not a complete period",result["note"])
        self.assertEqual(result["records"][0]["Message"],"example")

    def test_partial_sample_is_not_labeled_complete_or_sampled(self):
        session=FakeSession();collector=audit.Collector(session,WORKSPACE)
        with patch.object(collector,"get",return_value=response([dict(TimeGenerated="2024-01-01")],"PartialError")):
            from datetime import datetime, timezone
            now=datetime.now(timezone.utc)
            result=collector.sample("source_0","SigninLogs",now,now,5)
        self.assertEqual(result["status"],"partial")

    def test_inventory_does_not_query_events(self):
        session=FakeSession()
        audit.list_log_tables(session,WORKSPACE)
        self.assertNotIn("/query?",str(session.calls))
        self.assertEqual(session.verifications,2)

    def test_preview_queries_only_selected_tables_and_retains_denials(self):
        session=FakeSession();session.fail="/query?"
        results=audit.preview_log_tables(session,WORKSPACE,"2024-01-01","2024-01-02",["SigninLogs"])
        self.assertEqual(results[0]["status"],"access_denied")
        self.assertEqual(results[0]["records"],[])
        query=results[0]["query"]
        self.assertIn("summarize Records=count(), LatestEvent=max(TimeGenerated)",query)
        self.assertNotIn("union",query)

    def test_preview_rejects_injection_before_verification(self):
        session=FakeSession()
        with self.assertRaises(Stop):audit.preview_log_tables(session,WORKSPACE,"2024-01-01","2024-01-02",["X; print 1"])
        self.assertEqual(session.verifications,0)

    def test_export_contains_only_selected_raw_records_and_provenance(self):
        session=FakeSession();original=session.az
        rule=dict(name="rule-1",properties=dict(displayName="Example",enabled=False,query="SecurityEvent",queryFrequency="PT1H"))
        def fake(*args):
            if "/alertRules?" in str(args): return dict(value=[rule])
            return original(*args)
        with tempfile.TemporaryDirectory() as folder,patch.object(session,"az",side_effect=fake):
            output=audit.collect_evidence(session,WORKSPACE,"ignored","ignored",folder,dict(configurations=["rules"]))
            path=Path(output["folder"])
            self.assertEqual(path.parent.name,WORKSPACE["workspace_id"])
            self.assertEqual(path.parent.parent.name,WORKSPACE["tenant_id"])
            self.assertEqual({p.name for p in path.iterdir()},{"rules.json","rules.csv","report.html","manifest.json"})
            self.assertEqual(json.loads((path/"rules.json").read_text())["records"],[rule])
            report=(path/"report.html").read_text(encoding="utf-8")
            for text in ("Example","Disabled","SecurityEvent","PT1H"):self.assertIn(text,report)
            self.assertIsNone(output["manifest"]["period"])

    def test_invalid_log_dates_are_ignored_only_without_logs(self):
        session=FakeSession()
        with self.assertRaises(Stop):audit.Collector(session,WORKSPACE).run("invalid","invalid",dict(health=True))
        self.assertFalse(session.calls)


if __name__ == "__main__": unittest.main()
