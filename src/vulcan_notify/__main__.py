"""Entry point for vulcan-notify service."""

import asyncio
import logging
import sys

from vulcan_notify.auth import load_session, login_and_save_session, test_session
from vulcan_notify.client import SessionExpiredError, VulcanClient
from vulcan_notify.config import settings
from vulcan_notify.db import Database
from vulcan_notify.display import format_sync_results
from vulcan_notify.sync import sync_all


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def cmd_auth() -> None:
    """Interactive auth flow - browser login and save session cookies."""
    await login_and_save_session(settings.session_file)


async def cmd_test() -> None:
    """Test if saved session is still valid."""
    session = load_session(settings.session_file)
    valid = await test_session(session)
    if not valid:
        print("Session expired. Run 'vulcan-notify auth' to re-authenticate.")
        sys.exit(1)
    print("Session is valid.")


async def cmd_sync() -> None:
    """Fetch latest data and show changes since last sync."""
    session = load_session(settings.session_file)
    client = VulcanClient(session)
    db = Database(settings.db_path)
    await db.connect()

    try:
        results = await sync_all(client, db)

        if not results:
            print("No students found.")
            sys.exit(1)

        output = format_sync_results(results)
        print(output)

    except SessionExpiredError:
        print("Session expired. Run 'vulcan-notify auth' to re-authenticate.")
        sys.exit(1)
    finally:
        await client.close()
        await db.close()


def main() -> None:
    setup_logging()

    command = sys.argv[1] if len(sys.argv) > 1 else "sync"

    match command:
        case "auth":
            asyncio.run(cmd_auth())
        case "test":
            asyncio.run(cmd_test())
        case "sync":
            asyncio.run(cmd_sync())
        case _:
            print("Usage: vulcan-notify [auth|test|sync]")
            print("  auth  - Interactive login and save session")
            print("  test  - Test if saved session is valid")
            print("  sync  - Fetch latest data and show changes (default)")
            sys.exit(1)


if __name__ == "__main__":
    main()
