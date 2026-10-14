"""Scheduled check-ins with APScheduler.

Every ``CHECKIN_INTERVAL_MINUTES`` (5 by default) the scheduler runs
`CopilotService.check_in`: tickets with new emails are processed again, every
open ticket is checked for silence, and each alert is sent once. See
`copilot.service` for what a check-in does.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from copilot.config import Settings, get_settings
from copilot.service import CopilotService

log = logging.getLogger(__name__)


def _run(service: CopilotService) -> None:
    report = service.check_in()
    if report.processed or report.alerts:
        log.info(
            "Check-in: %d tickets processed, %d alerts sent",
            len(report.processed),
            len(report.alerts),
        )


def start(service: CopilotService, settings: Settings | None = None) -> BackgroundScheduler:
    """Run the check-in in the background, starting now."""
    settings = settings or get_settings()
    scheduler = BackgroundScheduler(timezone=UTC)
    scheduler.add_job(
        _run,
        "interval",
        args=[service],
        minutes=settings.checkin_interval_minutes,
        id="checkin",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()
    return scheduler
