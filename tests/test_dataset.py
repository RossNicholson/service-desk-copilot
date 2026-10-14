"""Checks over the committed dataset in data/."""

import json

import pytest

from datagen.plan import TRAIT_NAMES
from datagen.validate import ROOT, read_split, validate_pair

DATA = ROOT / "data"
MANIFEST = json.loads((DATA / "MANIFEST.json").read_text())


@pytest.fixture(scope="module", params=["dev", "test"])
def split(request):
    return request.param, read_split(DATA / request.param)


def test_counts_match_manifest(split):
    name, pairs = split
    assert len(pairs) == MANIFEST["splits"][name]["count"]


def test_every_thread_passes_validation(split):
    _, pairs = split
    failures = {t.thread_id: p for t, lab in pairs if (p := validate_pair(t, lab))}
    assert not failures


def test_mix_matches_configuration(split, mix):
    _, pairs = split
    n = len(pairs)
    for trait in TRAIT_NAMES:
        count = sum(getattr(lab.traits, trait) for _, lab in pairs)
        assert count == round(mix["traits"][trait] * n), trait


def test_labels_belong_to_their_split(split):
    name, pairs = split
    assert {lab.split for _, lab in pairs} == {name}


def test_dev_and_test_do_not_overlap():
    dev = read_split(DATA / "dev")
    test = read_split(DATA / "test")
    assert not {t.thread_id for t, _ in dev} & {t.thread_id for t, _ in test}
    dev_bodies = {t.messages[0].body for t, _ in dev}
    assert not dev_bodies & {t.messages[0].body for t, _ in test}


@pytest.mark.skipif(
    any(s["renderer"] != "template" for s in MANIFEST["splits"].values()),
    reason="committed data was rendered by a model, which is not reproducible byte for byte",
)
def test_dataset_is_reproducible(tmp_path):
    """Regenerating with the template renderer gives the committed files exactly."""
    from datagen.generate import main

    assert main(["--out", str(tmp_path)]) == 0
    for name in ("dev", "test"):
        for file in ("threads.jsonl", "labels.jsonl"):
            assert (tmp_path / name / file).read_bytes() == (DATA / name / file).read_bytes()
