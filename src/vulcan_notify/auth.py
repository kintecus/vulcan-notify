"""Playwright-based auth for eduVulcan - saves session cookies after browser login."""

import asyncio
import json
import logging
from pathlib import Path

import aiohttp
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

EDUVULCAN_LOGIN_URL = "https://eduvulcan.pl"


async def login_and_save_session(session_path: Path) -> dict:
    """Open browser for manual login, save session cookies after redirect to dashboard.

    Returns the session data dict (contains cookies and base_url).
    """
    login_complete = asyncio.Event()
    dashboard_url = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        def handle_navigation(frame):  # type: ignore[no-untyped-def]
            nonlocal dashboard_url
            url = frame.url
            if "uczen.eduvulcan.pl" in url and "/App/" in url:
                dashboard_url = url
                login_complete.set()

        page.on("framenavigated", handle_navigation)

        await page.goto(EDUVULCAN_LOGIN_URL)
        print("[auth] Browser opened. Please log in to eduVulcan...")

        try:
            await asyncio.wait_for(login_complete.wait(), timeout=300)
        except TimeoutError:
            await browser.close()
            raise TimeoutError("Login timed out after 5 minutes")

        # Let the dashboard finish loading
        await asyncio.sleep(2)

        cookies = await context.cookies()
        await browser.close()

    # Extract tenant from dashboard URL
    # e.g. https://uczen.eduvulcan.pl/{tenant}/App/.../tablica
    parts = dashboard_url.split("uczen.eduvulcan.pl/")
    tenant = parts[1].split("/")[0] if len(parts) > 1 else ""

    session_data = {
        "cookies": cookies,
        "tenant": tenant,
        "base_url": f"https://uczen.eduvulcan.pl/{tenant}",
        "dashboard_url": dashboard_url,
    }

    session_path.write_text(json.dumps(session_data, indent=2, default=str))
    print(f"[auth] Session saved to {session_path}")
    print(f"[auth] Tenant: {tenant}")
    print(f"[auth] Base URL: {session_data['base_url']}")

    return session_data


def load_session(session_path: Path) -> dict:
    """Load a previously saved session."""
    if not session_path.exists():
        raise FileNotFoundError(f"No session file at {session_path}. Run 'vulcan-notify auth' first.")
    return json.loads(session_path.read_text())


def cookies_for_url(session_data: dict, url: str) -> dict[str, str]:
    """Build a Cookie header dict for a given URL from saved session cookies."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""

    matching = {}
    for cookie in session_data["cookies"]:
        domain = cookie.get("domain", "")
        # Match exact domain or parent domain (with leading dot)
        if host == domain or host.endswith(domain.lstrip(".")):
            matching[cookie["name"]] = cookie["value"]

    return matching


def _make_ssl_context():  # type: ignore[no-untyped-def]
    """Create an SSL context using certifi's CA bundle."""
    import ssl

    import certifi

    return ssl.create_default_context(cafile=certifi.where())


async def test_session(session_data: dict) -> bool:
    """Test if the session is still valid by hitting the Context API."""
    base_url = session_data["base_url"]
    ssl_ctx = _make_ssl_context()
    url = f"{base_url}/api/Context"
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies_for_url(session_data, url).items())

    async with aiohttp.ClientSession() as session:
        async with session.get(url, ssl=ssl_ctx, headers={"Cookie": cookie_header}) as resp:
            text = await resp.text()
            content_type = resp.headers.get("content-type", "")

            print(f"[auth] Response: status={resp.status} content-type={content_type} len={len(text)}")

            if resp.status != 200:
                print(f"[auth] Session invalid: status {resp.status}")
                print(f"[auth] Body: {text[:300]}")
                return False

            # If we got HTML back, the session is expired (redirect to login)
            if "text/html" in content_type:
                print("[auth] Got HTML instead of JSON - session expired or cookies not sent correctly")
                print(f"[auth] Body preview: {text[:300]}")
                return False

            try:
                data = json.loads(text)
                print(f"[auth] Session valid! Got JSON response.")
                print(f"[auth] Data: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
                return True
            except json.JSONDecodeError:
                print(f"[auth] Unexpected response: {text[:300]}")
                return False
