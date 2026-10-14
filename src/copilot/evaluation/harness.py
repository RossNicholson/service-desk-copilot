"""Runs systems over a split and scores them."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from copilot.baseline import BaselineCopilot
from copilot.directory import load_directory
from copilot.evaluation.dataset import LabelledThread, load_split
from copilot.evaluation.metrics import all_metrics
from copilot.llm import SpendCapReached, model_configured
from copilot.pipeline import ModelCopilot
from copilot.schemas import ThreadResult

# Every system the harness knows how to build. The copilot needs a model, so it only
# runs when one is configured (see `available_systems`).
SYSTEMS: dict[str, Callable[[], object]] = {
    "baseline": BaselineCopilot,
    "copilot": ModelCopilot,
}
NEEDS_MODEL = {"copilot"}


def available_systems() -> list[str]:
    """Systems that can run now, baseline first."""
    ready = model_configured()
    return [n for n in SYSTEMS if n not in NEEDS_MODEL or ready]


@dataclass
class SystemRun:
    system: str
    metrics: dict[str, float | None]
    results: list[ThreadResult] = field(repr=False)
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def run_system(name: str, threads: list[LabelledThread], judge=None) -> SystemRun:
    system = SYSTEMS[name]()
    results, pairs, errors = [], [], []
    started = time.monotonic()
    for lt in threads:
        try:
            result = system.process(lt.thread, now=lt.labels.timing.observed_at)
        except SpendCapReached as error:
            errors.append(f"{lt.thread.thread_id}: stopped, {error}")
            break
        except Exception as error:  # a crash on one thread should not hide the rest
            errors.append(f"{lt.thread.thread_id}: {type(error).__name__}: {error}")
            result = ThreadResult(thread_id=lt.thread.thread_id, system=name, fields={})
        results.append(result)
        pairs.append((lt, result))
    seconds = time.monotonic() - started
    metrics = all_metrics(pairs, load_directory().support_address)
    if judge:
        from copilot.evaluation.judge import judge_metrics

        metrics.update(judge_metrics(pairs, judge()))
    metrics["run.errors"] = float(len(errors))
    return SystemRun(system=name, metrics=metrics, results=results, seconds=seconds, errors=errors)


def evaluate(
    split: str, systems: list[str], limit: int | None = None, judge=None
) -> list[SystemRun]:
    threads = load_split(split)
    if limit:
        threads = threads[:limit]
    return [run_system(name, threads, judge) for name in systems]
