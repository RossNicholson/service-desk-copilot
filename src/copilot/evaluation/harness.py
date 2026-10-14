"""Runs systems over a split and scores them."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from copilot.baseline import BaselineCopilot
from copilot.directory import load_directory
from copilot.evaluation.dataset import LabelledThread, load_split
from copilot.evaluation.metrics import all_metrics
from copilot.schemas import ThreadResult

# Every system the harness knows how to build. Later phases add the copilot here.
SYSTEMS: dict[str, Callable[[], object]] = {
    "baseline": BaselineCopilot,
}


@dataclass
class SystemRun:
    system: str
    metrics: dict[str, float | None]
    results: list[ThreadResult] = field(repr=False)
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def run_system(name: str, threads: list[LabelledThread]) -> SystemRun:
    system = SYSTEMS[name]()
    results, pairs, errors = [], [], []
    started = time.monotonic()
    for lt in threads:
        try:
            result = system.process(lt.thread, now=lt.labels.timing.observed_at)
        except Exception as error:  # a crash on one thread should not hide the rest
            errors.append(f"{lt.thread.thread_id}: {type(error).__name__}: {error}")
            result = ThreadResult(thread_id=lt.thread.thread_id, system=name, fields={})
        results.append(result)
        pairs.append((lt, result))
    seconds = time.monotonic() - started
    metrics = all_metrics(pairs, load_directory().support_address)
    metrics["run.errors"] = float(len(errors))
    return SystemRun(system=name, metrics=metrics, results=results, seconds=seconds, errors=errors)


def evaluate(split: str, systems: list[str], limit: int | None = None) -> list[SystemRun]:
    threads = load_split(split)
    if limit:
        threads = threads[:limit]
    return [run_system(name, threads) for name in systems]
