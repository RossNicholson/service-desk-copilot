"""The model renderer, with the model replaced by a fake. No API calls are made."""

import json
from types import SimpleNamespace

import pytest

from datagen.plan import plan_split
from datagen.render_model import ModelRenderer, SpendCapReached, load_prompt
from datagen.render_template import TemplateRenderer


def fake_completion(world, plan, *, drop_facts=False):
    """Returns what a well-behaved model would, using the template renderer's text."""
    rendered = TemplateRenderer(world, 0).render(plan)
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        bodies = rendered.bodies
        if drop_facts:
            bodies = ["Hello, it's broken. Thanks" for _ in bodies]
        content = {
            "messages": [{"index": i, "body": b} for i, b in enumerate(bodies)],
            "forwarded_original": rendered.original,
        }
        message = SimpleNamespace(content=json.dumps(content))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return completion, calls


@pytest.fixture
def plan(world, mix):
    plans = plan_split(world, mix, "dev", 40, seed=8)
    return next(p for p in plans if p.details_stated and len(p.messages) > 1)


def test_prompt_has_system_and_user_sections():
    prompt = load_prompt()
    assert set(prompt) == {"system", "user"}
    assert "British English" in prompt["system"]
    assert "—" not in prompt["system"] + prompt["user"]


def test_renders_and_passes_facts_to_the_model(world, plan):
    completion, calls = fake_completion(world, plan)
    renderer = ModelRenderer(world, 0, max_cost=1, completion=completion)
    renderer.min_interval = 0
    rendered = renderer.render(plan)
    assert len(rendered.bodies) == len(plan.messages)
    user = calls[0]["messages"][1]["content"]
    for fact in plan.details_stated:
        assert fact.text in user


def test_missing_facts_are_rejected_after_retries(world, plan):
    completion, calls = fake_completion(world, plan, drop_facts=True)
    renderer = ModelRenderer(world, 0, max_cost=1, completion=completion)
    renderer.min_interval = 0
    with pytest.raises(RuntimeError, match="failed checks"):
        renderer.render(plan)
    assert len(calls) == 3


def test_spend_cap_stops_calls(world, plan):
    completion, calls = fake_completion(world, plan)
    renderer = ModelRenderer(world, 0, max_cost=0.5, completion=completion)
    renderer.cost_usd = 0.5
    with pytest.raises(SpendCapReached):
        renderer.render(plan)
    assert calls == []


def test_api_key_is_passed_but_not_in_prompt(world, plan, monkeypatch):
    from copilot.config import get_settings

    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-real")
    get_settings.cache_clear()
    try:
        completion, calls = fake_completion(world, plan)
        renderer = ModelRenderer(world, 0, max_cost=1, completion=completion)
        renderer.min_interval = 0
        renderer.render(plan)
    finally:
        get_settings.cache_clear()
    assert calls[0]["api_key"] == "sk-test-not-real"
    assert "sk-test" not in json.dumps(calls[0]["messages"])
