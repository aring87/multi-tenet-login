"""Recognize interactive Azure challenges without treating RBAC denials as MFA."""
import base64
import json
import re

from .lighthouse_onboarding import Stop

CHALLENGE = re.compile(
    r"\bAADSTS(?:50074|50076|50079|50173|70043|700082)\b|"
    r"\binteraction[_ ]?required\b|TokenIssuedBeforeRevocationTimestamp|--claims-challenge", re.I)
CLAIMS_ARGUMENT = re.compile(r"--claims-challenge\s+['\"]?([A-Za-z0-9_+/=-]{1,16384})")


class SignInCancelled(Stop):
    def __init__(self):
        super().__init__("Sign-in cancelled. You can sign in again.")


def signin_was_cancelled(details):
    # These are explicit user-cancellation signals, not generic access_denied,
    # consent_required, Conditional Access failures, or missing permissions.
    return bool(re.search(r"\b(?:user_cancelled|user_canceled|usercancelled|usercanceled|"
                         r"authentication_cancelled|authentication_canceled|Status_UserCanceled|"
                         r"Status_UserCancelled|AADSTS65004)\b|\buser (?:has )?cancel(?:led|ed)\b", details, re.I))


class AuthenticationRequired(Stop):
    def __init__(self, details, tenant=""):
        self.tenant = tenant
        self.broker_failure = bool(re.search(r"Status_InteractionRequired|V2Error:|MSALRuntime", details, re.I))
        self.resource = "arm"
        if "797f4846-ba00-4fd7-ba43-dac1f8f63013" not in details.lower() and re.search(
                r"00000003-0000-0000-c000-000000000000|https://graph\.|_msgraph", details, re.I):
            self.resource = "graph"
        self.claims = None
        match = CLAIMS_ARGUMENT.search(details)
        if match:
            try:
                encoded = match[1]
                value = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
                if isinstance(value, dict) and isinstance(value.get("access_token"), dict):
                    self.claims = encoded
            except (ValueError, UnicodeError):
                pass
        # Keep codes and correlation IDs, but don't copy challenge payloads to the log.
        details = CLAIMS_ARGUMENT.sub("--claims-challenge [omitted]", details)
        super().__init__("Microsoft requires interactive authentication for this operation.\n" + details)


def azure_error(details, tenant=""):
    if re.search(r"unrecognized arguments:.*--claims-challenge", details, re.I):
        return Stop("This Azure CLI does not support claims-challenge sign-in. Update Azure CLI to 2.76.0 or later, restart the app, and sign in again.\n" + CLAIMS_ARGUMENT.sub("--claims-challenge [omitted]", details))
    if CHALLENGE.search(details):
        return AuthenticationRequired(details, tenant)
    return Stop(details)


def recovery_scope(cloud, resource):
    if resource not in ("arm", "graph"):
        raise Stop("Unsupported authentication resource.")
    endpoints = cloud.get("endpoints", {})
    key = "activeDirectoryResourceId" if resource == "arm" else "microsoftGraphResourceId"
    audience = endpoints.get(key)
    if not isinstance(audience, str) or not audience.startswith("https://"):
        raise Stop("Azure CLI did not return the cloud's authentication endpoint. Check the CLI cloud configuration.")
    # Preserve the ARM audience trailing slash: its scope ends in //.default.
    # Graph uses its standard single-slash scope.
    return (audience if resource == "arm" else audience.rstrip("/")) + "/.default"
