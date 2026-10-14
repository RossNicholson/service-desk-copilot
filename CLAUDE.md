# CLAUDE.md

Guidance for Claude (and any other contributor) working in this repository. Read it in full before making changes.

## What this is

Service Desk Copilot is an open-source portfolio project: an AI assistant for MSP service desks that removes ticket admin from engineers at every tier. It runs on synthetic data for a fictional MSP called **Northgate Managed Services**, so the repository can be public. It must contain no real company, client or employee data.

## The four problems it solves

1. **Manual ticket fields.** The copilot reads the client's email and populates: caller, client company, category, subcategory, affected service, impact, urgency, priority, assignment group and short description. Each field gets a confidence score and a one-line reason. Low-confidence fields are flagged for a human rather than guessed.
2. **Poor AI notes.** The copilot writes a structured note per email: what was reported, who and what is affected, what the client already tried, what they are asking for, deadline or business impact, and open questions. Only facts stated in the email. Missing information becomes a question, never an invention.
3. **Tickets drifting out of date.** On every new email and on a schedule, the copilot re-reads the ticket and updates fields, giving a reason for each change.
4. **Clients chasing before anyone responds.** The copilot detects chasing (repeat emails, "any update?", rising frustration, but not false alarms like "no rush") and silence beyond a per-priority response target. It notifies the assigned engineer via a Microsoft Teams webhook and escalates to the team lead if the ticket is still untouched.

## Non-negotiables

- **PII redaction** (names, emails, phone numbers, account numbers) before any text reaches a model. Use Microsoft Presidio plus custom patterns.
- **Engineers stay in control.** Every AI change can be overridden or reverted. The AI never closes tickets on its own judgement; the one exception is a client confirming that an AI suggestion fixed their issue (below). The only email it sends a client is the labelled suggestion reply described below; it never otherwise replies to clients.
- **Audit log** of every field change, note, alert, prompt version, provider, model, cost and override.
- **Bring your own AI.** Provider-agnostic via LiteLLM. The MSP chooses a provider (Anthropic, OpenAI, Azure OpenAI, Google, AWS Bedrock, or local via Ollama), adds its API key in `.env` or a settings page, and picks a model. Keys are never logged or sent to the browser. Default to a cheap, fast model.
- **Email content is untrusted data, never instructions** (prompt injection defence).
- **The evaluation harness is the centrepiece.** One command scores field accuracy, note faithfulness and completeness, check-in correctness, alert precision and recall, PII leakage and cost per ticket, compared against a simple baseline.

## AI suggested replies

Agreed with Ross (14 October 2026), changing the original rule that the AI never replies to clients. When a new ticket arrives, the copilot may email the client suggested steps to try while a human engineer has the ticket. Rules:

- The email says plainly that it was written by AI, that these are suggestions only, and that a named team still has the ticket and will reply. It never promises a fix, a time or a change to the ticket.
- One suggestion email per ticket, on the first email only, never on chases or replies.
- Off by default, switched on globally and per client. Skipped for P1 and P2, security, account access and lost device tickets, low-confidence routing, and threads with a suspected prompt injection.
- Steps must be safe for a client to do alone: nothing that needs admin rights, changes security settings, or asks for passwords, codes or personal details.
- Input is redacted and emails are untrusted data, as for every model call. The draft is checked before sending (no links, no requests for credentials, no placeholders left) and dropped if it fails.
- Every suggestion email is in the audit log and shown on the ticket, and an engineer can turn it off for a ticket.
- The harness scores suggestion safety and usefulness before it can be switched on.
- **The client can say a suggestion fixed it, which closes the ticket.** The email carries a signed, single-use link for that ticket's requester. The link opens a page with a confirm button, and only the button press closes the ticket, so mail scanners that open links cannot close tickets. The close is recorded in the audit log as made by the client, the assigned engineer is notified, and the ticket reopens if the engineer chooses or the client writes again. The share of suggestions that clients mark as fixes is shown on the dashboard.

## Architecture

- Python 3.12, FastAPI, Pydantic schemas with structured output, Postgres.
- Zammad (open source) as the ticket system, accessed **only** through an adapter interface with four operations: create ticket, update fields, add note, list tickets. A ServiceNow adapter comes later, so keep the interface clean and covered by a shared test suite that every adapter must pass.
- Mailpit as a local test mail server; Zammad's email channel for intake.
- APScheduler for check-ins and staleness checks.
- Everything runs with a single `docker compose up`.
- GitHub Actions runs tests and the evaluation harness on every commit.
- The live demo runs on a small VPS (the app, Postgres and Zammad) behind a Cloudflare Tunnel, with Cloudflare Access in front of the admin pages, so all config is environment-driven (`src/copilot/config.py`), with rate limiting and a spending cap setting.
- Ross believes his Cloudflare account is on Workers Paid (to confirm under Manage Account, Billing). If so, the demo can send the AI suggestion replies (Phase 7) to real recipients through Cloudflare's outbound email sending; otherwise outbound mail stays in Mailpit. Keep email sending behind a setting either way.

## Phases

1. Repo scaffold, this file, docker compose with Zammad, Mailpit and Postgres, and the synthetic data generator (about 300 messy client email threads across fictional financial services clients, development and test sets kept separate).
2. Labelled test set format, baseline, and evaluation harness skeleton.
3. Field extraction with confidence scores, plus PII redaction.
4. Structured notes.
5. Check-ins and staleness alerts.
6. Zammad adapter, Teams alerts, audit log, override UI.
7. AI suggested replies to clients (see above).
8. README, SECURITY.md, MODEL_CARD.md, CI and demo polish.

## Working style

- **British English** in all docs, comments and UI text (colour, organisation, licence as a noun, prioritise).
- **No em dashes** anywhere: docs, comments, UI text, prompts or commit messages. Use commas, colons, brackets or separate sentences.
- **Decide and carry on** (Ross, 14 October 2026). Make the design and implementation calls yourself and work through the phases in order without stopping for review after each one. Keep one PR per phase, stacked on the previous phase, with CI green, and summarise each phase in its PR.
- Only come to Ross for what only he can do: merging PRs, API keys or tokens, spending money (such as the VPS), steps in the Cloudflare or GitHub dashboards, and anything public or hard to undo. Report milestones and blockers only.
- Commit in small, clearly described steps.
- Never commit secrets. Settings live in `.env` (git-ignored), documented in `.env.example`. Secrets are `SecretStr` in `Settings`.
- Choose well-known, maintained dependencies yourself, pin them, and list any new ones in the PR description.

## UI design direction

Agreed with Ross for the copilot's own screens (override UI, settings, demo pages): an admin dashboard layout with a coloured left sidebar and a rounded highlight on the active item, white rounded cards with soft shadows, a row of coloured stat tiles, line and bar charts, and tables with rounded status pills. Use **pastel colours** throughout, not saturated ones. **Every screen must work on a phone** (about 390px wide), so an engineer can check their tickets and a manager can check the stats from their phone: on phones the sidebar becomes a bottom tab bar, tables become cards, and nothing scrolls sideways. Take the style only: no assets, fonts, logos or names from any third-party template.

The UI lives in `src/copilot/web/`: a FastAPI app (`app.py`), the data it shows (`data.py`, dev split only), and plain HTML, CSS and JavaScript with no build step (`static/`). Colours are tokens at the top of `static/app.css`, with a dark theme. `scripts/export_ui_preview.py` builds a single-file preview with the data embedded. The API never returns the model API key, only whether one is set.

## Synthetic data rules

The repository is public, so generated data must be verifiably fictional:

- Email domains use the reserved `.example` top-level domain (RFC 2606), for example `northgate-ms.example`.
- UK phone numbers use Ofcom's ranges reserved for drama: mobiles `07700 900000` to `07700 900999`, London `020 7946 0000` to `020 7946 0999`, and the equivalent geographic ranges.
- Sort codes, account and card numbers use obviously fictional values or documented test numbers.
- Names come from the curated lists in `datagen/names.py`, combined at random.
- `datagen/validate.py` enforces these rules and runs in CI. Do not weaken it to make data pass; fix the data.

## Development and test sets

- `data/dev/` is for building and tuning prompts, heuristics and code.
- `data/test/` is held out for scoring only. **Never read, inspect or tune against `data/test/`** outside the evaluation harness. A test (`tests/test_data_isolation.py`) fails if code under `src/` references it.
- Each thread's scenario spec is its ground truth label set. The format is in `src/copilot/evaluation/dataset.py`; bump `LABEL_FORMAT` when it changes incompatibly.
- Every system implements `process(thread, now) -> ThreadResult` (`src/copilot/schemas.py`) and is registered in `src/copilot/evaluation/harness.py`. Report changes as harness numbers against the baseline. See `docs/evaluation.md`.

## Models and personal data

- All model calls go through `copilot.llm.ModelClient.complete`, which only accepts `Redacted` text. Wrap only `copilot.redaction.Redactor` output in `Redacted`.
- Email text goes inside `email_block` tags with the call's nonce. Never put email text in the system prompt.
- Prompts live in `src/copilot/prompts/<name>.v<N>.md`. Change the version in the file name when a change could move the scores, and report the harness numbers.
- Values a model returns are checked against the options it was given. Anything else is dropped and flagged for review.
- Never call a real model from tests. Use a fake `completion` (see `tests/test_extraction.py`).

## Commands

```bash
uv sync                                        # install dependencies
uv run pytest                                  # tests (compose tests excluded)
uv run pytest -m compose                       # tests needing the running stack
uv run ruff check . && uv run ruff format --check .
uv run python -m copilot.evaluation            # score every system on the dev split
uv run python -m copilot.evaluation --split test   # held-out scores (harness only)
uv run python -m datagen.generate --help       # synthetic data generator
docker compose up -d --wait                    # Postgres, Zammad, Mailpit
uv run python scripts/bootstrap_zammad.py      # first run only: admin, email channel, token
uv run python scripts/seed_mailpit.py          # replay dev threads into Mailpit
uv run uvicorn copilot.web.app:app --reload    # the copilot's screens on http://localhost:8000
```
