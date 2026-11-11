"""What the builder's Query box shows beyond the check: which colour each part of a query takes, and
what to offer while the analyst types. Text in, plain data out (kql_editor draws it); nothing here
touches Tk or calls Azure.

- spans(report) names the colour of each part of a checked query: steps, tables, functions, strings…
- complete(text, offset) lists what could be written at the caret: steps after a |, tables where a
  query starts or joins, and elsewhere the columns of the tables the query reads, the names the query
  itself defines, functions and operators.

Like kql_check this reads the query's shape, not its meaning. The columns are the common ones of the
tables rules read most; a workspace's real schema (custom columns, custom tables) is only known to
Log Analytics, so anything the analyst has already typed in the query is offered too."""
import re
from dataclasses import dataclass

from . import kql_check as kql
from .kql_check import AGGREGATES, FUNCTIONS, JOIN_KINDS, KEYWORDS, KNOWN_TABLES, OPERATORS, WORD_OPERATORS

# Every colour a part of the query can take (kql_editor has one tag per kind, desktop_theme one colour).
KINDS = ("comment", "string", "number", "step", "keyword", "function", "table", "variable", "operator")

# Columns every Log Analytics table has.
EVERY_TABLE = ("TimeGenerated", "Type", "TenantId", "SourceSystem", "_ResourceId")

_SIGN_IN = """UserPrincipalName UserDisplayName UserId UserType AppDisplayName AppId IPAddress Location
LocationDetails ResultType ResultDescription ResultSignature ClientAppUsed ConditionalAccessStatus
ConditionalAccessPolicies DeviceDetail AuthenticationRequirement AuthenticationDetails
RiskLevelDuringSignIn RiskLevelAggregated RiskState RiskDetail RiskEventTypes IsInteractive IsRisky
ResourceDisplayName ResourceIdentity Status UserAgent CorrelationId OperationName Category Identity
MfaDetail NetworkLocationDetails TokenIssuerType HomeTenantId ResourceTenantId AutonomousSystemNumber
CrossTenantAccessType CreatedDateTime OriginalRequestId UniqueTokenIdentifier"""
_SERVICE_SIGN_IN = """ServicePrincipalName ServicePrincipalId AppId IPAddress Location LocationDetails
ResultType ResultDescription ResourceDisplayName ResourceIdentity ResourceServicePrincipalId
ServicePrincipalCredentialKeyId ServicePrincipalCredentialThumbprint ConditionalAccessStatus
ConditionalAccessPolicies CorrelationId Category OperationName UniqueTokenIdentifier"""
# What every Defender XDR Device* table records about the process that did it.
_DEVICE = """Timestamp DeviceId DeviceName ActionType ReportId InitiatingProcessAccountName
InitiatingProcessAccountDomain InitiatingProcessAccountUpn InitiatingProcessAccountSid
InitiatingProcessFileName InitiatingProcessFolderPath InitiatingProcessCommandLine InitiatingProcessId
InitiatingProcessSHA1 InitiatingProcessSHA256 InitiatingProcessMD5 InitiatingProcessParentFileName
InitiatingProcessParentId InitiatingProcessIntegrityLevel InitiatingProcessTokenElevation
InitiatingProcessCreationTime AdditionalFields"""
_FILE = "FileName FolderPath SHA1 SHA256 MD5 FileSize"
_ACCOUNT = "AccountDomain AccountName AccountSid AccountUpn"

# The common columns of the tables rules read most, as Microsoft documents them. A suggestion only:
# a column missing here is never reported, and Run test query is the real check.
COLUMNS = {name: tuple(text.split()) for name, text in {
    "SigninLogs": _SIGN_IN,
    "AADNonInteractiveUserSignInLogs": _SIGN_IN,
    "AADServicePrincipalSignInLogs": _SERVICE_SIGN_IN,
    "AADManagedIdentitySignInLogs": _SERVICE_SIGN_IN,
    "AADRiskyUsers": """UserPrincipalName UserDisplayName Id RiskLevel RiskState RiskDetail
        RiskLastUpdatedDateTime IsDeleted IsProcessing CorrelationId OperationName""",
    "AADUserRiskEvents": """UserPrincipalName UserDisplayName UserId RiskEventType RiskLevel RiskState
        RiskDetail DetectionTimingType Activity ActivityDateTime DetectedDateTime IpAddress Location Source
        TokenIssuerType AdditionalInfo CorrelationId Id LastUpdatedDateTime RequestId OperationName""",
    "AuditLogs": """OperationName Category Result ResultReason ResultDescription ActivityDisplayName
        ActivityDateTime InitiatedBy TargetResources AdditionalDetails LoggedByService CorrelationId Identity
        AADOperationType AADTenantId Id Level DurationMs""",
    "SecurityEvent": """Computer EventID Activity Account AccountType AccountName AccountDomain TargetAccount
        TargetUserName TargetDomainName TargetLogonId TargetUserSid SubjectAccount SubjectUserName
        SubjectDomainName SubjectLogonId SubjectUserSid LogonType LogonTypeName LogonProcessName
        AuthenticationPackageName IpAddress IpPort WorkstationName Process ProcessName ProcessId
        NewProcessName NewProcessId ParentProcessName CommandLine EventData Channel Level Status SubStatus
        FailureReason ObjectName ObjectType PrivilegeList MemberName MemberSid ServiceName ShareName
        TokenElevationType EventSourceName Task""",
    "SecurityAlert": """AlertName AlertSeverity DisplayName Description ProviderName VendorName ProductName
        ProductComponentName AlertType SystemAlertId Entities ExtendedProperties Tactics Techniques
        SubTechniques Status CompromisedEntity ConfidenceLevel ConfidenceScore StartTime EndTime
        ProcessingEndTime RemediationSteps ResourceId AlertLink IsIncident""",
    "SecurityIncident": """IncidentName IncidentNumber Title Description Severity Status Classification
        ClassificationComment ClassificationReason Owner ProviderName ProviderIncidentId FirstActivityTime
        LastActivityTime FirstModifiedTime LastModifiedTime CreatedTime ClosedTime AlertIds BookmarkIds
        Comments Labels Tasks IncidentUrl RelatedAnalyticRuleIds AdditionalData ModifiedBy""",
    "AzureActivity": """OperationNameValue OperationName Caller CallerIpAddress CategoryValue
        ActivityStatusValue ActivitySubstatusValue ActivityStatus ActivitySubstatus Level ResourceGroup
        ResourceProviderValue ResourceId SubscriptionId Properties Properties_d Authorization
        Authorization_d Claims Claims_d HTTPRequest CorrelationId EventDataId EventSubmissionTimestamp
        Hierarchy Resource ResourceProvider Category""",
    "AzureDiagnostics": """Category OperationName ResourceId ResourceType ResourceGroup ResourceProvider
        Resource SubscriptionId ResultType ResultSignature ResultDescription CallerIPAddress CorrelationId
        Level DurationMs""",
    "OfficeActivity": """Operation OfficeWorkload RecordType UserId UserType UserKey ClientIP OfficeObjectId
        ResultStatus Parameters OrganizationId OrganizationName Site_Url SourceFileName SourceFileExtension
        SourceRelativeUrl UserAgent MailboxOwnerUPN Logon_Type ExternalAccess Item Folder Folders
        AffectedItems Client_IPAddress ActorIpAddress ItemType EventSource OfficeId Members TeamName
        DestMailboxOwnerUPN""",
    "Syslog": """Computer HostName HostIP Facility SeverityLevel SyslogMessage ProcessName ProcessID
        EventTime CollectorHostName""",
    "CommonSecurityLog": """DeviceVendor DeviceProduct DeviceVersion DeviceEventClassID Activity LogSeverity
        DeviceAction ApplicationProtocol Protocol SourceIP SourcePort SourceUserName SourceHostName
        DestinationIP DestinationPort DestinationUserName DestinationHostName RequestURL RequestMethod
        RequestClientApplication ReceivedBytes SentBytes Message AdditionalExtensions DeviceName
        DeviceAddress Computer SimplifiedDeviceAction CommunicationDirection FileName FileHash MaliciousIP
        ThreatSeverity IndicatorThreatType DeviceCustomString1 DeviceCustomString2 FlexString1 Reason
        EventOutcome""",
    "DeviceProcessEvents": f"""{_DEVICE} {_FILE} {_ACCOUNT} ProcessId ProcessCommandLine
        ProcessIntegrityLevel ProcessTokenElevation ProcessCreationTime LogonId
        ProcessVersionInfoCompanyName ProcessVersionInfoProductName ProcessVersionInfoOriginalFileName
        ProcessVersionInfoFileDescription""",
    "DeviceNetworkEvents": f"""{_DEVICE} RemoteIP RemotePort RemoteUrl LocalIP LocalPort Protocol
        LocalIPType RemoteIPType""",
    "DeviceFileEvents": f"""{_DEVICE} {_FILE} FileOriginUrl FileOriginReferrerUrl FileOriginIP
        PreviousFolderPath PreviousFileName RequestProtocol RequestSourceIP RequestSourcePort
        RequestAccountName RequestAccountDomain RequestAccountSid ShareName SensitivityLabel""",
    "DeviceLogonEvents": f"""{_DEVICE} AccountDomain AccountName AccountSid LogonType Protocol FailureReason
        IsLocalAdmin LogonId RemoteDeviceName RemoteIP RemoteIPType RemotePort""",
    "DeviceRegistryEvents": f"""{_DEVICE} RegistryKey RegistryValueType RegistryValueName RegistryValueData
        PreviousRegistryKey PreviousRegistryValueName PreviousRegistryValueData""",
    "DeviceImageLoadEvents": f"{_DEVICE} {_FILE}",
    "DeviceEvents": f"""{_DEVICE} {_FILE} AccountDomain AccountName AccountSid RemoteUrl RemoteDeviceName
        ProcessId ProcessCommandLine ProcessCreationTime ProcessTokenElevation LogonId RegistryKey
        RegistryValueName RegistryValueData RemoteIP RemotePort LocalIP LocalPort FileOriginUrl FileOriginIP""",
    "DeviceInfo": """Timestamp DeviceId DeviceName ClientVersion PublicIP OSArchitecture OSPlatform OSBuild
        OSVersion OSDistribution OSVersionInfo IsAzureADJoined AadDeviceId LoggedOnUsers RegistryDeviceTag
        MachineGroup ReportId OnboardingStatus DeviceCategory DeviceType DeviceSubtype Model Vendor
        MergedDeviceIds MergedToDeviceId SensorHealthState IsExcluded ExposureLevel JoinType""",
    "AlertInfo": "Timestamp AlertId Title Category Severity ServiceSource DetectionSource AttackTechniques",
    "AlertEvidence": f"""Timestamp AlertId Title Categories AttackTechniques ServiceSource DetectionSource
        EntityType EvidenceRole EvidenceDirection FileName FolderPath SHA1 SHA256 FileSize ThreatFamily
        RemoteIP RemoteUrl AccountName AccountDomain AccountSid AccountObjectId AccountUpn DeviceId
        DeviceName LocalIP NetworkMessageId EmailSubject Application ApplicationId ProcessCommandLine
        RegistryKey RegistryValueName RegistryValueData AdditionalFields Severity""",
    "EmailEvents": """Timestamp NetworkMessageId InternetMessageId SenderMailFromAddress SenderFromAddress
        SenderDisplayName SenderObjectId SenderMailFromDomain SenderFromDomain SenderIPv4 SenderIPv6
        RecipientEmailAddress RecipientObjectId Subject EmailClusterId EmailDirection DeliveryAction
        DeliveryLocation ThreatTypes ThreatNames DetectionMethods ConfidenceLevel EmailAction
        EmailActionPolicy EmailActionPolicyGuid AttachmentCount UrlCount EmailLanguage Connectors
        OrgLevelAction OrgLevelPolicy UserLevelAction UserLevelPolicy ReportId AuthenticationDetails
        BulkComplaintLevel""",
    "EmailUrlInfo": "Timestamp NetworkMessageId Url UrlDomain UrlLocation ReportId",
    "EmailAttachmentInfo": """Timestamp NetworkMessageId SenderFromAddress SenderDisplayName SenderObjectId
        RecipientEmailAddress RecipientObjectId FileName FileType SHA256 FileSize ThreatTypes ThreatNames
        DetectionMethods ReportId""",
    "UrlClickEvents": """Timestamp Url ActionType AccountUpn Workload NetworkMessageId IPAddress ThreatTypes
        DetectionMethods IsClickedThrough UrlChain ReportId""",
    "IdentityInfo": """AccountUPN AccountName AccountDomain AccountSID AccountObjectId AccountDisplayName
        AccountTenantId AccountCreationTime GivenName Surname Department JobTitle Manager MailAddress City
        Country State StreetAddress CompanyName EmployeeId IsAccountEnabled GroupMembership AssignedRoles
        UserType UserState UserStateChangedOn Tags RiskLevel RiskState BlastRadius
        OnPremisesDistinguishedName OnPremisesExtensionAttributes SAMAccountName IsMFARegistered
        IsServiceAccount UserAccountControl InvestigationPriority EntityRiskScore RelatedAccounts
        ServicePrincipals DeletedDateTime ChangeSource LastSeenDate Phone AdditionalMailAddresses
        ExtensionProperty""",
    "IdentityLogonEvents": """Timestamp ActionType Application LogonType Protocol FailureReason AccountName
        AccountDomain AccountUpn AccountSid AccountObjectId AccountDisplayName DeviceName DeviceType
        OSPlatform IPAddress Port DestinationDeviceName DestinationIPAddress DestinationPort
        TargetDeviceName TargetAccountDisplayName Location ISP ReportId AdditionalFields""",
    "CloudAppEvents": """Timestamp ActionType Application ApplicationId AccountObjectId AccountId
        AccountDisplayName AccountType IsAdminOperation IsExternalUser IsImpersonated DeviceType OSPlatform
        IPAddress IsAnonymousProxy CountryCode City ISP UserAgent ActivityType ActivityObjects ObjectName
        ObjectType ObjectId ReportId IPTags IPCategory UserAgentTags RawEventData AdditionalFields""",
    "BehaviorAnalytics": """UserName UserPrincipalName ActivityType ActionType EventSource SourceIPAddress
        SourceIPLocation SourceDevice DestinationIPAddress DestinationIPLocation DestinationDevice
        UsersInsights DevicesInsights ActivityInsights InvestigationPriority SourceRecordId EventVendor
        EventProductVersion NativeTableName TimeProcessed""",
    "ThreatIntelligenceIndicator": """IndicatorId ThreatType Active Description ConfidenceScore
        ExpirationDateTime NetworkIP NetworkSourceIP NetworkDestinationIP NetworkCidrBlock NetworkPort
        NetworkProtocol DomainName Url EmailSenderAddress EmailSenderName EmailRecipient EmailSubject
        FileName FileHashType FileHashValue FilePath Action Tags TrafficLightProtocolLevel ThreatSeverity
        AdditionalInformation AzureTenantId ExternalIndicatorId KnownFalsePositives LatestIndicatorTime
        MalwareNames""",
    "DnsEvents": """Computer ClientIP Name QueryType SubType Result ResultCode IPAddresses EventId Message
        TaskCategory Severity MaliciousIP RemoteIPCountry IndicatorThreatType Confidence""",
    "AWSCloudTrail": """EventName EventSource EventTypeName AWSRegion SourceIpAddress UserAgent
        UserIdentityType UserIdentityArn UserIdentityAccountId UserIdentityPrincipalid UserIdentityUserName
        UserIdentityAccessKeyId SessionMfaAuthenticated SessionIssuerUserName ErrorCode ErrorMessage
        RequestParameters ResponseElements RecipientAccountId ReadOnly ManagementEvent EventVersion
        AwsEventId AwsRequestId AdditionalEventData Resources VpcEndpointId""",
    "Heartbeat": """Computer ComputerIP ComputerEnvironment OSType OSName OSMajorVersion OSMinorVersion
        Version Category SubscriptionId ResourceGroup ResourceProvider Resource ResourceId ResourceType
        RemoteIPCountry RemoteIPLatitude RemoteIPLongitude ManagementGroupName Solutions VMUUID
        IsGatewayInstalled ComputerPrivateIPs""",
    "Event": """Computer EventLog EventID EventLevel EventLevelName Source RenderedDescription ParameterXml
        EventData UserName EventCategory Message""",
    "WindowsEvent": """Computer Channel EventID EventData EventLevel EventLevelName Provider Task Keywords
        Opcode EventRecordId Data Version SystemProcessId SystemThreadId SystemUserId Correlation
        TimeCreated""",
    "W3CIISLog": """Computer sSiteName sIP csMethod csUriStem csUriQuery sPort csUserName cIP csUserAgent
        csReferer csHost scStatus scSubStatus scWin32Status scBytes csBytes TimeTaken""",
    "Usage": "DataType Quantity QuantityUnit IsBillable Solution StartTime EndTime ResourceUri Computer",
}.items()}

# Offered in this order: what a rule most often starts with, then the rest by name.
_COMMON_TABLES = ("SigninLogs", "AuditLogs", "SecurityEvent", "SecurityAlert", "SecurityIncident", "AzureActivity",
                  "OfficeActivity", "Syslog", "CommonSecurityLog", "DeviceProcessEvents", "DeviceNetworkEvents",
                  "DeviceFileEvents", "DeviceLogonEvents", "DeviceEvents", "EmailEvents", "IdentityInfo",
                  "AADNonInteractiveUserSignInLogs", "ThreatIntelligenceIndicator", "BehaviorAnalytics", "Heartbeat")
TABLES = _COMMON_TABLES + tuple(sorted(KNOWN_TABLES - set(_COMMON_TABLES)))
_COMMON_STEPS = ("where", "summarize", "project", "extend", "join", "sort", "take", "top", "distinct", "count",
                 "mv-expand", "parse", "project-away", "project-rename", "project-keep", "project-reorder",
                 "union", "lookup", "order", "limit", "make-series", "evaluate", "invoke", "render", "search",
                 "serialize", "mv-apply", "parse-where", "parse-kv", "getschema", "sample", "as")
# Older spellings KQL still runs are understood, never offered.
STEPS = _COMMON_STEPS + tuple(sorted(OPERATORS - set(_COMMON_STEPS) - {"makeseries", "mvexpand", "mvapply", "filter"}))
_COMMON_FUNCTIONS = ("ago", "now", "bin", "tostring", "toint", "tolong", "todatetime", "todynamic", "tolower",
                     "toupper", "strcat", "iff", "case", "isempty", "isnotempty", "isnull", "isnotnull", "coalesce",
                     "parse_json", "extract", "split", "substring", "strlen", "replace_string", "trim",
                     "array_length", "bag_pack", "pack_array", "set_has_element", "ipv4_is_private",
                     "ipv4_is_in_range", "format_datetime", "datetime_diff", "startofday", "dayofweek", "hourofday",
                     "round", "column_ifexists", "base64_decode_tostring", "url_decode", "hash_sha256", "prev",
                     "next", "row_number", "toscalar", "materialize", "dynamic", "datetime", "timespan")
_COMMON_AGGREGATES = ("count", "countif", "dcount", "dcountif", "sum", "sumif", "avg", "min", "max", "arg_max",
                      "arg_min", "make_set", "make_list", "make_bag", "take_any", "percentile", "stdev")
SCALARS = _COMMON_FUNCTIONS + tuple(sorted(FUNCTIONS - set(_COMMON_FUNCTIONS)))
SUMMARIES = _COMMON_AGGREGATES + tuple(sorted(AGGREGATES - set(_COMMON_AGGREGATES)))
_COMMON_OPERATORS = ("and", "or", "has", "contains", "in", "startswith", "endswith", "has_any", "has_all",
                     "between", "matches")
WORDS = _COMMON_OPERATORS + tuple(sorted(WORD_OPERATORS - set(_COMMON_OPERATORS)))
TYPES = ("string", "int", "long", "real", "bool", "datetime", "timespan", "dynamic", "guid", "decimal")
# Called with nothing between the brackets, so the caret goes after them.
_NO_ARGUMENTS = frozenset({"count", "now", "ingestion_time", "row_number", "new_guid", "pi", "rand", "pack_all",
                           "buildschema", "getschema"})
# The keywords each step takes, offered before the general ones.
_STEP_WORDS = {"summarize": ("by",), "join": ("on", "kind", "$left", "$right"), "lookup": ("on", "kind", "$left", "$right"),
               "sort": ("by", "asc", "desc", "nulls"), "order": ("by", "asc", "desc", "nulls"),
               "top": ("by", "asc", "desc"), "top-nested": ("of", "by", "with"), "mv-expand": ("to", "typeof"),
               "mv-apply": ("to", "typeof", "on"), "parse": ("with", "kind"), "parse-where": ("with", "kind"),
               "make-series": ("on", "from", "to", "step", "by", "default"), "range": ("from", "to", "step"),
               "union": ("kind", "withsource", "isfuzzy"), "distinct": (), "render": ("with",),
               "partition": ("by",), "evaluate": ()}
_GENERAL_WORDS = ("by", "on", "asc", "desc", "true", "false", "null", "not")
_SPACE_AFTER = frozenset(("by", "on", "with", "to", "from", "step", "of", "let", "union", "print", "not")) | WORD_OPERATORS
_STATEMENTS = ("let", "union", "print", "datatable", "range", "search", "find")
_VOCABULARY = FUNCTIONS | AGGREGATES | WORD_OPERATORS | KEYWORDS | OPERATORS | frozenset(TYPES)

_TYPED = re.compile(r"[A-Za-z_$][A-Za-z0-9_]*$")
_TYPED_STEP = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:-[A-Za-z0-9_]*)*$")
_HUMPS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")
MAX_CHOICES = 60


# ------------------------------------------------------------------ colours
def spans(report):
    """(kind, start, end) for each part of a checked query that takes a colour, in query order; the
    offsets count characters in ``report.text``. Brackets, commas and names the check can't place
    (columns, mostly) are left out: they keep the editor's own text colour."""
    found = report.tokens
    marks = {start: (kind, end) for kind, start, end in report.marks}
    result = [("comment", start, end) for start, end in report.comments]
    skip = -1
    for index, token in enumerate(found):
        if token.start < skip: continue  # Inside a step with a hyphen (mv-expand): already coloured.
        end = token.start + len(token.text)
        if token.start in marks:
            kind, end = marks[token.start]; skip = end
        elif token.kind in ("string", "number"): kind = token.kind
        elif token.kind == "pipe": kind = "step"
        elif token.kind == "op": kind = "operator"
        elif token.kind == "word":
            kind = _word_kind(token.text, found[index + 1] if index + 1 < len(found) else None, report.lets)
        else: kind = None
        if kind: result.append((kind, token.start, end))
    result.sort(key=lambda span: span[1])
    return result


def _word_kind(word, following, lets):
    if word in ("true", "false", "null"): return "number"
    if word in lets: return "variable"
    if following is not None and following.text == "(":
        # Anything called is a function, the analyst's own included; in ( and by ( are still keywords.
        return "keyword" if word in WORD_OPERATORS or (word in KEYWORDS and word not in FUNCTIONS) else "function"
    if word in WORD_OPERATORS or word in KEYWORDS or word in ("$left", "$right"): return "keyword"
    return None


# ------------------------------------------------------------------ completions
@dataclass(frozen=True)
class Choice:
    text: str       # What is listed, and written into the query.
    kind: str       # One of KINDS, or "column": the colour it is listed in.
    note: str = ""  # Shown beside it: what it is or where it comes from.
    tail: str = ""  # Written straight after it: "()" for a function, a space where one always follows.
    back: int = 0   # How far the caret then steps back: 1 puts it between a function's brackets.


@dataclass(frozen=True)
class Offer:
    start: int       # Offset in the query where the word being typed begins.
    word: str        # What has been typed of it so far ("" just after a |).
    context: str     # "step" (after a |), "table", "kind" (after kind=), "type" (after a :) or "value".
    choices: tuple   # Choices, the likeliest first.


class _Level:
    """One pair of brackets (or the statement) while walking up to the caret."""

    def __init__(self, outer=None):
        self.step, self.fresh, self.joined, self.outer = None, True, False, outer


def complete(text, offset, limit=MAX_CHOICES):
    """What could be written at ``offset`` in ``text``, or None when there is nothing useful to offer:
    inside a string or a comment, in the middle of a word, after a dot, or when the word is already
    the only choice."""
    text = str(text or "").replace("\r\n", "\n")
    offset = max(0, min(int(offset), len(text)))
    if offset < len(text) and (text[offset].isalnum() or text[offset] == "_"): return None
    source, unclosed, comments = kql._Source(text), [], []
    found = kql.tokens(text, source, unclosed, comments)
    if any(start < offset <= end for start, end in comments): return None
    open_strings = {source.starts[issue.line - 1] + issue.column for issue in unclosed}
    for token in found:
        end = token.start + len(token.text)
        if token.kind == "string" and token.start < offset and (offset < end or (offset == end and token.start in open_strings)):
            return None
    typed = _TYPED_STEP.search(text[:offset])  # mv-expand and project-away are one word, straight after a |.
    before = [token for token in found if token.start < (typed.start() if typed else offset)]
    if not (typed and "-" in typed.group() and before and before[-1].kind == "pipe"):
        typed = _TYPED.search(text[:offset])
        before = [token for token in found if token.start < (typed.start() if typed else offset)]
    start, word = (typed.start(), typed.group()) if typed else (offset, "")
    if start and (text[start - 1].isalnum() or text[start - 1] == "_"): return None  # 1h, 0x1F: a number's tail.

    levels, skip = [_Level()], -1
    for token in before:
        level = levels[-1]
        if token.start < skip: continue
        if token.kind == "open": levels.append(_Level(level)); level.fresh = False
        elif token.kind == "close":
            if len(levels) > 1: levels.pop()
        elif token.kind == "semi": level.step, level.fresh, level.joined = None, True, False
        elif token.kind == "pipe": level.step, level.fresh, level.joined = "|", False, False
        elif token.kind == "word" and level.step == "|":
            match = kql._STEP.match(text, token.start)
            level.step, skip = match.group().lower(), match.end()
        else:
            if token.kind == "word" and level.fresh and token.text == "union": level.step = "union"
            if token.kind == "word" and token.text == "on": level.joined = True
            level.fresh = False
    level = levels[-1]
    last = before[-1] if before else None
    earlier = before[-2] if len(before) > 1 else None

    report = kql.check(text)
    if last is not None and last.text == ".":
        if earlier is None or earlier.text not in ("$left", "$right"): return None  # A field of a dynamic value.
        return _offer(start, word, "value", _columns(report, start), limit)
    if level.step == "|":
        return _offer(start, word, "step", [Choice(name, "step", "step", " ") for name in STEPS], limit)
    if last is not None and last.text == "=" and earlier is not None and earlier.text == "kind":
        return _offer(start, word, "kind", [Choice(name, "keyword", "join kind") for name in JOIN_KINDS], limit)
    if last is not None and last.text == ":" and earlier is not None and earlier.kind == "word":
        return _offer(start, word, "type", [Choice(name, "keyword", "type") for name in TYPES], limit)

    variables = [Choice(name, "variable", "let") for name in sorted(report.lets)
                 if not (name == word and _defined_at(found, start))]
    tables = [Choice(name, "table", "table") for name in TABLES]
    opening = last is not None and last.text == "("
    joins = level.step in ("join", "lookup") and not level.joined
    inside_join = opening and level.outer is not None and level.outer.step in ("join", "lookup", "union")
    if last is None or last.kind == "semi" or (last.kind == "open" and last.text == "{") or inside_join or \
            (level.step == "union" and last.kind in ("comma", "word", "number")) or (joins and last.kind != "op"):
        if joins: extra = [Choice("kind", "keyword", "keyword", "=")]
        elif inside_join or level.step == "union": extra = []
        else: extra = [Choice(name, "step", "statement", " " if name in _SPACE_AFTER else "") for name in _STATEMENTS]
        return _offer(start, word, "table", variables + tables + extra, limit)

    summarizing = level.step in ("summarize", "make-series", "top-nested")
    scalars = [Choice(name, "function", "function", "()", 1) for name in SCALARS]
    aggregates = [Choice(name, "function", "summarize function", "()", 0 if name in _NO_ARGUMENTS else 1)
                  for name in SUMMARIES]
    scalars = [choice if choice.text not in _NO_ARGUMENTS else Choice(choice.text, "function", "function", "()")
               for choice in scalars]
    words = list(dict.fromkeys(_STEP_WORDS.get(level.step, ()) + (WORDS if level.step in ("where", "filter") else ())
                               + _GENERAL_WORDS + (() if level.step in ("where", "filter") else WORDS)))
    keywords = [Choice(name, "keyword", "operator" if name in WORD_OPERATORS else "keyword",
                       " " if name in _SPACE_AFTER else "") for name in words]
    functions = aggregates + scalars if summarizing else scalars + aggregates
    assigned = last is not None and last.text == "=" and len(before) > 2 and before[-3].text == "let"
    own = len(_STEP_WORDS.get(level.step, ()))  # by after summarize, on after join: ahead of any column.
    choices = keywords[:own] + _columns(report, start) + variables + functions + keywords[own:]
    if opening or assigned or (last is not None and last.text == "in"):
        choices = (variables + tables + functions) if assigned else choices + tables  # A sub-query can start here.
    return _offer(start, word, "value", choices, limit)


def _defined_at(found, start):
    """Whether the word at ``start`` is the name a let is defining (let name = …)."""
    index = next((i for i, token in enumerate(found) if token.start == start), None)
    return bool(index) and found[index - 1].text == "let"


def _columns(report, start):
    """Columns to offer: those of the tables the query reads, then the names the query itself uses
    (aliases and custom columns), leaving out the word at ``start`` that is being typed."""
    choices = [Choice(column, "column", table) for table in report.tables for column in COLUMNS.get(table, ())]
    if report.tables or not report.tokens:
        choices += [Choice(column, "column", "every table") for column in EVERY_TABLE]
    tables = set(report.tables)
    marked = {begin for kind, begin, end in report.marks}
    for index, token in enumerate(report.tokens):
        following = report.tokens[index + 1] if index + 1 < len(report.tokens) else None
        if token.kind != "word" or token.start == start or token.start in marked: continue
        if token.text in _VOCABULARY or token.text in tables or token.text in report.lets: continue
        if token.text.startswith("$") or len(token.text) < 2 or (following is not None and following.text == "("): continue
        choices.append(Choice(token.text, "column", "in this query"))
    return choices


def _humps(name):
    """'UserPrincipalName' -> 'upn': the initials an analyst may type for a long name."""
    return "".join(part[0] for part in _HUMPS.findall(name)).lower()


def _offer(start, word, context, choices, limit):
    """The Offer for ``choices`` matching ``word``: names that start with it first, then names whose
    initials it spells (upn -> UserPrincipalName), then names that contain it. None when nothing
    matches, or when the word is already complete and nothing else is on offer."""
    typed, ranked, seen = word.lower(), [], set()
    for order, choice in enumerate(choices):
        if choice.text in seen: continue
        name = choice.text.lower()
        if not typed: rank = 0
        elif choice.text.startswith(word): rank = 0
        elif name.startswith(typed): rank = 1
        elif len(typed) > 1 and _humps(choice.text).startswith(typed): rank = 2
        # Only names are searched inside: 'att' finds FailedAttempts, not every function with those letters.
        elif len(typed) > 2 and typed in name and choice.kind in ("column", "table", "variable"): rank = 3
        else: continue
        seen.add(choice.text)
        ranked.append((rank, name != typed, order, choice))
    ranked.sort(key=lambda item: item[:3])
    kept = tuple(item[3] for item in ranked[:limit])
    if not kept or (len(kept) == 1 and kept[0].text == word): return None
    return Offer(start, word, context, kept)
