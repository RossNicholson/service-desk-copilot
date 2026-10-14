"""Replay development threads into Mailpit, so Zammad turns them into tickets.

Sends each client email over SMTP with its original headers (Date, Message-ID,
In-Reply-To, References), so follow-ups thread onto the right Zammad ticket.
Northgate's own replies are skipped by default: Zammad ignores mail from its own
address.

Mailpit's POP3 mailbox hands Zammad the newest mail first, so a follow-up could
arrive before the email it replies to and open a ticket of its own. Emails are
therefore sent in rounds (every thread's first email, then every second email
and so on), waiting for Zammad to empty the mailbox between rounds. Use
--no-wait to send everything at once when only Mailpit matters.

Only the development set can be seeded; the test set is for scoring only.

    uv run python scripts/seed_mailpit.py --limit 20
    uv run python scripts/seed_mailpit.py --thread DEV-0007 --include-outbound
"""

from __future__ import annotations

import argparse
import json
import smtplib
import sys
import time
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datagen.schema import Email, Thread  # noqa: E402
from scripts.bootstrap_zammad import SUPPORT_ADDRESS, load_env  # noqa: E402


def to_mime(email: Email, references: list[str]) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((email.from_name, email.from_address))
    message["To"] = ", ".join(email.to)
    if email.cc:
        message["Cc"] = ", ".join(email.cc)
    message["Date"] = email.date
    message["Subject"] = email.subject
    message["Message-ID"] = email.message_id
    if email.in_reply_to:
        message["In-Reply-To"] = email.in_reply_to
        message["References"] = " ".join(references)
    message["X-Synthetic-Data"] = "Service Desk Copilot test data, fictional"
    message.set_content(email.body)
    return message


def wait_for_empty_mailbox(api: str, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with urllib.request.urlopen(f"{api}/api/v1/messages?limit=1", timeout=10) as resp:
            if json.loads(resp.read())["total"] == 0:
                return
        time.sleep(3)
    raise SystemExit("Zammad did not collect the mail in time. Is the email channel set up?")


def load_threads(path: Path) -> list[Thread]:
    return [Thread.model_validate_json(line) for line in path.read_text().splitlines()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="Only the first N threads.")
    parser.add_argument("--thread", action="append", help="Only this thread id (repeatable).")
    parser.add_argument(
        "--include-outbound",
        action="store_true",
        help="Also send Northgate's replies (Zammad will ignore them).",
    )
    parser.add_argument("--delay", type=float, default=0.05, help="Seconds between emails.")
    parser.add_argument(
        "--no-wait", action="store_true", help="Send everything at once without waiting for Zammad."
    )
    args = parser.parse_args(argv)

    env = load_env(ROOT / ".env")
    host = env.get("SMTP_HOST", "localhost")
    port = int(env.get("SMTP_PORT", "1025"))

    threads = load_threads(ROOT / "data" / "dev" / "threads.jsonl")
    if args.thread:
        threads = [t for t in threads if t.thread_id in set(args.thread)]
    if args.limit:
        threads = threads[: args.limit]

    api = env.get("MAILPIT_API_URL", "http://localhost:8025")
    sent = 0
    rounds = max((len(t.messages) for t in threads), default=0)
    for position in range(rounds):
        with smtplib.SMTP(host, port) as smtp:
            for thread in threads:
                if position >= len(thread.messages):
                    continue
                email = thread.messages[position]
                if email.from_address == SUPPORT_ADDRESS and not args.include_outbound:
                    continue
                references = [m.message_id for m in thread.messages[:position]]
                smtp.send_message(to_mime(email, references), to_addrs=email.to + email.cc)
                sent += 1
                time.sleep(args.delay)
        if not args.no_wait and position < rounds - 1:
            print(f"Round {position + 1} of {rounds} sent, waiting for Zammad to collect it...")
            wait_for_empty_mailbox(api)
    print(f"Sent {sent} emails from {len(threads)} threads to {host}:{port}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
