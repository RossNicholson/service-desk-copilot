"""Checks that generated data is safe to publish and that labels match the text.

Safety rules (the repository is public):
- every email address and domain name uses the reserved .example TLD;
- every UK phone number is in an Ofcom drama range;
- card numbers are documented test numbers and NI numbers use the QQ prefix;
- no em dashes (house style).

Consistency rules: priorities follow the matrix, every message has a label, and
the distinctive facts a label says were stated appear in the emails.

Run against the committed data with `uv run python -m datagen.validate`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from copilot.evaluation.dataset import load_split
from copilot.priority import priority
from datagen import pools
from datagen.schema import Thread, ThreadLabels

ROOT = Path(__file__).resolve().parent.parent
REAL_TLDS = {
    "com",
    "net",
    "org",
    "uk",
    "io",
    "ai",
    "co",
    "gov",
    "edu",
    "info",
    "biz",
    "me",
    "app",
    "dev",
    "cloud",
    "eu",
    "de",
    "fr",
    "us",
    "ie",
    "online",
    "email",
}
DOMAIN_RE = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+([a-z]{2,})\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+'-]+@([\w-]+(?:\.[\w-]+)+)")
PHONE_RE = re.compile(r"(?<!\d )(?<![\d-])(?:\+44\s?|0)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}(?![\d-])")
CARD_RE = re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{4}(?!\d)|(?<!\d)\d{16}(?!\d)")
NI_RE = re.compile(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")
EM_DASH = "\N{EM DASH}"

# Ofcom ranges reserved for TV and radio drama, as digit prefixes (national format).
DRAMA_PREFIXES = (
    "07700900",
    "02079460",
    "01134960",
    "01614960",
    "01174960",
    "01314960",
    "01214960",
    "01632960",
    "02890180",
    "02920180",
)
TEST_CARDS = {re.sub(r"\D", "", c) for c in pools.TEST_CARD_NUMBERS}


def _digits(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return "0" + digits[2:] if digits.startswith("44") else digits


def safety_problems(text: str) -> list[str]:
    problems = []
    for domain in EMAIL_RE.findall(text):
        if not domain.lower().endswith(".example"):
            problems.append(f"email address on a real-looking domain: {domain}")
    for match in DOMAIN_RE.finditer(text):
        host = match.group(0).lower()
        if match.group(1).lower() in REAL_TLDS and not host.endswith(".example"):
            problems.append(f"real-looking domain: {host}")
    for number in PHONE_RE.findall(text):
        if not _digits(number).startswith(DRAMA_PREFIXES):
            problems.append(f"phone number outside the drama ranges: {number}")
    for card in CARD_RE.findall(text):
        if re.sub(r"\D", "", card) not in TEST_CARDS:
            problems.append(f"card number that is not a documented test number: {card}")
    for ni in NI_RE.findall(text):
        if not ni.startswith("QQ"):
            problems.append(f"NI number without the QQ example prefix: {ni}")
    if EM_DASH in text:
        problems.append("em dash found")
    return problems


DISTINCTIVE = re.compile(r'"[^"]+"|\b[A-Z]{2,4}-(?:LT-)?\d{3,6}\b|\b\d{2,}\S*')


def consistency_problems(thread: Thread, labels: ThreadLabels) -> list[str]:
    problems = []
    f = labels.fields
    if priority(f.impact, f.urgency) != f.priority:
        problems.append(
            f"priority {f.priority} does not match impact {f.impact}, urgency {f.urgency}"
        )
    if len(labels.messages) != len(thread.messages):
        problems.append("message labels do not match the number of emails")
    text = "\n".join(e.body for e in thread.messages)
    if f.caller not in text and f.caller not in {e.from_address for e in thread.messages}:
        problems.append(f"caller {f.caller} does not appear in the thread")
    for detail in labels.note.details:
        for token in DISTINCTIVE.findall(detail):
            if token not in text:
                problems.append(f"stated detail {token!r} not found in the emails")
    if labels.traits.chasing and not any(m.is_chase for m in labels.messages):
        problems.append("chasing thread has no chase message")
    if labels.traits.adversarial and not any(m.injection for m in labels.messages):
        problems.append("adversarial thread has no injection")
    return problems


def validate_pair(thread: Thread, labels: ThreadLabels) -> list[str]:
    blob = json.dumps(thread.model_dump(), ensure_ascii=False)
    # JSON escapes newlines; check the raw strings too so line-based patterns work.
    raw = "\n".join(
        f"{e.from_name} {e.from_address} {' '.join(e.to + e.cc)} {e.subject}\n{e.body}"
        for e in thread.messages
    )
    label_text = json.dumps(labels.model_dump(mode="json"), ensure_ascii=False)
    problems = sorted(set(safety_problems(raw) + safety_problems(label_text)))
    if EM_DASH in blob:
        problems.append("em dash found")
    return problems + consistency_problems(thread, labels)


def read_split(split_dir: Path) -> list[tuple[Thread, ThreadLabels]]:
    pairs = load_split(split_dir.name, split_dir.parent)
    return [(p.thread, p.labels) for p in pairs]


def main(data_dir: Path = ROOT / "data") -> int:
    failures = 0
    for split in ("dev", "test"):
        split_dir = data_dir / split
        if not (split_dir / "threads.jsonl").exists():
            print(f"{split}: no data")
            continue
        pairs = read_split(split_dir)
        for thread, labels in pairs:
            for problem in validate_pair(thread, labels):
                failures += 1
                print(f"{thread.thread_id}: {problem}")
        print(f"{split}: checked {len(pairs)} threads")
    print("OK" if not failures else f"{failures} problems")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
