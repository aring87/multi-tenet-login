"""The offline KQL check: what it reports, where, and what it leaves alone. Nothing here calls Azure."""
import unittest

from sentinel_app import kql_check as kql

# Realistic rule queries the check must pass without a word (shapes taken from common Sentinel rules).
CLEAN = (
    'SigninLogs\n| where ResultType != "0"\n| summarize Attempts = count() by UserPrincipalName',
    "SecurityEvent\n| where EventID == 4625\n| summarize count() by Account, bin(TimeGenerated, 1h)\n"
    "| where count_ > 10\n| sort by count_ desc",
    'let threshold = 20;\nlet window = 1h;\nlet failures = SigninLogs\n| where TimeGenerated > ago(window)\n'
    '| where ResultType in ("50126", "50053");\nfailures\n| summarize Failed = dcount(IPAddress) by UserPrincipalName\n'
    '| where Failed > threshold',
    "union isfuzzy=true withsource=Source SigninLogs, AADNonInteractiveUserSignInLogs\n| where ResultType == \"0\"\n"
    "| project TimeGenerated, Source, UserPrincipalName",
    'AuditLogs\n| where OperationName has_any ("Add member to role", "Add eligible member to role")\n'
    '| mv-expand TargetResources\n| extend Role = tostring(TargetResources.displayName)\n'
    '| join kind=inner (IdentityInfo | summarize arg_max(TimeGenerated, *) by AccountUPN) on $left.Role == $right.AccountUPN\n'
    '| project-away TimeGenerated1',
    "SigninLogs\n| lookup kind=leftouter IdentityInfo on $left.UserPrincipalName == $right.AccountUPN\n| take 10",
    'let f = (user:string) { SigninLogs | where UserPrincipalName =~ user };\nf("a@example.com") | take 1',
    "Custom_CL\n| where Message_s contains \"error\" // a comment with \"quotes\", | pipes and (brackets\n"
    "| invoke _GetWatchlist('VIPs')\n| evaluate bag_unpack(Properties)",
    "imAuthentication(starttime=ago(1h))\n| where EventResult == 'Failure'",
    '_Im_NetworkSession\n| where DstIpAddr in (materialize(ThreatIntelligenceIndicator | where Active == true '
    '| distinct NetworkIP))\n| top 10 by EventCount',
    "DeviceTvmSoftwareVulnerabilitiesKB\n| where CvssScore >= 9\n| project CveId",
    'print x = @"C:\\temp\\", y = h"secret", z = ```multi\nline | string (```\n| extend w = strcat(x, "\\"quoted\\"")',
    "SecurityEvent | where TimeGenerated between (ago(2d) .. ago(1d)) and Account !has \"svc\" | count",
    "Syslog\n| where Facility == \"auth\" or SeverityLevel == \"err\"\n| make-series c = count() on TimeGenerated step 1h",
)


def messages(query, severity=None):
    return [str(issue) for issue in kql.check(query).issues if severity in (None, issue.severity)]


class KqlCheckTests(unittest.TestCase):
    def test_realistic_queries_pass_clean(self):
        for query in CLEAN:
            with self.subTest(query=query.splitlines()[0]):
                self.assertEqual(messages(query), [])

    def test_a_misspelt_table_points_at_the_one_meant(self):
        report = kql.check("SignLogs\n| where ResultType != 0\n| summarize Attempts = count() by UserPrincipalName")
        self.assertEqual([(i.severity, i.line, i.column, i.length) for i in report.issues], [("warning", 1, 0, 8)])
        self.assertIn("SignLogs isn't a known table. Did you mean SigninLogs?", report.issues[0].message)
        self.assertEqual(report.tables, ["SignLogs"])
        self.assertEqual(messages("SigninLogs | join (AuditLog | where x) on y"),
                         ["Line 1, column 20: AuditLog isn't a known table. Did you mean AuditLogs? "
                          "(Ignore this for a custom table or function.)"])
        self.assertEqual(messages("signinlogs | take 1"),
                         ["Line 1, column 1: Table names are case-sensitive in KQL: write SigninLogs."])

    def test_custom_tables_functions_and_unknown_names_are_left_alone(self):
        for query in ("MyCompany_CL | take 1", "imAuthentication | take 1", "_ASim_Dns | take 1",
                      "let SignLogs = SigninLogs; SignLogs | take 1", "BusinessApps | take 1",
                      "SecurityEventArchive | take 1", "T | take 1"):
            with self.subTest(query=query):
                self.assertEqual(messages(query), [])

    def test_steps_are_checked_after_every_pipe(self):
        self.assertEqual(messages("SigninLogs\n| wher x == 1\n| Summarize count()\n| mv-expnd y\n| frobnicate"),
                         ["Line 2, column 3: KQL has no wher step. Did you mean where?",
                          "Line 3, column 3: KQL steps are lowercase: write summarize.",
                          "Line 4, column 3: KQL has no mv-expnd step. Did you mean mv-expand?",
                          "Line 5, column 3: KQL has no frobnicate step."])
        self.assertEqual(messages("T | where (x in (T2 | projet a))"),
                         ["Line 1, column 23: KQL has no projet step. Did you mean project?"])

    def test_common_slips_in_where(self):
        self.assertEqual(messages("T | where a = 1 && b == 2 || c"),
                         ["Line 1, column 13: Use == to compare in where (= names a column).",
                          "Line 1, column 17: KQL uses and / or, not &&.",
                          "Line 1, column 27: KQL uses and / or, not ||."])
        self.assertEqual(messages("T | summarize n = count() | extend x = 1 | where n == 1"), [])

    def test_operators_spelt_as_words_are_checked_between_two_values_in_where(self):
        self.assertEqual(messages('T | where Name contians "a" AND Other !Has "b" or Third startwith "c"'),
                         ["Line 1, column 16: KQL has no contians operator. Did you mean contains?",
                          "Line 1, column 29: KQL operators are lowercase: write and.",
                          "Line 1, column 40: KQL operators are lowercase: write has.",
                          "Line 1, column 57: KQL has no startwith operator. Did you mean startswith?"])
        # A step misspelt is read as the one meant, so the slips inside it show in the same pass.
        self.assertEqual([issue.fix for issue in kql.check('T | wher a contians "b" && c = 1').issues],
                         ["where", "contains", "and", "=="])
        # Names where a value goes, and words between values outside where, are the analyst's own.
        for query in ('T | where name has "x" and other startswith "y" and contents == 1',
                      'T | where matched == true and likes > 2 and has_any_flag',
                      'T | parse Message with * "user=" contain " " *', "T | project contians, startwith",
                      'T | extend x = iff(a, "b", "c") | sort by contians desc'):
            with self.subTest(query=query):
                self.assertEqual(messages(query), [])

    def test_functions_a_letter_off_or_in_capitals_point_at_the_one_meant(self):
        self.assertEqual(messages("T | extend a = tostrng(x), b = ToLower(y)\n| summarize dcont(a), Count() by bin(t, 1h)"),
                         ["Line 1, column 16: KQL has no tostrng function. Did you mean tostring? "
                          "(Ignore this for a function of your own.)",
                          "Line 1, column 32: KQL functions are lowercase: write tolower.",
                          "Line 2, column 13: KQL has no dcont function. Did you mean dcount? "
                          "(Ignore this for a function of your own.)",
                          "Line 2, column 23: KQL functions are lowercase: write count."])
        # The analyst's own functions, parsers, plugins and anything not close to a built-in are left alone.
        for query in ("T | extend x = GetUsers(1), y = my_helper(a), z = Enrich(b)", "T | invoke _MyParser(x)",
                      "let tostrng = (a:string) { a }; T | extend x = tostrng(y)", "T | extend x = a.tostrng(1)",
                      "T | evaluate my_plugin(x)", "T | extend x = notempty(a), y = agg(b)",
                      "T | partition by Account ( top 1 by x )"):
            with self.subTest(query=query):
                self.assertEqual(messages(query), [])

    def test_most_warnings_carry_the_spelling_they_suggest(self):
        fixes = lambda query: [(issue.column, issue.length, issue.fix) for issue in kql.check(query).issues]
        self.assertEqual(fixes("SignLogs | take 1"), [(0, 8, "SigninLogs")])
        self.assertEqual(fixes("signinlogs | Take 1 | sumarize count()"),
                         [(0, 10, "SigninLogs"), (13, 4, "take"), (22, 8, "summarize")])
        self.assertEqual(fixes("T | where a = 1 && b"), [(12, 1, "=="), (16, 2, "and")])
        # and / or get the spaces && and || were written without.
        self.assertEqual(fixes('T | where a=="x"||b=="y"&& c'), [(16, 2, " or "), (24, 2, " and")])
        report = kql.check('T | frobnicate | where a == "open')  # Nothing to suggest for these.
        self.assertEqual([issue.fix for issue in report.issues], [None, None])
        self.assertEqual(report.fixes, [])
        self.assertEqual(len(kql.check("T | wher a | Take 1").fixes), 2)

    def test_the_report_keeps_what_the_walk_learned(self):
        query = "let n = 5;\nSigninLogs // all\n| mv-expand x\n| join (AuditLogs) on y\n| take n"
        report = kql.check(query)
        self.assertEqual(report.text, query)
        self.assertEqual([query[start:end] for start, end in report.comments], ["// all"])
        self.assertEqual(report.lets, {"n"})
        self.assertEqual([(kind, query[start:end]) for kind, start, end in report.marks],
                         [("step", "let"), ("table", "SigninLogs"), ("step", "mv-expand"), ("step", "join"),
                          ("table", "AuditLogs"), ("step", "take")])
        self.assertEqual("".join(token.text for token in report.tokens), "".join(query.replace("// all", "").split()))
        self.assertEqual(kql.check("a\r\nb").text, "a\nb")

    def test_a_table_alone_in_a_joins_brackets_and_steps_inside_a_steps_brackets_are_read(self):
        self.assertEqual(kql.check("SigninLogs | lookup (IdentityInfo) on a | join kind=inner (AuditLogs) on b").tables,
                         ["SigninLogs", "IdentityInfo", "AuditLogs"])
        self.assertEqual(messages("union (SignLogs), (AuditLogs)"),
                         ["Line 1, column 8: SignLogs isn't a known table. Did you mean SigninLogs? "
                          "(Ignore this for a custom table or function.)"])
        self.assertEqual(kql.check("T | where x in (SignLogs) | extend y = (AuditLog)").tables, ["T"])  # Values, not tables.
        # mv-apply, fork and partition hold steps with no | before the first one.
        query = "T | mv-apply p on (where p.a = 1 | mv-expand p) | fork (summarize count()) (take 1) | partition by A (top 1 by B)"
        report = kql.check(query)
        self.assertEqual([query[start:end] for kind, start, end in report.marks if kind == "step"],
                         ["mv-apply", "where", "mv-expand", "fork", "summarize", "take", "partition", "top"])
        self.assertEqual([str(issue) for issue in report.issues],
                         ["Line 1, column 30: Use == to compare in where (= names a column)."])
        self.assertEqual(messages("T | where (take > 1) | extend x = (count)"), [])  # Brackets of any other step.

    def test_what_kql_always_rejects_is_an_error_with_its_place(self):
        cases = {
            'T | where a == "open': ['Line 1, column 16: This string isn\'t closed. Add the closing " on the same line.'],
            "T | where (a == 1": ["Line 1, column 11: This ( is never closed. Add )."],
            "T | where a == 1)": ["Line 1, column 17: This ) has no ( before it."],
            "T | where a in [1, 2)": ["Line 1, column 21: This ) closes the [ on line 1, column 16, which needs ]."],
            "| where x": ["Line 1, column 1: Nothing comes before this |. Start with a table, such as SigninLogs."],
            "T | where x |": ["Line 1, column 13: Nothing follows this |. Remove it or add a step, such as where."],
            "T\n| where x\n| | take 1": ["Line 3, column 1: Nothing follows this |. Remove it or add a step, such as where."],
            "print ```never closed": ["Line 1, column 7: This ``` string isn't closed."],
            "let x = | take 1": ["Line 1, column 9: Nothing comes before this |. Start with a table, such as SigninLogs."],
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(messages(query, "error"), expected)

    def test_issue_marks_stay_on_their_own_line(self):
        issue = kql.check('T | extend a = "open\n| take 1').issues[0]
        self.assertEqual((issue.line, issue.column, issue.length), (1, 15, 5))
        issue = kql.check("T\n|\n").issues[0]
        self.assertEqual((issue.line, issue.column, issue.length), (2, 0, 1))

    def test_windows_line_ends_count_like_unix_ones(self):
        self.assertEqual(messages("SigninLogs\r\n| wher x"), messages("SigninLogs\n| wher x"))

    def test_tables_read_are_listed_once_in_order(self):
        self.assertEqual(kql.check(CLEAN[3]).tables, ["SigninLogs", "AADNonInteractiveUserSignInLogs"])
        self.assertEqual(kql.check(CLEAN[4]).tables, ["AuditLogs", "IdentityInfo"])
        self.assertEqual(kql.check("let a = SigninLogs; a | join (SigninLogs | take 1) on x").tables, ["SigninLogs"])

    def test_a_badly_broken_query_lists_a_bounded_number_of_issues(self):
        self.assertEqual(len(kql.check("T " + "| wher x " * 50).issues), kql.MAX_ISSUES)

    def test_suggestions_for_log_analytics_messages(self):
        detail = "'where' operator: Failed to resolve table or column expression named 'SignLogs'"
        self.assertEqual(kql.unresolved_name(detail), "SignLogs")
        self.assertEqual(kql.suggest_table(kql.unresolved_name(detail)), "SigninLogs")
        self.assertEqual(kql.unresolved_name("Failed to resolve scalar expression named 'Foo'"), "Foo")
        for name in (None, "", "SigninLogs", "UserPrincipalName", "Foo_CL", "DeviceTvmSoftwareVulnerabilitiesKB"):
            with self.subTest(name=name):
                self.assertIsNone(kql.suggest_table(name))
        self.assertIsNone(kql.unresolved_name("Query could not be parsed at 'wher' on line [2,2]"))

    def test_summary_line(self):
        self.assertEqual(kql.summary(kql.check(CLEAN[0])), ("No problems found. Reads SigninLogs.", "neutral"))
        text, tone = kql.summary(kql.check("T | wher a | wher b | wher c | wher d"))
        self.assertEqual(tone, "warning")
        self.assertEqual(text.splitlines()[-1], "…and 1 more.")
        self.assertEqual(kql.summary(kql.check("T | where (a"))[1], "danger")
        self.assertEqual(kql.summary(kql.check("print 1")), ("No problems found.", "neutral"))


if __name__ == "__main__":
    unittest.main()
