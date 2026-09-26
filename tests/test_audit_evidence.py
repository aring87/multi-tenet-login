"""Offline evidence boundary, failure and export tests. No Azure calls."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_evidence as audit
from desktop_backend import Session, Stop
from test_desktop import WORKSPACE, ITEM


class FakeSession:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.cloud = "AzureCloud"
        self.workspace = dict(id=ITEM["id"], properties={"customerId": ITEM["customerId"], "retentionInDays": 30})
        self.verifications = 0

    def log(self, message):
        pass

    def verify(self, workspace):
        self.verifications += 1
        if self.fail == "verify":
            raise Stop("Session/destination mismatch")

    def az(self, *args):
        self.calls.append(args)
        if args == ("cloud", "show"):
            return dict(name=self.cloud)
        assert args[:3] == ("rest", "--method", "get"), args
        url = args[args.index("--url")+1]
        if "?api-version=2025-07-01" in url and "/tables" not in url:
            return copy.deepcopy(self.workspace)
        if self.fail and self.fail in url:
            raise Stop("AuthorizationFailed: 403")
        if "/query?" in url:
            return dict(tables=[dict(name="PrimaryResult", columns=[dict(name="TimeGenerated")], rows=[])])
        return dict(value=[])


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.session = FakeSession()
        self.collector = audit.Collector(self.session, WORKSPACE)
        self.url = self.collector.base+"/tables?api-version=2025-07-01"

    def result(self, **kwargs):
        return self.collector.collect("sample", "Sample", self.url, "Test", **kwargs)

    def test_date_bounds_and_leap_day(self):
        self.assertEqual(audit.date_range("2024-02-29", "2024-02-29"),
                         ("2024-02-29T00:00:00Z", "2024-03-01T00:00:00Z"))
        for start, end in [("invalid", "2024-01-01"), ("2024-02-01", "2024-01-01"),
                           ("2020-01-01", "2024-01-01"), ("2099-01-01", "2099-01-02")]:
            with self.assertRaises(Stop): audit.date_range(start, end)

    def test_read_only_calls_and_incident_period(self):
        results = self.collector.run("2024-01-01", "2024-01-31")
        self.assertEqual(len(results), 10)
        self.assertEqual(self.session.verifications, 2)
        for call in self.session.calls:
            if call[0] == "rest": self.assertEqual(call[2], "get")
        incident = next(r for r in results if r["key"] == "incidents")
        clause = parse_qs(urlsplit(incident["source"]).query)["$filter"][0]
        self.assertIn("createdTimeUtc ge 2024-01-01T00:00:00Z", clause)
        self.assertIn("createdTimeUtc lt 2024-02-01T00:00:00Z", clause)

    def test_wrong_session_stops_before_requests(self):
        self.session.fail = "verify"
        with self.assertRaises(Stop): self.collector.run("2024-01-01", "2024-01-02")
        self.assertEqual(self.session.calls, [])

    def test_wrong_workspace_stops_before_export(self):
        self.session.workspace["properties"]["customerId"] = "44444444-4444-4444-8444-444444444444"
        with self.assertRaises(Stop): audit.collect_evidence(self.session, WORKSPACE, "2024-01-01", "2024-01-02", self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_other_cloud_stops_before_rest(self):
        self.session.cloud = "AzureUSGovernment"
        with self.assertRaises(Stop): self.collector.run("2024-01-01", "2024-01-02")
        self.assertEqual(self.session.calls, [("cloud", "show")])

    def test_pagination_retains_all_pages(self):
        link = self.url+"&skipToken=next"
        with patch.object(self.session, "az", side_effect=[dict(value=[{"id":"a"}], nextLink=link), dict(value=[{"id":"b"}])]):
            result = self.result()
        self.assertEqual(result["records"], [{"id":"a"}, {"id":"b"}])
        self.assertEqual(result["status"], "collected")
        self.assertEqual(len(result["requests"]), 2)

    def test_late_page_failure_is_partial(self):
        with patch.object(self.session, "az", side_effect=[dict(value=[{"id":"a"}], nextLink=self.url+"&page=2"), Stop("403 Forbidden")]):
            result = self.result()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["records"]), 1)

    def test_pagination_cannot_escape_scope(self):
        for link in ["https://evil.example/test", self.url.replace("law-example-sentinel", "other"), "http://management.azure.com/test"]:
            with patch.object(self.session, "az", return_value=dict(value=[{"id":"a"}], nextLink=link)) as api:
                result = self.result()
                self.assertEqual(api.call_count, 1)
                self.assertEqual(result["status"], "partial")

    def test_pagination_loop_is_partial(self):
        with patch.object(self.session, "az", return_value=dict(value=[{"id":"a"}], nextLink=self.url)):
            self.assertEqual(self.result()["status"], "partial")

    def test_page_limit_is_partial(self):
        with patch.object(audit,"MAX_PAGES",1), patch.object(self.session, "az", return_value=dict(value=[{"id":"a"}],nextLink=self.url+"&page=2")):
            self.assertEqual(self.result()["status"], "partial")

    def test_denied_empty_and_unavailable_are_distinct(self):
        with patch.object(self.session, "az", return_value={"value":[]}):
            self.assertEqual(self.result()["status"], "no_records")
        for error, status in [("403 Forbidden", "access_denied"), ("404 NotFound", "unavailable"), ("network failure", "error")]:
            with patch.object(self.session, "az", side_effect=Stop(error)):
                self.assertEqual(self.result()["status"], status)

    def test_malformed_response_is_not_no_records(self):
        with patch.object(self.session, "az", return_value={}):
            self.assertEqual(self.result()["status"], "error")

    def test_query_partial_error_preserves_rows(self):
        response = dict(tables=[dict(name="PrimaryResult", columns=[dict(name="id")], rows=[[1]])], error=dict(code="PartialError"))
        with patch.object(self.session,"az",return_value=response):
            result = self.result(query="Test")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["records"], [{"id":1}])

    def test_query_limit_is_explicit(self):
        response = dict(tables=[dict(name="PrimaryResult", columns=[dict(name="id")], rows=[[1],[2]])])
        with patch.object(audit,"LOG_LIMIT",1),patch.object(self.session,"az",return_value=response):
            result=self.result(query="Test")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["records"]), 1)

    def test_missing_query_table_is_error(self):
        with patch.object(self.session,"az",return_value=dict(tables=[])):
            self.assertEqual(self.result(query="Test")["status"],"error")

    def test_collection_continues_after_denied_category(self):
        self.session.fail = "/alertRules"
        results = self.collector.run("2024-01-01", "2024-01-02")
        self.assertEqual(next(r for r in results if r["key"]=="rules")["status"], "access_denied")
        self.assertEqual(results[-1]["key"], "usage")

    def test_export_hashes_scope_and_unique_packages(self):
        first = audit.collect_evidence(self.session,WORKSPACE,"2024-01-01","2024-01-02",self.folder)
        second = audit.collect_evidence(self.session,WORKSPACE,"2024-01-01","2024-01-02",self.folder)
        self.assertNotEqual(first["folder"], second["folder"])
        package=Path(first["folder"])
        manifest=json.loads((package/"manifest.json").read_text())
        self.assertEqual(manifest["workspace"],WORKSPACE)
        for name,digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((package/name).read_bytes()).hexdigest(),digest)
        self.assertTrue((package/"workspace.csv").exists())
        self.assertIn("no records",(package/"report.html").read_text())

    def test_csv_formula_protection(self):
        for value in ("=1+1", " +SUM(A1)", "@evil", "-1+2", "\tbad"):
            self.assertTrue(audit.csv_value(value).startswith("'"))
        self.assertEqual(audit.csv_value("normal"),"normal")

    def test_html_escapes_cloud_text(self):
        self.session.workspace["tags"]={"test":"=1+1"}
        bad=dict(WORKSPACE,workspace_name="<script>alert(1)</script>")
        with patch.object(audit.Collector,"run",return_value=[dict(key="workspace",title="<script>bad</script>",status="collected",note="<img src=x>",records=[{"name":"=1+1"}])]):
            result=audit.collect_evidence(self.session,bad,"2024-01-01","2024-01-02",self.folder)
        self.assertNotIn("<script>",(Path(result["folder"])/"report.html").read_text())
        self.assertIn("'=1+1",(Path(result["folder"])/"workspace.csv").read_text(encoding="utf-8-sig"))

    def test_msi_azure_cli_bypasses_cmd_with_literal_url(self):
        session=Session(self.folder/"session")
        session.az_exe="C:/Azure/wbin/az.cmd"
        url=self.url+"&skipToken=abc%20x"
        with patch.object(Path,"is_file",return_value=True),patch("desktop_backend.subprocess.run",return_value=subprocess.CompletedProcess([],0,"{}","")) as run:
            session.az("rest","--method","get","--url",url)
        args=run.call_args.args[0]
        self.assertEqual(Path(args[0]).name,"python.exe")
        self.assertEqual(args[1:3],["-IBm","azure.cli"])
        self.assertIn(url,args)
        self.assertFalse(run.call_args.kwargs["shell"])


if __name__ == "__main__": unittest.main()
