"""Scheduled check-ins: keep open tickets up to date and catch silence.

Every few minutes (``CHECKIN_INTERVAL_MINUTES``) the check-in reads each open
ticket. A ticket with new emails is processed again, so its fields, note and
chase alerts reflect the whole thread. Every ticket is also checked for silence
against the response target for its priority, which escalates to the team lead
at twice the target. Each alert is sent once.

Tickets come from a `TicketSource` and alerts go to a `Notifier`. Until the
Zammad adapter and Teams alerts arrive in Phase 6, `ThreadSource` serves threads
from memory and `CollectingNotifier` keeps the alerts it is given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from apscheduler.schedulers.background import BackgroundScheduler

from copilot.baseline import silence_alert
from copilot.chasing import escalate_silence
from copilot.config import Settings, get_settings
from copilot.directory import Directory, load_directory
from copilot.schemas import Alert, Thread, ThreadResult


class System(Protocol):
    name: str

    def process(self, thread: Thread, now: datetime) -> ThreadResult: ...


class TicketSource(Protocol):
    def open_threads(self) -> list[Thread]: ...


class Notifier(Protocol):
    def send(self, thread_id: str, alert: Alert) -> None: ...


@dataclass
class ThreadSource:
    threads: list[Thread] = field(default_factory=list)

    def open_threads(self) -> list[Thread]:
        return list(self.threads)


@dataclass
class CollectingNotifier:
    sent: list[tuple[str, Alert]] = field(default_factory=list)

    def send(self, thread_id: str, alert: Alert) -> None:
        self.sent.append((thread_id, alert))


@dataclass
class CheckInReport:
    processed: list[str] = field(default_factory=list)
    alerts: list[tuple[str, Alert]] = field(default_factory=list)


def _key(alert: Alert) -> tuple:
    return (alert.kind, alert.message_index, alert.notify)


@dataclass
class CheckIn:
    system: System
    source: TicketSource
    notifier: Notifier
    directory: Directory = field(default_factory=load_directory)
    results: dict[str, ThreadResult] = field(default_factory=dict)
    seen: dict[str, int] = field(default_factory=dict)
    sent: dict[str, set[tuple]] = field(default_factory=dict)

    def run(self, now: datetime | None = None) -> CheckInReport:
        now = now or datetime.now(UTC)
        report = CheckInReport()
        for thread in self.source.open_threads():
            tid = thread.thread_id
            if self.seen.get(tid) != len(thread.messages):
                self.results[tid] = self.system.process(thread, now)
                self.seen[tid] = len(thread.messages)
                report.processed.append(tid)
            for alert in self._alerts(thread, self.results[tid], now):
                if _key(alert) in self.sent.setdefault(tid, set()):
                    continue
                self.sent[tid].add(_key(alert))
                self.notifier.send(tid, alert)
                report.alerts.append((tid, alert))
        return report

    def _alerts(self, thread: Thread, result: ThreadResult, now: datetime) -> list[Alert]:
        """Chase alerts from the last processing, and silence as of now."""
        alerts = [a for a in result.alerts if a.kind == "chase"]
        priority = result.fields.get("priority")
        value = priority.value if priority else None
        silence = silence_alert(thread, value, now, self.directory)
        if silence:
            target = self.directory.response_targets_minutes[str(value)]
            alerts.append(escalate_silence(silence, now, target))
        return alerts


def start(checkin: CheckIn, settings: Settings | None = None) -> BackgroundScheduler:
    """Run the check-in in the background every ``checkin_interval_minutes``."""
    settings = settings or get_settings()
    scheduler = BackgroundScheduler(timezone=UTC)
    scheduler.add_job(
        checkin.run,
        "interval",
        minutes=settings.checkin_interval_minutes,
        id="checkin",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()
    return scheduler
