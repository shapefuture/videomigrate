#!/usr/bin/env python3
"""Create a FRESH Rumble Playwright state for GitHub Actions privately.

Run on a computer or an authenticated remote desktop with a visible Chromium
GUI. Does NOT upload videos. Does NOT print cookies, JWTs, or the resulting
base64 secret. Requires a FRESH login after invalidating previously exposed
Rumble sessions. Never commit/share the output file.

python -m pip install 'playwright>=1.51,<3'
python -m playwright install chromium
python rumble_capture_login.py
"""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DEST = ROOT / ".private_browser_sessions" / "RUMBLE_BROWSER_STATE_B64.txt"


def owned_host(domain):
    h = str(domain or "").lstrip(".").lower()
    return h == "rumble.com" or h.endswith(".rumble.com")


def extract_site_state(state):
    """Never include unrelated website cookies or local-storage origins."""
    cookies = [x for x in state.get("cookies", [])
               if isinstance(x, dict) and owned_host(x.get("domain"))]
    origins = [x for x in state.get("origins", [])
               if isinstance(x, dict) and urlsplit(x.get("origin", "")).scheme == "https"
               and owned_host(urlsplit(x["origin"]).hostname)]
    if not cookies:
        raise RuntimeError("No Rumble cookies were found; authenticate first.")
    return {"cookies": cookies, "origins": origins}


def capture():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Install Playwright: python -m pip install playwright") from None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://studio.rumble.com/", wait_until="domcontentloaded",
                  timeout=60000)
        print("Sign in to YOUR Rumble account in the Chromium window.")
        print("Finish any MFA. Navigate to https://studio.rumble.com/ when signed in.")
        input("After verifying you are logged into the correct Studio account, press ENTER here: ")
        page.goto("https://studio.rumble.com/", wait_until="domcontentloaded",
                  timeout=60000)
        # IndexedDB support was added in Playwright 1.51.
        state = extract_site_state(context.storage_state(indexed_db=True))
        browser.close()
    encoded = base64.b64encode(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    DEST.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if DEST.exists():
        raise RuntimeError("State already exists at destination. Move/delete it manually before rerunning.")
    # PRIVATE file permissions on Unix; other OS users must protect the folder.
    with DEST.open("xb") as f:
        f.write(encoded)
    try:
        DEST.chmod(0o600)
    except OSError:
        pass
    print("Fresh Rumble login state created. It was NOT printed.")
    print("Open ONLY this local file in your private editor:", DEST)
    print("Copy its single entire line DIRECTLY into GitHub Actions secret RUMBLE_BROWSER_STATE_B64.")
    print("Do not commit or send it through chat. Delete the local file after saving the secret.")
    print("Then run Rumble Studio GraphQL and chunk inspection with use_saved_login=true.")
    print("This ONLY tests login/inspection, NOT uploads.")

if __name__ == "__main__":
    try:
        capture()
    except Exception as exc:
        # Avoid printing raw Browser API errors that could include account URLs.
        print("Rumble state capture stopped:", type(exc).__name__, str(exc)[:150])
        raise SystemExit(1)
