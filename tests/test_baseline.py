import pytest

from copilot.baseline import BaselineCopilot
from copilot.evaluation.dataset import load_split
from copilot.schemas import FIELD_NAMES


@pytest.fixture(scope="module")
def dev():
    return load_split("dev")[:60]


@pytest.fixture(scope="module")
def results(dev):
    baseline = BaselineCopilot()
    return [baseline.process(lt.thread, lt.labels.timing.observed_at) for lt in dev]


def test_baseline_predicts_every_field(results):
    for result in results:
        assert set(result.fields) == set(FIELD_NAMES)
        for prediction in result.fields.values():
            assert 0 <= prediction.confidence <= 1
            assert prediction.reason


def test_baseline_uses_no_model(results):
    assert all(not r.model_inputs and r.usage.cost_usd == 0 for r in results)


def test_caller_is_first_sender(dev, results):
    for lt, result in zip(dev, results, strict=True):
        assert result.fields["caller"].value == lt.thread.messages[0].from_address


def test_silence_alerts_only_without_a_reply(dev, results):
    for lt, result in zip(dev, results, strict=True):
        replied = any(e.from_address == "support@northgate-ms.example" for e in lt.thread.messages)
        if replied:
            assert not [a for a in result.alerts if a.kind == "silence"]


def test_one_note_per_client_email(dev, results):
    for lt, result in zip(dev, results, strict=True):
        inbound = [m for m in lt.labels.messages if m.direction == "inbound"]
        assert [n.message_index for n in result.notes] == [m.index for m in inbound]
