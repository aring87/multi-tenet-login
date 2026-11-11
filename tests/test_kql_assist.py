"""What the Query box colours and offers (kql_assist): pure text in, plain data out. Nothing here calls Azure."""
import re
import unittest

from sentinel_app import kql_assist as assist
from sentinel_app import kql_check as kql

QUERY = ('let window = 1h;\nSigninLogs // failed sign-ins\n| where ResultType != "0" and TimeGenerated > ago(window)\n'
         '| mv-expand Detail\n| summarize Attempts = count() by UserPrincipalName, bin(TimeGenerated, 5m)\n'
         '| join kind=inner (AuditLogs | take 1) on $left.UserPrincipalName == $right.Identity\n'
         '| where Attempts > 10 or MyFunction(Attempts) == true')


def offer(query):
    """complete() at the ^ in ``query``."""
    return assist.complete(query.replace("^", ""), query.index("^"))


def names(query, count=5):
    found = offer(query)
    return None if found is None else [choice.text for choice in found.choices[:count]]


class ColourTests(unittest.TestCase):
    def coloured(self, query):
        report = kql.check(query)
        return [(kind, report.text[start:end]) for kind, start, end in assist.spans(report)]

    def test_each_part_of_a_query_takes_its_colour(self):
        parts = self.coloured(QUERY)
        for expected in (("step", "let"), ("variable", "window"), ("number", "1h"), ("table", "SigninLogs"),
                         ("comment", "// failed sign-ins"), ("step", "|"), ("step", "where"), ("operator", "!="),
                         ("string", '"0"'), ("keyword", "and"), ("function", "ago"), ("step", "mv-expand"),
                         ("step", "summarize"), ("function", "count"), ("keyword", "by"), ("function", "bin"),
                         ("number", "5m"), ("step", "join"), ("keyword", "kind"), ("keyword", "inner"),
                         ("table", "AuditLogs"), ("keyword", "on"), ("keyword", "$left"), ("keyword", "or"),
                         ("function", "MyFunction"), ("number", "true")):
            with self.subTest(part=expected):
                self.assertIn(expected, parts)
        self.assertEqual([kind for kind, text in parts if text == "window"], ["variable", "variable"])
        # Columns and brackets keep the editor's own colour, and a step with a hyphen is one part.
        texts = [text for kind, text in parts]
        for plain in ("ResultType", "UserPrincipalName", "Attempts", "(", ")", ",", "mv", "expand"):
            self.assertNotIn(plain, texts)
        self.assertTrue(all(kind in assist.KINDS for kind, text in parts))

    def test_spans_are_in_order_and_never_overlap(self):
        report = kql.check(QUERY)
        spans = assist.spans(report)
        self.assertEqual(spans, sorted(spans, key=lambda span: span[1]))
        self.assertTrue(all(first[2] <= second[1] for first, second in zip(spans, spans[1:])))

    def test_a_step_that_is_not_one_is_left_plain(self):
        self.assertNotIn(("step", "wher"), self.coloured("SigninLogs | wher x"))
        self.assertNotIn(("step", "Summarize"), self.coloured("SigninLogs | Summarize count()"))
        self.assertIn(("keyword", "in"), self.coloured('T | where a in ("x")'))  # in ( is not a call.
        # A column that shares a table's name is only a table where a table can be.
        self.assertEqual(self.coloured('OfficeActivity | where Operation == "x"')[0], ("table", "OfficeActivity"))
        self.assertNotIn(("table", "Operation"), self.coloured('OfficeActivity | where Operation == "x"'))

    def test_an_unfinished_query_is_still_coloured(self):
        self.assertIn(("string", '"open'), self.coloured('T | where a == "open'))
        self.assertEqual(self.coloured(""), [])


class CompletionTests(unittest.TestCase):
    def test_steps_are_offered_after_a_pipe(self):
        found = offer("SigninLogs\n| ^")
        self.assertEqual((found.context, found.word, found.start), ("step", "", 13))
        self.assertEqual([choice.text for choice in found.choices[:4]], ["where", "summarize", "project", "extend"])
        self.assertEqual({choice.tail for choice in found.choices}, {" "})
        self.assertEqual(names("SigninLogs | su^"), ["summarize"])
        self.assertEqual(names("SigninLogs | project-a^"), ["project-away"])
        self.assertEqual(names("SigninLogs | mv^"), ["mv-expand", "mv-apply"])
        self.assertEqual(names("T | where x in (T2 | pro^)")[0], "project")
        for legacy in ("mvexpand", "makeseries", "filter"):  # Understood, never suggested.
            self.assertNotIn(legacy, assist.STEPS)
        self.assertTrue(set(assist.STEPS) <= kql.OPERATORS)

    def test_tables_are_offered_where_a_query_starts_or_joins(self):
        self.assertEqual(names("Sig^")[0], "SigninLogs")
        self.assertEqual(offer("Sig^").context, "table")
        self.assertEqual(names("^", 3), ["SigninLogs", "AuditLogs", "SecurityEvent"])
        self.assertEqual(names("let x = 1;\nSecurityI^"), ["SecurityIncident"])
        self.assertEqual(names("SigninLogs | join kind=inner (Aud^")[0], "AuditLogs")
        self.assertEqual(names("SigninLogs | join Ident^")[:2], ["IdentityInfo", "IdentityDirectoryEvents"])
        self.assertEqual(names("union isfuzzy=true SigninLogs, AADNon^"), ["AADNonInteractiveUserSignInLogs"])
        self.assertEqual(names("let failures = SigninLogs | take 5;\nfa^"), ["failures"])
        self.assertEqual(names("let known = Devi^")[0], "DeviceProcessEvents")
        self.assertEqual(names("le^")[0], "let")

    def test_columns_of_the_tables_read_come_first(self):
        found = offer("SigninLogs\n| where U^")
        self.assertEqual(found.context, "value")
        self.assertEqual([choice.text for choice in found.choices[:3]], ["UserPrincipalName", "UserDisplayName", "UserId"])
        self.assertEqual(found.choices[0].note, "SigninLogs")
        self.assertEqual(names("SigninLogs | where Time^")[0], "TimeGenerated")  # Every table has it.
        self.assertEqual(names("SecurityEvent | where Eve^")[:2], ["EventID", "EventData"])
        self.assertEqual(names("SigninLogs | join (AuditLogs) on $left.UserP^"), ["UserPrincipalName"])
        # Initials find a long name, and so do letters inside it.
        self.assertEqual(names("SigninLogs | where upn^"), ["UserPrincipalName"])
        self.assertEqual(names("SigninLogs | project Princ^"), ["UserPrincipalName"])

    def test_names_the_query_itself_uses_are_offered(self):
        query = "Custom_CL\n| extend FailedAttempts = toint(Count_d)\n| where Fail^"
        self.assertEqual(names(query), ["FailedAttempts"])
        self.assertEqual(offer(query).choices[0].note, "in this query")
        self.assertEqual(names("Custom_CL | where Count_d > 1 | project Cou^")[0], "Count_d")
        self.assertEqual(names("let threshold = 5;\nT | where x > thr^"), ["threshold"])
        self.assertEqual(names("T | summarize Attempts = count() by User | where att^"), ["Attempts"])

    def test_functions_and_operators_fit_the_step(self):
        self.assertEqual(names("T | summarize c^", 3), ["count", "countif", "count_distinct"])
        self.assertEqual(names("T | extend x = tos^"), ["tostring", "toscalar"])
        self.assertEqual(names("T | summarize Attempts = count() b^", 1), ["by"])
        self.assertEqual(names("T | sort by Attempts d^", 1), ["desc"])
        self.assertEqual(names("T | where Name cont^", 2), ["contains", "contains_cs"])
        self.assertEqual(names("T | where Name starts^", 1), ["startswith"])
        self.assertEqual(names("T | join kind=^", 3), ["inner", "innerunique", "leftouter"])
        self.assertEqual(offer("T | join kind=left^").context, "kind")
        self.assertEqual(names("let f = (name:s^"), ["string"])
        count = next(choice for choice in offer("T | summarize c^").choices if choice.text == "count")
        ago = next(choice for choice in offer("T | where t > ag^").choices if choice.text == "ago")
        self.assertEqual((count.tail, count.back, ago.tail, ago.back), ("()", 0, "()", 1))  # count()| and ago(|).

    def test_nothing_is_offered_where_it_would_get_in_the_way(self):
        for query in ('T | where a == "ab^"', 'T | where a == "open^', "T // a comm^", "T | where Use^r == 1",
                      "T | where t > ago(1^", "T | where n == 0x1F^", "T | extend x = Properties.a^",
                      "T | take^", "T | where zzzzqq^"):
            with self.subTest(query=query):
                self.assertIsNone(offer(query))
        self.assertIsNotNone(offer('T | where a == "closed" ^'))  # After a string is not inside it.

    def test_a_finished_word_is_listed_first_among_longer_ones(self):
        self.assertEqual(names("T | project^", 2), ["project", "project-away"])
        self.assertEqual(names("T | summarize count^", 2), ["count", "countif"])

    def test_the_vocabulary_is_well_formed(self):
        self.assertTrue(set(assist.COLUMNS) <= kql.KNOWN_TABLES)
        name = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
        for table, columns in assist.COLUMNS.items():
            with self.subTest(table=table):
                self.assertEqual(len(columns), len(set(columns)))
                self.assertTrue(all(name.match(column) for column in columns))
                self.assertNotIn("TimeGenerated", columns)  # Every table has it: EVERY_TABLE.
        for group in (assist.TABLES, assist.STEPS, assist.SCALARS, assist.SUMMARIES, assist.WORDS):
            self.assertEqual(len(group), len(set(group)))
        self.assertEqual(set(assist.TABLES), kql.KNOWN_TABLES)
        self.assertEqual(set(assist.SCALARS), kql.FUNCTIONS)
        self.assertEqual(set(assist.SUMMARIES), kql.AGGREGATES)
        self.assertEqual(set(assist.WORDS), kql.WORD_OPERATORS)
        # A function is lowercase, and nothing is both a step's keyword and a table.
        self.assertTrue(all(word == word.lower() for word in kql.FUNCTIONS | kql.AGGREGATES | kql.WORD_OPERATORS))
        self.assertFalse(kql.KEYWORDS & kql.KNOWN_TABLES)


if __name__ == "__main__":
    unittest.main()
