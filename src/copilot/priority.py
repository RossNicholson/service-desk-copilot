"""Priority from impact and urgency, using the familiar ITIL matrix.

Impact: 1 a whole site or firm, 2 a team, 3 one person.
Urgency: 1 work has stopped or a deadline is imminent, 2 degraded, 3 a workaround exists.
"""

PRIORITY_MATRIX = {
    (1, 1): "P1", (1, 2): "P2", (1, 3): "P3",
    (2, 1): "P2", (2, 2): "P3", (2, 3): "P4",
    (3, 1): "P3", (3, 2): "P4", (3, 3): "P5",
}  # fmt: skip


def priority(impact: int, urgency: int) -> str:
    return PRIORITY_MATRIX[(impact, urgency)]
