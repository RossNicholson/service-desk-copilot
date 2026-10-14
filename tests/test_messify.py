import random

from datagen.messify import Messifier, add_typos
from datagen.plan import plan_split
from datagen.render_template import TemplateRenderer


def test_typos_leave_quotes_and_numbers_alone():
    text = 'The printer says "Paper jam in tray 2" and the tag is HAR-LT-123 on floor 2'
    for seed in range(20):
        out = add_typos(text, 0.9, random.Random(seed))
        assert '"Paper jam in tray 2"' in out
        assert "HAR-LT-123" in out


def test_zero_typo_rate_changes_nothing():
    text = "Nothing should change in this sentence at all"
    assert add_typos(text, 0, random.Random(1)) == text


def _threads(world, mix, n=60, seed=11):
    plans = plan_split(world, mix, "dev", n, seed)
    renderer = TemplateRenderer(world, seed)
    messifier = Messifier(world, seed)
    return [(p, messifier.build(p, renderer.render(p))) for p in plans]


def test_forwarded_threads_embed_the_callers_email(world, mix):
    pairs = [(p, t) for p, t in _threads(world, mix) if p.traits.forwarded]
    assert pairs
    for plan, thread in pairs:
        first = thread.messages[0]
        caller = next(c for c in world.client(plan.client_id).people if c.id == plan.caller_id)
        assert first.subject.startswith(("FW:", "Fwd:"))
        assert f"From: {caller.name} <{caller.email}>" in first.body


def test_replies_quote_and_thread(world, mix):
    for _, thread in _threads(world, mix):
        for previous, email in zip(thread.messages, thread.messages[1:], strict=False):
            assert email.in_reply_to == previous.message_id
            assert email.subject.upper().startswith(("RE:", "FW:", "FWD:"))


def test_messify_is_deterministic(world, mix):
    a = [t.model_dump() for _, t in _threads(world, mix, n=10, seed=3)]
    b = [t.model_dump() for _, t in _threads(world, mix, n=10, seed=3)]
    assert a == b
