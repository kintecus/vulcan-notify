"""Main polling loop - fetches data from Vulcan API and detects changes."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from iris.api import IrisHebeCeApi
from iris.credentials import RsaCredential  # noqa: TC002

from vulcan_notify.config import settings
from vulcan_notify.db import Database  # noqa: TC001
from vulcan_notify.differ import (
    Change,
    detect_announcement_changes,
    detect_attendance_changes,
    detect_grade_changes,
    detect_message_changes,
)
from vulcan_notify.notifier import send_notification

logger = logging.getLogger(__name__)


class Poller:
    def __init__(self, credential: RsaCredential, db: Database) -> None:
        self.credential = credential
        self.db = db
        self.api = IrisHebeCeApi(credential)
        self._account: object | None = None

    async def _get_account(self) -> object:
        """Get the first account (cached)."""
        if self._account is None:
            accounts = await self.api.get_accounts()
            if not accounts:
                raise RuntimeError("No accounts found for this credential")
            self._account = accounts[0]
            logger.info("Using account: %s", getattr(self._account, "pupil", self._account))
        return self._account

    async def poll_once(self) -> list[Change]:
        """Run a single poll cycle. Returns list of detected changes."""
        account = await self._get_account()
        all_changes: list[Change] = []

        # Extract common params from account
        rest_url = account.unit.rest_url  # type: ignore[union-attr]
        pupil_id = account.pupil.id  # type: ignore[union-attr]
        unit_id = account.unit.id  # type: ignore[union-attr]
        period_id = account.periods[-1].id  # type: ignore[union-attr]

        # Grades
        try:
            grades = await self.api.get_grades(
                rest_url=rest_url,
                unit_id=unit_id,
                pupil_id=pupil_id,
                period_id=period_id,
            )
            changes = await detect_grade_changes(grades, self.db)
            all_changes.extend(changes)
            logger.debug("Grades: %d total, %d changes", len(grades), len(changes))
        except Exception:
            logger.exception("Failed to fetch grades")

        # Messages (may be gated by subscription)
        try:
            # box parameter is the mailbox key from the account
            box = getattr(account, "message_box", None)
            if box:
                messages = await self.api.get_received_messages(
                    rest_url=rest_url,
                    box=box.key,  # type: ignore[union-attr]
                    pupil_id=pupil_id,
                )
                changes = await detect_message_changes(messages, self.db)
                all_changes.extend(changes)
                logger.debug("Messages: %d total, %d changes", len(messages), len(changes))
            else:
                logger.debug("No message box available (may require subscription)")
        except Exception:
            logger.exception("Failed to fetch messages (may require eduVulcan+ subscription)")

        # Attendance
        try:
            today = datetime.now()
            date_from = today - timedelta(days=1)
            date_to = today + timedelta(days=1)
            attendance = await self.api.get_presence_extra(
                rest_url=rest_url,
                pupil_id=pupil_id,
                date_from=date_from,
                date_to=date_to,
            )
            changes = await detect_attendance_changes(attendance, self.db)
            all_changes.extend(changes)
            logger.debug("Attendance: %d total, %d changes", len(attendance), len(changes))
        except Exception:
            logger.exception("Failed to fetch attendance")

        # Announcements
        try:
            announcements = await self.api.get_announcements(
                rest_url=rest_url,
                pupil_id=pupil_id,
            )
            changes = await detect_announcement_changes(announcements, self.db)
            all_changes.extend(changes)
            logger.debug("Announcements: %d total, %d changes", len(announcements), len(changes))
        except Exception:
            logger.exception("Failed to fetch announcements")

        return all_changes

    async def notify_changes(self, changes: list[Change]) -> None:
        """Send notifications for all detected changes."""
        for change in changes:
            await send_notification(
                title=change.title,
                body=change.body,
                priority=change.priority,
                tags=change.tags,
            )

    async def run(self) -> None:
        """Main polling loop. Runs until cancelled."""
        logger.info(
            "Starting poller (interval: %ds, topic: %s)",
            settings.poll_interval,
            settings.ntfy_topic,
        )

        # First run: populate initial state without notifications
        first_run = await self.db.get_state("initialized")
        if first_run is None:
            logger.info("First run - populating initial state (no notifications)")
            await self.poll_once()
            await self.db.set_state("initialized", "true")
            logger.info("Initial state populated. Future changes will trigger notifications.")
        else:
            # Normal poll
            changes = await self.poll_once()
            if changes:
                logger.info("Detected %d change(s), sending notifications", len(changes))
                await self.notify_changes(changes)
            else:
                logger.debug("No changes detected")

        # Continue polling
        while True:
            await asyncio.sleep(settings.poll_interval)
            try:
                changes = await self.poll_once()
                if changes:
                    logger.info("Detected %d change(s), sending notifications", len(changes))
                    await self.notify_changes(changes)
                else:
                    logger.debug("No changes detected")
            except Exception:
                logger.exception("Poll cycle failed, will retry next interval")
