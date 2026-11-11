"""A quick offline look at a KQL query as it is typed, before anything is sent to Azure.

Not a full KQL parser (Microsoft's own is a large library): it reads the query's shape (strings,
comments, brackets, statements and the | steps of each pipeline) and points at the mistakes analysts
make most often, each with its line and column.

- Errors are things KQL always rejects: a string or bracket left open, a bracket closed that was
  never opened or closed with the wrong kind, and a | with nothing before or after it.
- Warnings are almost always mistakes, but the query may still be right: a step KQL doesn't have
  (| wher), a step in capitals, = instead of == in where, && or || instead of and / or, a table name
  that differs from a well-known one only in case or by a letter or two (SignLogs -> SigninLogs), a
  function or a where operator a letter or two off a built-in one (tostrng(, contians) or in capitals.

Names that aren't close to a well-known table are left alone: custom tables (_CL), functions,
watchlists and ASIM parsers are all valid. Log Analytics has the last word (Run test query).

Most warnings carry the spelling they suggest (Issue.fix), so the builder can put it in for the
analyst. The report also keeps what the walk learned about the query (its tokens, comments, let
names, and where its steps and tables are), which kql_assist turns into colours and completions."""
import bisect
import difflib
import re
from dataclasses import dataclass, field

# Tables most Microsoft Sentinel workspaces have, or that rules commonly read. Only used to notice
# near misses; a table missing from this list is never reported.
KNOWN_TABLES = frozenset("""
AADManagedIdentitySignInLogs AADNonInteractiveUserSignInLogs AADProvisioningLogs AADRiskyServicePrincipals
AADRiskyUsers AADServicePrincipalRiskEvents AADServicePrincipalSignInLogs AADUserRiskEvents ADFSSignInLogs
AKSAudit AKSAuditAdmin AKSControlPlane ASimAuditEventLogs ASimAuthenticationEventLogs ASimDhcpEventLogs
ASimDnsActivityLogs ASimFileEventLogs ASimNetworkSessionLogs ASimProcessEventLogs ASimRegistryEventLogs
ASimUserManagementActivityLogs ASimWebSessionLogs AWSCloudTrail AWSCloudWatch AWSGuardDuty AWSVPCFlow
AZFWApplicationRule AZFWDnsQuery AZFWIdpsSignature AZFWNatRule AZFWNetworkRule AZFWThreatIntel
AlertEvidence AlertInfo Anomalies AppDependencies AppExceptions AppRequests AppServiceAuditLogs
AppServiceConsoleLogs AppServiceHTTPLogs AppTraces AuditLogs AzureActivity AzureDevOpsAuditing
AzureDiagnostics AzureMetrics BehaviorAnalytics CloudAppEvents CommonSecurityLog ConfidentialWatchlist
ConfigurationChange ConfigurationData ContainerLog ContainerLogV2 DeviceEvents DeviceFileCertificateInfo
DeviceFileEvents DeviceImageLoadEvents DeviceInfo DeviceLogonEvents DeviceNetworkEvents DeviceNetworkInfo
DeviceProcessEvents DeviceRegistryEvents DeviceTvmBrowserExtensions DeviceTvmBrowserExtensionsKB
DeviceTvmCertificateInfo DeviceTvmHardwareFirmware DeviceTvmInfoGathering DeviceTvmInfoGatheringKB
DeviceTvmSecureConfigurationAssessment DeviceTvmSecureConfigurationAssessmentKB DeviceTvmSoftwareEvidenceBeta
DeviceTvmSoftwareInventory DeviceTvmSoftwareVulnerabilities DeviceTvmSoftwareVulnerabilitiesKB
DnsEvents DnsInventory Dynamics365Activity EmailAttachmentInfo EmailEvents EmailPostDeliveryEvents
EmailUrlInfo Event GCPAuditLogs Heartbeat IdentityDirectoryEvents IdentityInfo IdentityLogonEvents
IdentityQueryEvents InsightsMetrics IntuneAuditLogs IntuneDeviceComplianceOrg IntuneDevices
IntuneOperationalLogs KubeEvents KubePodInventory LAQueryLogs McasShadowItReporting
MicrosoftGraphActivityLogs NetworkAccessTraffic OfficeActivity Operation Perf PowerBIActivity
ProtectionStatus SecurityAlert SecurityBaseline SecurityBaselineSummary SecurityDetection SecurityEvent
SecurityIncident SecurityRecommendation SentinelAudit SentinelHealth SigninLogs StorageBlobLogs StorageFileLogs
StorageQueueLogs StorageTableLogs Syslog ThreatIntelIndicators ThreatIntelObjects
ThreatIntelligenceIndicator Update UpdateSummary UrlClickEvents Usage UserPeerAnalytics VMComputer
VMConnection VMProcess W3CIISLog Watchlist WindowsEvent
""".split())
_BY_LOWER = {name.lower(): name for name in KNOWN_TABLES}

# Everything that can follow a |, including older spellings KQL still accepts.
OPERATORS = frozenset("""
as assert-schema consume count distinct evaluate extend externaldata facet filter find fork getschema
graph-mark-components graph-match graph-shortest-paths graph-to-table invoke join limit lookup
macro-expand make-graph make-series makeseries mv-apply mv-expand mvapply mvexpand order parse parse-kv
parse-where partition print project project-away project-keep project-rename project-reorder range
reduce render sample sample-distinct scan search serialize sort summarize take top top-hitters
top-nested union where
""".split())

# Words that start a statement or a source without being a table.
SOURCES = frozenset({"let", "set", "declare", "alias", "pattern", "restrict", "union", "print", "range",
                     "datatable", "search", "find", "externaldata", "evaluate", "materialize", "view",
                     "true", "false", "null", "dynamic", "toscalar", "and", "or", "not", "on", "by", "with"})
# The ones that are a statement's first word, coloured like a step.
STATEMENTS = frozenset({"let", "set", "declare", "alias", "pattern", "restrict", "union", "print", "range",
                        "datatable", "search", "find", "externaldata"})

# Functions that summarize takes (and make-series, top-nested).
AGGREGATES = frozenset("""
any anyif arg_max arg_min avg avgif binary_all_and binary_all_or binary_all_xor buildschema count
count_distinct count_distinctif countif dcount dcountif hll hll_if hll_merge make_bag make_bag_if
make_dictionary make_list make_list_if make_list_with_nulls make_set make_set_if makelist makeset max maxif min minif
percentile percentiles percentiles_array percentilesw percentilew stdev stdevif stdevp sum sumif
take_any take_anyif tdigest tdigest_merge variance varianceif variancep variancepif
""".split())

# Built-in scalar functions, the type names used as casts (int(null), datetime(2026-01-01)) and the
# plugins evaluate runs. Only used to colour, complete and notice near misses: a function missing
# from this list is never reported.
FUNCTIONS = frozenset("""
abs acos ago around array_concat array_iff array_index_of array_length array_reverse array_rotate_left
array_rotate_right array_shift_left array_shift_right array_slice array_sort_asc array_sort_desc
array_split array_sum asin atan atan2 bag_has_key bag_keys bag_merge bag_pack bag_pack_columns
bag_remove_keys bag_set_key bag_zip base64_decode_toarray base64_decode_toguid base64_decode_tostring
base64_encode_fromarray base64_encode_fromguid base64_encode_tostring beta_cdf bin bin_at bin_auto
binary_and binary_not binary_or binary_shift_left binary_shift_right binary_xor bitset_count_ones case
ceiling coalesce column_ifexists columnifexists convert_angle convert_length cos cot countof
datetime_add datetime_diff datetime_local_to_utc datetime_part datetime_utc_to_local dayofmonth
dayofweek dayofyear dcount_hll degrees dynamic_to_json endofday endofmonth endofweek endofyear
estimate_data_size exp exp10 exp2 extract extract_all extract_json extractjson floor format_bytes
format_datetime format_ipv4 format_ipv4_mask format_timespan gamma geo_distance_2points
geo_info_from_ip_address geo_point_in_circle geo_point_in_polygon geo_point_to_geohash getmonth gettype
getyear gzip_compress_to_base64_string gzip_decompress_from_base64_string has_any_index has_any_ipv4
has_any_ipv4_prefix has_ipv4 has_ipv4_prefix hash hash_combine hash_many hash_md5 hash_sha1 hash_sha256
hash_xxhash64 hourofday iff iif indexof indexof_regex ingestion_time ipv4_compare ipv4_is_in_any_range
ipv4_is_in_range ipv4_is_match ipv4_is_private ipv4_netmask_suffix ipv4_range_to_cidr_list ipv6_compare
ipv6_is_in_any_range ipv6_is_in_range ipv6_is_match isascii isempty isfinite isinf isnan isnotempty
isnotnull isnull isutf8 jaccard_index log log10 log2 loggamma make_datetime make_string make_timespan
max_of min_of monthofyear new_guid not now pack pack_all pack_array pack_dictionary parse_command_line
parse_csv parse_ipv4 parse_ipv4_mask parse_ipv6 parse_ipv6_mask parse_json parse_path parse_url
parse_urlquery parse_user_agent parse_version parse_xml parsejson percentile_tdigest pi pow prev next
punycode_domain_from_string punycode_domain_to_string punycode_from_string punycode_to_string radians
rand range regex_quote repeat replace_regex replace_string replace_strings reverse round row_cumsum
row_number row_rank_dense row_rank_min row_window_session series_abs series_add series_decompose
series_decompose_anomalies series_decompose_forecast series_fill_backward series_fill_const
series_fill_forward series_fill_linear series_fir series_fit_2lines series_fit_line series_iir
series_outliers series_periods_detect series_stats series_stats_dynamic series_subtract set_difference
set_has_element set_intersect set_union sign sin split sqrt startofday startofmonth startofweek
startofyear strcat strcat_array strcat_delim strcmp string_size strlen strrep substring tan tobool
toboolean todatetime todecimal todouble todynamic toguid tohex toint tolong tolower toreal tostring
totimespan toupper translate treepath trim trim_end trim_start unicode_codepoints_from_string
unicode_codepoints_to_string unixtime_microseconds_todatetime unixtime_milliseconds_todatetime
unixtime_nanoseconds_todatetime unixtime_seconds_todatetime url_decode url_encode url_encode_component
week_of_year weekofyear welch_test zip zlib_compress_to_base64_string zlib_decompress_from_base64_string
notempty notnull toobject base64_decodestring base64_encodestring percentile_array_tdigest
percentrank_tdigest rank_tdigest merge_tdigest geo_point_to_s2cell geo_point_to_h3cell
geo_distance_point_to_line geo_distance_point_to_polygon geo_geohash_to_central_point series_fit_poly
series_multiply series_divide series_greater series_greater_equals series_less series_less_equals
series_equals series_not_equals series_pearson_correlation series_seasonal series_exp series_log
series_pow series_sum series_product series_dot_product series_magnitude series_cosine_similarity
series_fft series_ifft series_ceiling series_floor series_sign series_cos series_sin series_tan
toscalar materialize table workspace app resource
bool datetime decimal double dynamic guid int long real string time timespan typeof
bag_unpack pivot narrow autocluster basket diffpatterns diffpatterns_text activity_counts_metrics
activity_engagement activity_metrics funnel_sequence funnel_sequence_completion new_activity_metrics
rolling_percentile sequence_detect session_count sliding_window_counts active_users_count
dcount_intersect ipv4_lookup ipv6_lookup preview
""".split())

# Operators spelt as words, as where uses them between two values.
WORD_OPERATORS = frozenset("""
and or in between has has_cs has_any has_all hasprefix hasprefix_cs hassuffix hassuffix_cs contains
contains_cs containscs notcontains notcontainscs startswith startswith_cs endswith endswith_cs matches
like likecs notlike notlikecs
""".split())

JOIN_KINDS = ("inner", "innerunique", "leftouter", "rightouter", "fullouter", "leftanti", "rightanti",
              "leftsemi", "rightsemi")

# The other words KQL itself owns: never a column, a table or a function of the analyst's.
KEYWORDS = frozenset("""
by on asc desc nulls first last kind with to step from of regex default isfuzzy withsource
bagexpansion with_itemindex with_match_id with_source hint anti semi leftantisemi rightantisemi
simple relaxed nooptimization view materialize toscalar typeof true false null
""".split()) | SOURCES | frozenset(JOIN_KINDS)

# Steps whose brackets hold steps of their own, with no | before the first: mv-apply x on (where …).
_SUBQUERY_STEPS = frozenset({"mv-apply", "mvapply", "fork", "partition", "facet"})
_CALLS = sorted(FUNCTIONS | AGGREGATES)
_KNOWN_CALLS = FUNCTIONS | AGGREGATES | WORD_OPERATORS | KEYWORDS | OPERATORS
_WORD_OPERATORS = sorted(WORD_OPERATORS)

# Two-character operators, longest first where it matters.
_DOUBLE = ("==", "!=", "=~", "!~", "<=", ">=", "<>", "=>", "&&", "||", "..")
_OPEN, _CLOSE = "([{", ")]}"
_PAIR = dict(zip(_CLOSE, _OPEN))
_WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_]*")
_STEP = re.compile(r"[A-Za-z_$][A-Za-z0-9_]*(?:-[A-Za-z_][A-Za-z0-9_]*)*")
_NUMBER = re.compile(r"[0-9](?:[0-9A-Za-z_]|\.(?=[0-9]))*")
_UNRESOLVED = re.compile(r"Failed to resolve (?:table or column|table|column|scalar) expression named '([^']+)'",
                         re.IGNORECASE)
MAX_ISSUES = 20  # Enough to fix; a badly broken query would otherwise list one per token.


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" (KQL can't run it) or "warning" (almost always a mistake).
    line: int      # 1-based, as editors count.
    column: int    # 0-based, as Tk text indexes count; shown 1-based.
    length: int
    message: str
    fix: str = None  # What to write over those ``length`` characters to put it right, when the check knows.

    @property
    def where(self):
        return f"Line {self.line}, column {self.column + 1}"

    def __str__(self):
        return self.where + ": " + self.message


@dataclass
class Report:
    issues: list = field(default_factory=list)
    tables: list = field(default_factory=list)  # Tables the query reads (well-known or not), in order.
    # What the walk learned, for kql_assist. Offsets count characters in ``text`` (line ends are \n).
    text: str = ""
    tokens: list = field(default_factory=list)
    comments: list = field(default_factory=list)  # (start, end) of each // comment.
    lets: frozenset = frozenset()                 # Names the query defines with let.
    marks: list = field(default_factory=list)     # ("step" | "table", start, end) where the walk found one.

    @property
    def fixes(self):
        """The issues the check can put right itself, in order."""
        return [issue for issue in self.issues if issue.fix is not None]

    @property
    def errors(self):
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self):
        return [issue for issue in self.issues if issue.severity == "warning"]


@dataclass(frozen=True)
class Token:
    kind: str  # word, number, string, op, open, close, pipe, semi, comma
    text: str
    start: int
    gap: bool  # Whitespace or a comment comes right before it.


class _Source:
    """The query text with offset -> (line, column) lookups."""

    def __init__(self, text):
        self.text = text
        self.starts = [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]

    def position(self, offset):
        line = bisect.bisect_right(self.starts, offset) - 1
        return line + 1, offset - self.starts[line]

    def issue(self, severity, offset, length, message, fix=None):
        line, column = self.position(offset)
        # A mark never runs past its own line: Tk would otherwise underline the line break as well.
        end = self.text.find("\n", offset)
        length = max(1, min(length, (len(self.text) if end < 0 else end) - offset))
        return Issue(severity, line, column, length, message, fix)

    def spaced(self, offset, length, word):
        """``word`` with a space on each side that has none in the query (a&&b -> a and b)."""
        before, after = self.text[offset - 1:offset], self.text[offset + length:offset + length + 1]
        return (" " if before and not before.isspace() else "") + word + (" " if after and not after.isspace() else "")


def tokens(text, source=None, issues=None, comments=None):
    """The query's tokens, without comments or whitespace. Unclosed strings go to ``issues``, and the
    (start, end) of each comment to ``comments``."""
    source = source or _Source(text)
    found, index, gap, size = [], 0, True, len(text)
    while index < size:
        char = text[index]
        if char.isspace():
            index += 1; gap = True; continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            if comments is not None: comments.append((index, size if end < 0 else end))
            index = size if end < 0 else end; gap = True; continue
        start = index
        if text.startswith(("```", "~~~"), index):  # A multi-line string.
            fence = text[index:index + 3]
            end = text.find(fence, index + 3)
            if end < 0:
                if issues is not None: issues.append(source.issue("error", start, 3, f"This {fence} string isn't closed."))
                index = size
            else:
                index = end + 3
            found.append(Token("string", text[start:index], start, gap)); gap = False; continue
        prefix = 0
        if char in "@hH" and index + 1 < size and text[index + 1] in "\"'": prefix = 1
        elif char in "hH" and text.startswith(("@\"", "@'"), index + 1): prefix = 2
        quote = text[index + prefix] if index + prefix < size else ""
        if quote in ("\"", "'"):
            verbatim = "@" in text[index:index + prefix]
            index += prefix + 1
            while index < size and text[index] != quote and text[index] != "\n":
                index += 2 if text[index] == "\\" and not verbatim else 1
            if index < size and text[index] == quote:
                index += 1
            elif issues is not None:
                issues.append(source.issue("error", start, index - start,
                                           "This string isn't closed. Add the closing " + quote + " on the same line."))
            found.append(Token("string", text[start:min(index, size)], start, gap)); gap = False; continue
        match = _WORD.match(text, index) if not char.isdigit() else None
        if match:
            found.append(Token("word", match.group(), start, gap)); index = match.end(); gap = False; continue
        match = _NUMBER.match(text, index)
        if match:
            found.append(Token("number", match.group(), start, gap)); index = match.end(); gap = False; continue
        double = text[index:index + 2]
        if double in _DOUBLE:
            found.append(Token("op", double, start, gap)); index += 2; gap = False; continue
        kind = ("open" if char in _OPEN else "close" if char in _CLOSE else "pipe" if char == "|"
                else "semi" if char == ";" else "comma" if char == "," else "op")
        found.append(Token(kind, char, start, gap)); index += 1; gap = False
    return found


def _brackets(found, source, issues):
    """Brackets that are never closed, closed without being opened, or closed by the wrong kind."""
    stack = []
    for token in found:
        if token.kind == "open":
            stack.append(token)
        elif token.kind == "close":
            if not stack:
                issues.append(source.issue("error", token.start, 1,
                                           f"This {token.text} has no {_PAIR[token.text]} before it."))
            elif stack[-1].text != _PAIR[token.text]:
                opener = stack.pop()
                line, column = source.position(opener.start)
                closer = _CLOSE[_OPEN.index(opener.text)]
                issues.append(source.issue("error", token.start, 1,
                                           f"This {token.text} closes the {opener.text} on line {line}, column "
                                           f"{column + 1}, which needs {closer}."))
            else:
                stack.pop()
    for opener in stack:
        closer = _CLOSE[_OPEN.index(opener.text)]
        issues.append(source.issue("error", opener.start, 1, f"This {opener.text} is never closed. Add {closer}."))


def _table(name, token, source, issues):
    """A warning when ``name`` is a near miss of a well-known table."""
    if name in KNOWN_TABLES or _custom(name): return
    exact = _BY_LOWER.get(name.lower())
    if exact:
        issues.append(source.issue("warning", token.start, len(name),
                                   f"Table names are case-sensitive in KQL: write {exact}.", exact))
        return
    better = suggest_table(name)
    if better:
        issues.append(source.issue("warning", token.start, len(name),
                                   f"{name} isn't a known table. Did you mean {better}? "
                                   "(Ignore this for a custom table or function.)", better))


def _custom(name):
    """Names that are custom tables, functions or ASIM parsers, never near misses of a built-in table."""
    return (name.endswith("_CL") or name.startswith(("_", "$")) or re.match(r"(?:im|vim|ASim)[A-Z]", name) is not None)


def _function(name, token, source, issues):
    """A warning when ``name``, which is being called, is a built-in function in capitals or a letter
    or two off one. Any other name is the analyst's own function and is left alone."""
    if name in _KNOWN_CALLS or _custom(name): return
    lower = name.lower()
    if lower in FUNCTIONS or lower in AGGREGATES:
        issues.append(source.issue("warning", token.start, len(name),
                                   f"KQL functions are lowercase: write {lower}.", lower))
    elif name == lower and len(name) >= 4:  # A built-in is never capitalised, so neither is a slip of one.
        close = difflib.get_close_matches(name, _CALLS, n=1, cutoff=0.85)
        if close:
            issues.append(source.issue("warning", token.start, len(name),
                                       f"KQL has no {name} function. Did you mean {close[0]}? "
                                       "(Ignore this for a function of your own.)", close[0]))


def _word_operator(name, token, source, issues):
    """A warning, and True, when ``name`` sits between two values in where and is an operator in
    capitals (AND, Contains) or a letter or two off one (contians)."""
    if name in WORD_OPERATORS: return False
    lower = name.lower()
    if lower in WORD_OPERATORS:
        issues.append(source.issue("warning", token.start, len(name),
                                   f"KQL operators are lowercase: write {lower}.", lower))
        return True
    close = difflib.get_close_matches(lower, _WORD_OPERATORS, n=1, cutoff=0.8) if len(name) >= 4 else None
    if close:
        issues.append(source.issue("warning", token.start, len(name),
                                   f"KQL has no {name} operator. Did you mean {close[0]}?", close[0]))
    return bool(close)


class _Frame:
    """The pipeline inside one pair of brackets (or the statement): its current step, and whether that
    step still takes table names (union's list; join's and lookup's right side, up to on)."""

    def __init__(self):
        self.step, self.names = None, False


def check(query):
    """A Report on ``query``: its issues (sorted by position, at most MAX_ISSUES) and the tables it reads."""
    text = str(query or "").replace("\r\n", "\n")
    source, issues, comments = _Source(text), [], []
    found = tokens(text, source, issues, comments)
    _brackets(found, source, issues)
    lets = {found[i + 1].text for i, token in enumerate(found[:-1])
            if token.kind == "word" and token.text == "let" and found[i + 1].kind == "word"}
    report = Report(text=text, tokens=found, comments=comments, lets=frozenset(lets))
    frames, first, skip_until = [_Frame()], True, -1
    for i, token in enumerate(found):
        prev = found[i - 1] if i else None
        nxt = found[i + 1] if i + 1 < len(found) else None
        starts, first, frame = first, False, frames[-1]
        if token.kind == "open":
            frames.append(_Frame()); continue
        if token.kind == "close":
            if len(frames) > 1: frames.pop()
            continue
        if token.kind == "semi":
            frame.step, frame.names, first = None, False, True; continue
        if token.kind == "pipe":
            if prev is None or prev.kind in ("semi", "open", "comma") or prev.text == "=":
                issues.append(source.issue("error", token.start, 1, "Nothing comes before this |. Start with a "
                                                                    "table, such as SigninLogs."))
            if nxt is None or nxt.kind in ("pipe", "semi", "close"):
                issues.append(source.issue("error", token.start, 1,
                                           "Nothing follows this |. Remove it or add a step, such as where."))
                frame.step = None; continue
            match = _STEP.match(text, nxt.start) if nxt.kind == "word" else None
            if not match:
                issues.append(source.issue("warning", nxt.start, len(nxt.text),
                                           "A | is followed by a step, such as where or summarize."))
                frame.step = None; continue
            word, skip_until = match.group(), match.end()
            step = word if word in OPERATORS else word.lower() if word.lower() in OPERATORS else None
            frame.step, frame.names = step or word, step in ("union", "join", "lookup")
            if step == word:
                report.marks.append(("step", nxt.start, match.end())); continue
            if step:
                issues.append(source.issue("warning", nxt.start, len(word), f"KQL steps are lowercase: write {step}.",
                                           step))
            else:
                close = difflib.get_close_matches(word.lower(), OPERATORS, n=1, cutoff=0.75)
                issues.append(source.issue("warning", nxt.start, len(word), f"KQL has no {word} step."
                                           + (f" Did you mean {close[0]}?" if close else ""), close[0] if close else None))
                if close:  # Read the rest of the step as the one meant, so its own slips show in the same pass.
                    frame.step, frame.names = close[0], close[0] in ("union", "join", "lookup")
            continue
        if token.kind == "op":
            if token.text in ("&&", "||"):
                word = "and" if token.text == "&&" else "or"
                issues.append(source.issue("warning", token.start, 2, "KQL uses and / or, not " + token.text + ".",
                                           source.spaced(token.start, 2, word)))
            elif token.text == "=" and frame.step in ("where", "filter"):
                issues.append(source.issue("warning", token.start, 1, "Use == to compare in where (= names a column).",
                                           "=="))
            continue
        if token.kind != "word" or token.start < skip_until: continue
        name = token.text
        outer = frames[-2] if len(frames) > 1 else None
        if prev is not None and prev.text == "(" and outer is not None and outer.step in _SUBQUERY_STEPS:
            match = _STEP.match(text, token.start)  # mv-apply x on (where …), fork (summarize …) (take 1).
            if match.group() in OPERATORS:
                frame.step, frame.names, skip_until = match.group(), match.group() in ("union", "join", "lookup"), match.end()
                report.marks.append(("step", token.start, match.end())); continue
        if starts and name in STATEMENTS: report.marks.append(("step", token.start, token.start + len(name)))
        if starts and name == "union":
            frame.step, frame.names = "union", True; continue
        if name == "on" and frame.step in ("join", "lookup"):
            frame.names = False; continue
        if name in SOURCES or name in lets: continue
        if frame.step in ("where", "filter") and nxt is not None and nxt.kind in ("word", "string", "number", "open"):
            # Between two values: only an operator belongs here (a !contians b counts the ! as part of it).
            left = found[i - 2] if prev is not None and prev.text == "!" and not token.gap and i >= 2 else prev
            value = left is not None and left.start >= skip_until and (
                left.kind in ("close", "string", "number") or
                (left.kind == "word" and left.text not in WORD_OPERATORS and left.text.lower() not in ("and", "or", "not")))
            if value and name not in _KNOWN_CALLS and name not in KNOWN_TABLES and \
                    _word_operator(name, token, source, issues): continue
        if nxt is not None and nxt.text == "(" and not (prev is not None and prev.text == "."):
            _function(name, token, source, issues)
        if nxt is not None and (nxt.kind == "open" or nxt.text in ("=", ".", ":")): continue  # A call, a name, a field.
        let_body = prev is not None and prev.text == "=" and i >= 3 and found[i - 3].text == "let"
        if prev is not None and (prev.text == "." or (prev.text == "=" and not let_body)): continue
        ends = nxt is None or nxt.kind in ("pipe", "semi", "close")
        bracketed = prev is not None and prev.kind == "open" and (
            (nxt is not None and nxt.kind == "pipe") or (prev.text == "{" and ends)
            or (prev.text == "(" and ends and outer is not None and outer.names))  # join (AuditLogs) on …
        listed = frame.names and (ends or nxt.kind == "comma" or (nxt.kind == "word" and nxt.text == "on"))
        if not ((starts or let_body) and ends or bracketed or listed): continue
        if listed and frame.step in ("join", "lookup"): frame.names = False  # Only one right side.
        if name not in report.tables: report.tables.append(name)
        report.marks.append(("table", token.start, token.start + len(name)))
        _table(name, token, source, issues)
    issues.sort(key=lambda issue: (issue.line, issue.column, issue.severity != "error"))
    report.issues = issues[:MAX_ISSUES]
    return report


def suggest_table(name):
    """The well-known table ``name`` most likely means, or None: a case slip or a letter or two off."""
    name = str(name or "").strip()
    if not name or name in KNOWN_TABLES or _custom(name): return None
    if name.lower() in _BY_LOWER: return _BY_LOWER[name.lower()]
    # A known name with more on the end is usually its own table (DeviceTvm...KB, ContainerLogV2).
    if len(name) < 4 or any(name.startswith(known) for known in KNOWN_TABLES): return None
    close = difflib.get_close_matches(name.lower(), list(_BY_LOWER), n=1, cutoff=0.8)
    return _BY_LOWER[close[0]] if close else None


def unresolved_name(message):
    """The name in Log Analytics' "Failed to resolve ... expression named 'X'" message, or None."""
    match = _UNRESOLVED.search(str(message or ""))
    return match.group(1) if match else None


def summary(report, limit=3):
    """(text, tone) for a one-glance line under the query box: up to ``limit`` issues, or what it reads."""
    if report.issues:
        lines = [str(issue) for issue in report.issues[:limit]]
        if len(report.issues) > limit: lines.append(f"…and {len(report.issues) - limit} more.")
        return "\n".join(lines), "danger" if report.errors else "warning"
    if report.tables:
        return "No problems found. Reads " + ", ".join(report.tables[:4]) + ("…" if len(report.tables) > 4 else "") + ".", "neutral"
    return "No problems found.", "neutral"
