"""ntfy.sh notification delivery."""

import logging

import aiohttp

from vulcan_notify.config import settings

logger = logging.getLogger(__name__)


async def send_notification(
    title: str,
    message: str,
    *,
    priority: int = 3,
    tags: list[str] | None = None,
    click_url: str | None = None,
) -> bool:
    """Send a push notification via ntfy.sh.

    Args:
        title: Notification title.
        message: Notification body.
        priority: 1 (min) to 5 (max), default 3 (normal).
        tags: Optional emoji tags (e.g., ["grade", "school"]).
        click_url: URL to open when notification is tapped.

    Returns:
        True if sent successfully.
    """
    url = f"{settings.ntfy_server}/{settings.ntfy_topic}"

    headers: dict[str, str] = {
        "Title": title,
        "Priority": str(priority),
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, data=message.encode(), headers=headers) as resp,
        ):
            if resp.status == 200:
                logger.info("Notification sent: %s", title)
                return True
            logger.warning("ntfy.sh returned status %d: %s", resp.status, await resp.text())
            return False
    except Exception:
        logger.exception("Failed to send notification")
        return False
