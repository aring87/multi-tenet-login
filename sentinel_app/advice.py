"""Plain-language advice for the errors analysts meet most often.

``friendly(message, workspace=..., repo=...)`` returns the advice for the first entry
whose words all appear in ``message`` (case does not matter), with {workspace} and
{repo} filled in, or ``message`` itself when nothing matches. The original message is
always kept in Session activity for engineers; the advice is what the analyst reads.
"""
import re

MFA = "This client requires multi-factor sign-in. Sign in again and complete MFA in the browser."
MISSING_REPOSITORY = ("GitHub couldn't find {repo} for your account. Check the spelling on Connect > Rule library, "
                      "or ask a repository admin for access (private repositories you can't see show as not found).")

# (words, advice): a single string, or a tuple of strings that must all appear. First match wins,
# so the specific entries come before the general ones.
FRIENDLY = (
    (("AuthorizationFailed", "alertRules/read"),
     "Your account can't read analytics rules in {workspace}. You need Microsoft Sentinel Reader on its resource "
     "group. Activate access in CyberQP, wait a few minutes, then select Refresh."),
    ("HTTP 404", MISSING_REPOSITORY),
    (("Signed in as", "GitHub read failed"), MISSING_REPOSITORY),  # The account works; the repository read didn't.
    ("AADSTS50076", MFA),
    ("AADSTS50079", MFA),
    ("Evidence collection supports Azure public cloud only",
     "Workspace rules can't be read from Azure Government (GCC High) workspaces yet."),
    ("already exists or its source is stale",
     "This rule's file is already in the library, or the library changed since you opened the rule. "
     "Use Edit existing rule in the Rule builder to start from the latest version."),
    ("rule ID already exists",
     "This rule is already in the library. Use Edit existing rule in the Rule builder to change it instead of adding a copy."),
    ("original repository rule is missing",
     "The rule you started from is no longer at that location in the library. Use Edit existing rule to open it again."),
    ("needs write access",
     "Your GitHub account can view the rule library but cannot submit to it. Ask a repository admin for write access."),
    ("catalog is incomplete or invalid",
     "Submissions are paused because some library files have problems. Ask an engineer to check "
     "Rules > Rule library > Library issues."),
    ("no file changes to submit",
     "Nothing has changed compared with the library version of this rule."),
    ("repository changed after preview",
     "Someone updated the library while you were reviewing. Select Check again, then submit."),
    ("private detection repository",
     "That repository is not the private rule library. Check the repository under Where it will be saved."),
    ("GitHub read failed", "Couldn't read the rule library with your GitHub account. Check that you are signed in "
     "(Connect > Rule library > Sign in to GitHub) and have access to the repository."),
    ("GitHub request failed", "GitHub did not accept the request. Check that you are signed in "
     "(Connect > Rule library > Sign in to GitHub) and have write access to the repository."),
    ("GitHub CLI is not installed", "GitHub CLI is not installed on this PC. Ask your administrator to install it."),
    ("GitHub timed out", "GitHub took too long to answer. Check your connection, then try again."),
)

# What a placeholder says when the caller doesn't know the value.
DEFAULTS = dict(workspace="this workspace", repo="the rule library repository")


def lookup(message, **context):
    """The advice for ``message``, or None when no entry matches."""
    text = str(message or "").casefold()
    for words, advice in FRIENDLY:
        if all(word.casefold() in text for word in ((words,) if isinstance(words, str) else words)):
            values = dict(DEFAULTS, **{key: value for key, value in context.items() if value})
            return advice.format_map(values)
    return None


def friendly(message, **context):
    """Plain advice for ``message``, or the message itself when there is none."""
    return lookup(message, **context) or str(message)


def first_sentence(text):
    """The first sentence (or line) of ``text``, for the one-line status bar."""
    line = next((part.strip() for part in str(text or "").splitlines() if part.strip()), "")
    match = re.match(r"(.+?[.!?])(?:\s|$)", line)
    return match.group(1) if match else line
