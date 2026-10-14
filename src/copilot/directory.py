"""What the MSP already knows: its clients, their staff and services, its teams.

In a real deployment this comes from the PSA or CMDB. Here it is read from the
fictional world files in `datagen/`, ignoring everything that only matters for
generating data (personas, typo rates and so on).

The taxonomy (category, subcategory, the service it usually affects and the team
that handles it) is the MSP's routing table. It holds the valid values for those
fields, not the answer for any particular ticket.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "datagen"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Contact(_Model):
    first_name: str
    last_name: str
    email: str
    role: str

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class ClientRecord(_Model):
    id: str
    name: str
    domain: str
    sites: list[str]
    services: dict[str, str]
    people: list[Contact]


class Team(_Model):
    key: str
    name: str
    lead: str


class EngineerRecord(_Model):
    id: str
    name: str
    email: str
    group: str


class Route(_Model):
    category: str
    subcategory: str
    service: str
    group: str


class Directory(_Model):
    msp_name: str
    support_address: str
    response_targets_minutes: dict[str, int]
    teams: list[Team]
    engineers: list[EngineerRecord]
    clients: list[ClientRecord]
    routes: list[Route]

    def client_for_address(self, address: str) -> ClientRecord | None:
        domain = address.rsplit("@", 1)[-1].lower()
        return next((c for c in self.clients if c.domain == domain), None)

    def team_name(self, key: str) -> str:
        return next(t.name for t in self.teams if t.key == key)

    def route(self, subcategory: str) -> Route | None:
        return next((r for r in self.routes if r.subcategory == subcategory), None)

    def team_lead(self, team_name: str) -> EngineerRecord | None:
        team = next((t for t in self.teams if t.name == team_name), None)
        if team is None:
            return None
        return next((e for e in self.engineers if e.id == team.lead), None)


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache
def load_directory(root: Path = DEFAULT_ROOT) -> Directory:
    msp = _yaml(root / "world" / "northgate.yaml")
    routes: dict[tuple[str, str], Route] = {}
    for issue in _yaml(root / "world" / "issues.yaml"):
        route = Route.model_validate(issue)
        routes.setdefault((route.subcategory, route.service), route)
    return Directory(
        msp_name=msp["name"],
        support_address=msp["support_address"],
        response_targets_minutes=msp["response_targets_minutes"],
        teams=msp["groups"],
        engineers=msp["engineers"],
        clients=[_yaml(p) for p in sorted((root / "clients").glob("*.yaml"))],
        routes=list(routes.values()),
    )
