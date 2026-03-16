"""Playwright-based auth for eduVulcan - saves session cookies after browser login."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import ssl  # noqa: TC003
import subprocess
from pathlib import Path  # noqa: TC003
from typing import Any

import aiohttp
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "vulcan-notify"


def get_keychain_credentials() -> tuple[str, str] | None:
    """Read login and password from macOS Keychain (service: vulcan-notify).

    Returns (login, password) or None if not available.
    """
    if platform.system() != "Darwin":
        return None

    try:
        # Get account name (login)
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None

        account = ""
        for line in result.stdout.splitlines():
            # Format: "acct"<blob>="the.email@example.com"
            if '"acct"' in line and "=" in line:
                account = line.split("=", 1)[1].strip().strip('"')
                break

        if not account:
            return None

        # Get password
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None

        password = result.stdout.strip()
        if not password:
            return None

        return (account, password)
    except FileNotFoundError:
        return None

EDUVULCAN_LOGIN_URL = "https://eduvulcan.pl"


async def login_and_save_session(session_path: Path) -> dict[str, Any]:
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
        except TimeoutError as exc:
            await browser.close()
            raise TimeoutError("Login timed out after 5 minutes") from exc

        # Let the dashboard finish loading
        await asyncio.sleep(2)

        # Extract tenant from dashboard URL before navigating away
        parts = dashboard_url.split("uczen.eduvulcan.pl/")
        tenant = parts[1].split("/")[0] if len(parts) > 1 else ""

        # Visit messages subdomain to establish its session cookies
        if tenant:
            print("[auth] Establishing messages session...")
            await page.goto(
                f"https://wiadomosci.eduvulcan.pl/{tenant}/App",
                wait_until="networkidle",
            )
            await asyncio.sleep(2)

        cookies = await context.cookies()
        await browser.close()

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


async def auto_login(session_path: Path, login: str, password: str) -> dict[str, Any]:
    """Headless auto-login using stored credentials.

    Steps:
    1. Navigate to eduvulcan.pl/logowanie
    2. Fill login, click Dalej
    3. Fill password, click Zaloguj
    4. Wait for redirect to eduvulcan.pl dashboard
    5. Click first student to trigger redirect to uczen.eduvulcan.pl
    6. Capture cookies and save session
    """
    dashboard_url = ""
    login_complete = asyncio.Event()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        def handle_navigation(frame):  # type: ignore[no-untyped-def]
            nonlocal dashboard_url
            url = frame.url
            if "uczen.eduvulcan.pl" in url and "/App/" in url:
                dashboard_url = url
                login_complete.set()

        page.on("framenavigated", handle_navigation)

        logger.info("Auto-login: navigating to login page")
        await page.goto("https://eduvulcan.pl/logowanie")

        # Dismiss cookie consent overlay if present
        cookie_wrapper = page.locator("#respect-privacy-wrapper")
        if await cookie_wrapper.count():
            # Try clicking accept/close button inside the cookie iframe
            cookie_frame = page.frame_locator("#respect-privacy-frame")
            for btn_text in ["Akceptuję", "Zgadzam", "OK", "Zamknij"]:
                btn = cookie_frame.locator(f'button:has-text("{btn_text}")').first
                try:
                    await btn.click(timeout=3000)
                    await asyncio.sleep(0.5)
                    break
                except Exception:
                    continue
            else:
                # If no button found, remove the overlay via JS
                await page.evaluate("""
                    () => {
                        const el = document.getElementById('respect-privacy-wrapper');
                        if (el) el.remove();
                    }
                """)

        # Step 1: fill login
        await page.fill('input[type="text"], input[name="login"], input[type="email"]', login)
        await page.click('button:has-text("Dalej")')

        # Step 2: fill password
        await page.wait_for_selector('input[type="password"]', timeout=10000)
        await page.fill('input[type="password"]', password)
        await page.click('button:has-text("Zaloguj")')

        # Wait for eduvulcan.pl dashboard to load (student picker)
        await page.wait_for_url("**/eduvulcan.pl/**", timeout=30000)
        await asyncio.sleep(2)

        # Step 3: click first student to trigger redirect to uczen.eduvulcan.pl
        # Try multiple strategies to find a clickable student entry
        clicked = False
        for selector in [
            'a[href*="uczen.eduvulcan.pl"]',
            'a[href*="/App/"]',
            '[class*="student"]',
            '[class*="uczen"]',
            '[class*="account"]',
        ]:
            loc = page.locator(selector).first
            if await loc.count():
                await loc.click()
                clicked = True
                break

        if not clicked:
            # Last resort: use JavaScript to find and click the first student link
            await page.evaluate("""
                () => {
                    const links = document.querySelectorAll('a[href]');
                    for (const link of links) {
                        if (link.href.includes('uczen.eduvulcan.pl')) {
                            link.click();
                            return;
                        }
                    }
                }
            """)

        try:
            await asyncio.wait_for(login_complete.wait(), timeout=30)
        except TimeoutError as exc:
            await browser.close()
            raise TimeoutError(
                "Auto-login: timed out waiting for redirect to uczen.eduvulcan.pl"
            ) from exc

        await asyncio.sleep(2)

        # Extract tenant
        parts = dashboard_url.split("uczen.eduvulcan.pl/")
        tenant = parts[1].split("/")[0] if len(parts) > 1 else ""

        # Visit messages subdomain
        if tenant:
            logger.info("Auto-login: establishing messages session")
            await page.goto(
                f"https://wiadomosci.eduvulcan.pl/{tenant}/App",
                wait_until="networkidle",
            )
            await asyncio.sleep(2)

        cookies = await context.cookies()
        await browser.close()

    session_data = {
        "cookies": cookies,
        "tenant": tenant,
        "base_url": f"https://uczen.eduvulcan.pl/{tenant}",
        "dashboard_url": dashboard_url,
    }

    session_path.write_text(json.dumps(session_data, indent=2, default=str))
    logger.info("Auto-login: session saved to %s (tenant: %s)", session_path, tenant)

    return session_data


def load_session(session_path: Path) -> dict[str, Any]:
    """Load a previously saved session."""
    if not session_path.exists():
        raise FileNotFoundError(
            f"No session file at {session_path}. Run 'vulcan-notify auth' first."
        )
    data: dict[str, Any] = json.loads(session_path.read_text())
    return data


def cookies_for_url(session_data: dict[str, Any], url: str) -> dict[str, str]:
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


def _make_ssl_context() -> ssl.SSLContext:
    """Create an SSL context using certifi's CA bundle."""
    import ssl

    import certifi

    return ssl.create_default_context(cafile=certifi.where())


async def test_session(session_data: dict[str, Any]) -> bool:
    """Test if the session is still valid by hitting the Context API."""
    base_url = session_data["base_url"]
    ssl_ctx = _make_ssl_context()
    url = f"{base_url}/api/Context"
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies_for_url(session_data, url).items())

    headers = {
        "Cookie": cookie_header,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    async with (
        aiohttp.ClientSession() as session,
        session.get(url, ssl=ssl_ctx, headers=headers) as resp,
    ):
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
            print("[auth] Session valid! Got JSON response.")
            print(f"[auth] Data: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
            return True
        except json.JSONDecodeError:
            print(f"[auth] Unexpected response: {text[:300]}")
            return False
