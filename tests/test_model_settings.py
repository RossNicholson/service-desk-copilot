"""Model settings: optional temperature and configurable prices."""

from __future__ import annotations

from copilot.config import Settings


def test_temperature_is_unset_by_default(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    assert Settings(_env_file=None).llm_temperature is None


def test_blank_temperature_means_unset(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "")
    assert Settings(_env_file=None).llm_temperature is None


def test_temperature_can_still_be_set(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    assert Settings(_env_file=None).llm_temperature == 0.2


def test_configured_prices_drive_the_cost(monkeypatch):
    from types import SimpleNamespace

    from copilot.llm import _cost

    monkeypatch.setenv("LLM_INPUT_COST_PER_MTOK", "0.10")
    monkeypatch.setenv("LLM_OUTPUT_COST_PER_MTOK", "0.50")
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=200_000)
    )
    assert _cost(response, Settings(_env_file=None)) == 0.2
