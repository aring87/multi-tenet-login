"""A quick offline look at a KQL query as it is typed, before anything is sent to Azure.

Not a full KQL parser (Microsoft's own is a large library): it reads the query's shape (strings,
comments, brackets, statements and the | steps of each pipeline) and points at the mistakes analysts
make most often, each with its line and column.

- Errors are things KQL always rejects: a string or bracket left open, a bracket closed that was
  never opened or closed with the wrong kind, and a | with nothing before or after it.
- Warnings are almost always mistakes, but the query may still be right: a step KQL doesn't have
  (| wher), a step in capitals, = instead of == in where, && or || instead of and / or, a table name
  that differs from a well-known one only in case or by a letter or two (SignLogs -> SigninLogs).

Names that aren't close to a well-known table are left alone: custom tables (_CL), functions,
watchlists and ASIM parsers are all valid. Log Analytics has the last word (Run test query)."""
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

    @property
    def where(self):
        return f"Line {self.line}, column {self.column + 1}"

    def __str__(self):
        return self.where + ": " + self.message


@dataclass
class Report:
    issues: list = field(default_factory=list)
    tables: list = field(default_factory=list)  # Tables the query reads (well-known or not), in order.

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

    def issue(self, severity, offset, length, message):
        line, column = self.position(offset)
        # A mark never runs past its own line: Tk would otherwise underline the line break as well.
        end = self.text.find("\n", offset)
        length = max(1, min(length, (len(self.text) if end < 0 else end) - offset))
        return Issue(severity, line, column, length, message)


def tokens(text, source=None, issues=None):
    """The query's tokens, without comments or whitespace. Unclosed strings go to ``issues``."""
    source = source or _Source(text)
    found, index, gap, size = [], 0, True, len(text)
    while index < size:
        char = text[index]
        if char.isspace():
            index += 1; gap = True; continue
        if text.startswith("//", index):
            end = text.find("\n", index)
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
                                   f"Table names are case-sensitive in KQL: write {exact}."))
        return
    better = suggest_table(name)
    if better:
        issues.append(source.issue("warning", token.start, len(name),
                                   f"{name} isn't a known table. Did you mean {better}? "
                                   "(Ignore this for a custom table or function.)"))


def _custom(name):
    """Names that are custom tables, functions or ASIM parsers, never near misses of a built-in table."""
    return (name.endswith("_CL") or name.startswith(("_", "$")) or re.match(r"(?:im|vim|ASim)[A-Z]", name) is not None)


class _Frame:
    """The pipeline inside one pair of brackets (or the statement): its current step, and whether that
    step still takes table names (union's list; join's and lookup's right side, up to on)."""

    def __init__(self):
        self.step, self.names = None, False


def check(query):
    """A Report on ``query``: its issues (sorted by position, at most MAX_ISSUES) and the tables it reads."""
    text = str(query or "").replace("\r\n", "\n")
    source, issues = _Source(text), []
    found = tokens(text, source, issues)
    _brackets(found, source, issues)
    report = Report()
    lets = {found[i + 1].text for i, token in enumerate(found[:-1])
            if token.kind == "word" and token.text == "let" and found[i + 1].kind == "word"}
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
            if step == word: continue
            if step:
                issues.append(source.issue("warning", nxt.start, len(word), f"KQL steps are lowercase: write {step}."))
            else:
                close = difflib.get_close_matches(word.lower(), OPERATORS, n=1, cutoff=0.75)
                issues.append(source.issue("warning", nxt.start, len(word), f"KQL has no {word} step."
                                           + (f" Did you mean {close[0]}?" if close else "")))
            continue
        if token.kind == "op":
            if token.text in ("&&", "||"):
                issues.append(source.issue("warning", token.start, 2, "KQL uses and / or, not " + token.text + "."))
            elif token.text == "=" and frame.step in ("where", "filter"):
                issues.append(source.issue("warning", token.start, 1, "Use == to compare in where (= names a column)."))
            continue
        if token.kind != "word" or token.start < skip_until: continue
        name = token.text
        if starts and name == "union":
            frame.step, frame.names = "union", True; continue
        if name == "on" and frame.step in ("join", "lookup"):
            frame.names = False; continue
        if name in SOURCES or name in lets: continue
        if nxt is not None and (nxt.kind == "open" or nxt.text in ("=", ".", ":")): continue  # A call, a name, a field.
        let_body = prev is not None and prev.text == "=" and i >= 3 and found[i - 3].text == "let"
        if prev is not None and (prev.text == "." or (prev.text == "=" and not let_body)): continue
        ends = nxt is None or nxt.kind in ("pipe", "semi", "close")
        bracketed = prev is not None and prev.kind == "open" and (
            (nxt is not None and nxt.kind == "pipe") or (prev.text == "{" and ends))
        listed = frame.names and (ends or nxt.kind == "comma" or (nxt.kind == "word" and nxt.text == "on"))
        if not ((starts or let_body) and ends or bracketed or listed): continue
        if listed and frame.step in ("join", "lookup"): frame.names = False  # Only one right side.
        if name not in report.tables: report.tables.append(name)
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
