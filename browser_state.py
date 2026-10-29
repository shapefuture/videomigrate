"""Validate a Rumble Playwright storage state loaded from a GitHub Actions secret."""
import base64
import json
from urllib.parse import urlsplit

RUMBLE_HOST = "rumble.com"


def is_site_host(host, root):
    return bool(host) and (host == root or host.endswith("." + root))


def restricted_storage_state(raw):
    """Decode the secret and discard anything that is not a rumble.com cookie or origin."""
    # GitHub Secrets copied from a text file may contain trailing newlines.
    # Remove ONLY whitespace, then require strictly valid Base64.
    cleaned = "".join(raw.split())
    obj = json.loads(base64.b64decode(cleaned, validate=True).decode("utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("cookies"), list):
        raise RuntimeError("Invalid Playwright storage state JSON")
    for c in obj["cookies"]:
        domain = str(c.get("domain", "")).lstrip(".")
        if not is_site_host(domain, RUMBLE_HOST):
            raise RuntimeError("Storage state contains an unrelated cookie domain; refuse to load")
    for origin in obj.get("origins", []):
        url = urlsplit(origin.get("origin", ""))
        if url.scheme != "https" or not is_site_host(url.hostname, RUMBLE_HOST):
            raise RuntimeError("Storage state contains an unrelated localStorage origin")
    return obj
