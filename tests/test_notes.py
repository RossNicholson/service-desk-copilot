from copilot.directory import load_directory
from copilot.evaluation.dataset import load_split
from copilot.llm import ModelClient
from copilot.notes import checklist
from copilot.pipeline import ModelCopilot
from tests.test_extraction import FakeModel, settings


def run(fake, lt):
    system = ModelCopilot(ModelClient(settings(), completion=fake), load_directory())
    return system.process(lt.thread, lt.labels.timing.observed_at)


def test_note_items_need_a_real_quote():
    lt = load_split("dev")[0]
    note = run(FakeModel(invent=True), lt).notes[-1]
    assert note.reported  # quoted from the email, so kept
    assert note.tried == []  # "I reinstalled Windows twice" is not in the email, so dropped
    assert note.asks and "[PERSON_" not in note.asks[0]  # placeholders restored


def test_open_questions_come_from_the_checklist():
    lt = load_split("dev")[0]
    result = run(FakeModel(), lt)
    note = result.notes[-1]
    questions = checklist(result.fields["subcategory"].value)
    assert note.open_questions == questions[:2]  # 99 is not on the checklist, so ignored


def test_every_subcategory_has_a_checklist():
    directory = load_directory()
    for route in directory.routes:
        assert len(checklist(route.subcategory)) > len(checklist(None)), route.subcategory


def test_judge_scores_notes_on_redacted_text():
    from copilot.evaluation.judge import NoteJudge, judge_metrics
    from tests.test_extraction import reply

    lt = load_split("dev")[0]
    result = run(FakeModel(), lt)
    seen = []

    def fake_judge(**kwargs):
        seen.append(kwargs["messages"][1]["content"])
        return reply({"covered_facts": [1, 1, 50], "unsupported_items": [2]})

    judge = NoteJudge(ModelClient(settings(), completion=fake_judge))
    metrics = judge_metrics([(lt, result)], judge)
    items = len([i for i in [result.notes[-1].reported, *result.notes[-1].asks] if i])
    assert metrics["notes.judge_faithfulness"] == (items - 1) / items
    assert 0 < metrics["notes.judge_completeness"] < 1
    assert not any(p.value.lower() in seen[0].lower() for p in lt.labels.pii)
