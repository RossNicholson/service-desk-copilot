import pytest
import yaml

from datagen.world import DATAGEN, load_world


@pytest.fixture(scope="session")
def world():
    return load_world()


@pytest.fixture(scope="session")
def mix():
    return yaml.safe_load((DATAGEN / "scenarios" / "mix.yaml").read_text())
