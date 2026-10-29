#!/usr/bin/env python3
"""Refresh management OAuth safely and check granted YouTube scopes.

Do not request NEW scopes in the refresh grant: Google refresh tokens may
only reuse scopes originally granted during the browser consent flow.
Never print tokens, client secrets, OAuth response bodies, or HTTP request URLs.
"""
import os
import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
REQUIRED_SECRETS = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_MANAGE_REFRESH_TOKEN")


class ManagementAuthorizationError(RuntimeError):
    pass


def get_management_credentials():
    missing = [key for key in REQUIRED_SECRETS if not os.environ.get(key)]
    if missing:
        raise ManagementAuthorizationError("Missing GitHub repository secret(s): " + ", ".join(missing))
    # Critically, no scopes override here. invalid_scope can occur if a
    # refresh request tries to add permissions not granted to the token.
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_MANAGE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
    )
    try:
        creds.refresh(Request())
    except RefreshError as ex:
        status = str(ex)
        if "invalid_scope" in status:
            diagnosis = "invalid_scope"
        elif "invalid_grant" in status:
            diagnosis = "invalid_grant"
        elif "invalid_client" in status:
            diagnosis = "invalid_client"
        else:
            diagnosis = "refresh_rejected"
        raise ManagementAuthorizationError(
            "Google OAuth refresh failed (" + diagnosis + "). "
            "Ensure the client ID and ROTATED secret match the exact OAuth client "
            "that issued YT_MANAGE_REFRESH_TOKEN. Re-authorize for " + MANAGE_SCOPE +
            " with access_type=offline and prompt=consent; then replace "
            "YT_MANAGE_REFRESH_TOKEN in GitHub Secrets. Do not paste credentials into chat."
        ) from None
    if not creds.token:
        raise ManagementAuthorizationError("Google refreshed no access token")
    # Google's tokeninfo tells us what the access token actually permits.
    try:
        response = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"access_token": creds.token}, timeout=20)
        response.raise_for_status()
        scope_field = response.json().get("scope", "")
    except (requests.RequestException, ValueError):
        raise ManagementAuthorizationError(
            "Could not check the access token's granted scopes; refusing management writes."
        ) from None
    granted = set(scope_field.split())
    if MANAGE_SCOPE not in granted:
        safe = ", ".join(sorted(x.rsplit("/", 1)[-1] for x in granted)) or "none"
        raise ManagementAuthorizationError(
            "Management token refresh succeeded, but NOT authorized for youtube.force-ssl. "
            "Granted permission names: " + safe + ". Re-authorize correct account in "
            "Google OAuth Playground using Step 1 scope " + MANAGE_SCOPE +
            " and exchange code in Step 2. Save the NEW refresh token in "
            "YT_MANAGE_REFRESH_TOKEN; do not replace the upload-only token."
        )
    print("OAuth: refresh succeeded; verified youtube.force-ssl permission. No tokens printed.", flush=True)
    return creds


if __name__ == "__main__":
    try:
        get_management_credentials()
        print("Management OAuth preflight PASSED. This script did not modify YouTube.")
    except ManagementAuthorizationError as exc:
        print("Management OAuth preflight FAILED:", exc)
        raise SystemExit(1)
