"""Entry point for vulcan-notify service."""

import asyncio
import logging
import sys

from vulcan_notify.auth import load_session, login_and_save_session, test_session
from vulcan_notify.config import settings


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


async def cmd_run() -> None:
    """Start the polling service."""
    print("Not yet implemented - run 'auth' and 'test' first to validate.")
    sys.exit(1)


def main() -> None:
    setup_logging()

    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    match command:
        case "auth":
            asyncio.run(cmd_auth())
        case "test":
            asyncio.run(cmd_test())
        case "run":
            asyncio.run(cmd_run())
        case _:
            print("Usage: vulcan-notify [auth|test|run]")
            print("  auth  - Interactive login and save session")
            print("  test  - Test if saved session is valid")
            print("  run   - Start polling service (default)")
            sys.exit(1)


if __name__ == "__main__":
    main()
