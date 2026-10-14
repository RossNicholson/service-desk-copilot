import json

from copilot.evaluation.__main__ import main


def test_one_command_scores_the_baseline(tmp_path, capsys):
    assert main(["--limit", "15", "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "| **Ticket fields** |" in printed
    assert "| **PII leakage** |" in printed
    report = json.loads((tmp_path / "eval-dev.json").read_text())
    assert report["threads"] == 15
    assert "baseline" in report["systems"]
    assert (tmp_path / "results-dev.jsonl").read_text().count("\n") == 15
