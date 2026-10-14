from datetime import timedelta

from datagen.plan import PRIORITY_MATRIX, TRAIT_NAMES, allocate_traits, plan_split, priority


def test_same_seed_gives_same_plans(world, mix):
    a = plan_split(world, mix, "dev", 20, seed=1)
    b = plan_split(world, mix, "dev", 20, seed=1)
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]


def test_different_seed_gives_different_plans(world, mix):
    a = plan_split(world, mix, "dev", 20, seed=1)
    b = plan_split(world, mix, "dev", 20, seed=2)
    assert [p.issue_id for p in a] != [p.issue_id for p in b]


def test_traits_are_allocated_exactly(mix):
    import random

    traits = allocate_traits(200, mix["traits"], random.Random(0))
    for name in TRAIT_NAMES:
        assert sum(getattr(t, name) for t in traits) == round(mix["traits"][name] * 200)


def test_priority_matrix():
    assert priority(1, 1) == "P1"
    assert priority(3, 3) == "P5"
    assert priority(2, 2) == "P3"
    assert set(PRIORITY_MATRIX.values()) == {"P1", "P2", "P3", "P4", "P5"}


def test_clients_are_spread_evenly(world, mix):
    plans = plan_split(world, mix, "dev", 50, seed=3)
    counts = {c.id: sum(p.client_id == c.id for p in plans) for c in world.clients}
    assert set(counts.values()) == {10}


def test_silent_threads_have_no_response_and_breach(world, mix):
    for plan in plan_split(world, mix, "dev", 100, seed=4):
        outbound = [m for m in plan.messages if m.direction == "outbound"]
        if plan.traits.silence:
            assert not outbound
        else:
            assert outbound and outbound[0].index == 1
            target = world.msp.response_targets_minutes[
                priority({"site": 1, "team": 2, "single": 3}[plan.scope], plan.urgency)
            ]
            assert outbound[0].sent_at - plan.messages[0].sent_at <= timedelta(minutes=target)


def test_messages_are_in_time_order(world, mix):
    for plan in plan_split(world, mix, "dev", 100, seed=5):
        times = [m.sent_at for m in plan.messages]
        assert times == sorted(times)
        assert plan.observed_at > times[-1]


def test_stated_and_missing_details_do_not_overlap(world, mix):
    for plan in plan_split(world, mix, "dev", 100, seed=6):
        stated = {f.key for f in plan.details_stated}
        missing = {f.key for f in plan.details_missing}
        assert not stated & missing
