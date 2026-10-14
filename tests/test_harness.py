import json

from copilot.evaluation import harness
from copilot.evaluation.__main__ import main
from copilot.extraction import ModelCopilot
from copilot.llm import ModelClient


def test_one_command_scores_the_baseline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(harness, "model_configured", lambda: False)  # never a paid call
    assert main(["--limit", "15", "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "| **Ticket fields** |" in printed
    assert "| **PII leakage** |" in printed
    report = json.loads((tmp_path / "eval-dev.json").read_text())
    assert report["threads"] == 15
    assert "baseline" in report["systems"]
    assert (tmp_path / "results-dev.jsonl").read_text().count("\n") == 15


def test_copilot_needs_a_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(harness, "model_configured", lambda: False)
    assert main(["--system", "copilot", "--limit", "2", "--out", str(tmp_path)]) == 2
    assert "needs a model" in capsys.readouterr().err


def test_copilot_is_scored_against_the_baseline(tmp_path, monkeypatch, capsys):
    from tests.test_extraction import FakeModel, settings

    monkeypatch.setattr(harness, "model_configured", lambda: True)
    monkeypatch.setitem(
        harness.SYSTEMS,
        "copilot",
        lambda: ModelCopilot(ModelClient(settings(), completion=FakeModel())),
    )
    assert main(["--limit", "5", "--no-redaction", "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "| Metric | baseline | copilot | vs baseline |" in printed
    report = json.loads((tmp_path / "eval-dev.json").read_text())
    copilot = report["systems"]["copilot"]["metrics"]
    assert copilot["pii.leakage_rate"] == 0
    assert copilot["cost.calls_per_ticket"] >= 1
    assert report["redaction"] is None


def test_redaction_section_runs_offline(tmp_path, capsys):
    assert main(["--system", "baseline", "--limit", "5", "--out", str(tmp_path)]) == 0
    assert "### Redaction" in capsys.readouterr().out
    report = json.loads((tmp_path / "eval-dev.json").read_text())
    assert report["redaction"]["redaction.leakage_rate"] == 0
