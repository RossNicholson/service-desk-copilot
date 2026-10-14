"""Loads the fictional world: Northgate, its clients and the issue catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from datagen.schema import Client, Issue, Msp

DATAGEN = Path(__file__).resolve().parent


@dataclass(frozen=True)
class World:
    msp: Msp
    clients: tuple[Client, ...]
    issues: tuple[Issue, ...]

    def client(self, client_id: str) -> Client:
        return next(c for c in self.clients if c.id == client_id)

    def issue(self, issue_id: str) -> Issue:
        return next(i for i in self.issues if i.id == issue_id)

    def group_name(self, key: str) -> str:
        return next(g.name for g in self.msp.groups if g.key == key)


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache
def load_world(root: Path = DATAGEN) -> World:
    msp = Msp.model_validate(_load(root / "world" / "northgate.yaml"))
    clients = tuple(
        Client.model_validate(_load(p)) for p in sorted((root / "clients").glob("*.yaml"))
    )
    issues = tuple(Issue.model_validate(i) for i in _load(root / "world" / "issues.yaml"))
    return World(msp=msp, clients=clients, issues=issues)
