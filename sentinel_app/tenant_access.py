"""Reviewed, tenant-bound Azure access elevation for the signed-in user only."""
import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .auth_recovery import AuthenticationRequired, azure_error
from .lighthouse_onboarding import Stop, guid, require

RESOURCE = "https://management.core.windows.net/"
ENDPOINT = "https://management.azure.com/providers/Microsoft.Authorization/elevateAccess?api-version=2015-07-01"
AUDIENCES = {RESOURCE, "https://management.azure.com/", "https://management.azure.com",
             "797f4846-ba00-4fd7-ba43-dac1f8f63013"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the ARM credential to a redirect destination.


def identity_token(session, tenant):
    tenant = guid(tenant, "client tenant ID")
    require(session.signed_in, "Sign in to the client tenant first.")
    require(session.az("cloud", "show").get("name") == "AzureCloud",
            "Azure access management currently supports Azure public cloud only.")
    account = session.az("account", "show")
    require(account.get("user", {}).get("type", "").lower() == "user",
            "Sign in with your authorized Global Administrator user account.")
    require(str(account.get("tenantId", "")).lower() == tenant,
            "The active account belongs to another tenant. Sign in to the client tenant again.")
    # Explicit --tenant works even when login returned only a tenant-level account.
    result = session.az("account", "get-access-token", "--tenant", tenant, "--resource", RESOURCE)
    token = result.get("accessToken", "")
    try:
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        require(isinstance(claims, dict), "Invalid ARM token claims.")
    except (IndexError, ValueError, TypeError, UnicodeError):
        raise Stop("Could not confirm the ARM token identity. Sign in again.") from None
    # Local claim inspection binds the review, not authorization. ARM verifies the
    # token signature and the caller's active Global Administrator role on POST.
    require(str(claims.get("tid", "")).lower() == tenant, "ARM token tenant does not match the client.")
    require(claims.get("aud") in AUDIENCES and claims.get("idtyp") != "app",
            "A delegated Azure Resource Manager user token is required.")
    principal = guid(claims.get("oid", ""), "signed-in user object ID")
    require(isinstance(claims.get("exp"), (int, float)) and claims["exp"] > time.time() + 30,
            "The ARM token is expired or about to expire. Sign in again.")
    return dict(tenant=tenant, principal=principal,
                account=claims.get("preferred_username") or claims.get("upn") or account["user"].get("name", "Unknown")), token


def access_management_plan(session, tenant):
    identity, _ = identity_token(session, tenant)
    return dict(identity, scope="/", created=time.time())  # Never retain a token in the plan or logs.


def enable_access_management(session, plan):
    require(plan.get("scope") == "/" and 0 <= time.time() - plan["created"] < 900,
            "Access-management review expired. Review the account and tenant again.")
    identity, token = identity_token(session, plan["tenant"])
    require(all(identity[key] == plan[key] for key in ("tenant", "principal", "account")),
            "The signed-in identity changed. Review Azure access management again.")
    request = Request(ENDPOINT, data=b"", method="POST",
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=45) as response:
            require(response.status == 200, "Azure did not confirm access elevation. Check the portal before retrying.")
    except HTTPError as error:
        details = error.read(16384).decode("utf-8", errors="replace").replace(token, "[omitted]")
        problem = azure_error(details, plan["tenant"])
        if isinstance(problem, AuthenticationRequired):
            raise problem from None
        if error.code == 403:
            raise Stop("Azure denied access elevation. Activate Global Administrator in this client tenant "
                       "through your approved access process, sign in again, and review the request. "
                       "Contributor or User Access Administrator alone cannot enable this setting. Azure policies also apply.") from None
        if error.code == 401:
            raise AuthenticationRequired("Azure requires fresh authentication for access management. " + details,
                                         plan["tenant"]) from None
        raise azure_error("Azure access-management request failed (HTTP " + str(error.code) + "). " + details,
                          plan["tenant"]) from None
    except (URLError, TimeoutError, OSError):
        raise Stop("Azure did not confirm the request outcome. Check Access management for Azure resources "
                   "in the client portal before trying again; the request may have succeeded.") from None
    return ("Azure accepted access management for " + identity["account"] + " in tenant " + identity["tenant"]
            + ". User Access Administrator at root scope (/) remains assigned until removed. "
            "Signing out or ending CyberQP access does not remove it. Turn the setting off in "
            "Microsoft Entra ID > Properties when finished. If subscriptions remain unavailable, "
            "allow propagation, then refresh or sign out and sign back in.")
