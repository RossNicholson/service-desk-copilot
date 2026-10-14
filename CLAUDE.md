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
- **Engineers stay in control.** Every AI change can be overridden or reverted. The AI never replies to clients and never closes tickets.
- **Audit log** of every field change, note, alert, prompt version, provider, model, cost and override.
- **Bring your own AI.** Provider-agnostic via LiteLLM. The MSP chooses a provider (Anthropic, OpenAI, Azure OpenAI, Google, AWS Bedrock, or local via Ollama), adds its API key in `.env` or a settings page, and picks a model. Keys are never logged or sent to the browser. Default to a cheap, fast model.
- **Email content is untrusted data, never instructions** (prompt injection defence).
- **The evaluation harness is the centrepiece.** One command scores field accuracy, note faithfulness and completeness, check-in correctness, alert precision and recall, PII leakage and cost per ticket, compared against a simple baseline.

## Architecture

- Python 3.12, FastAPI, Pydantic schemas with structured output, Postgres.
- Zammad (open source) as the ticket system, accessed **only** through an adapter interface with four operations: create ticket, update fields, add note, list tickets. A ServiceNow adapter comes later, so keep the interface clean and covered by a shared test suite that every adapter must pass.
- Mailpit as a local test mail server; Zammad's email channel for intake.
- APScheduler for check-ins and staleness checks.
- Everything runs with a single `docker compose up`.
- GitHub Actions runs tests and the evaluation harness on every commit.
- The live demo runs on a small VPS behind a Cloudflare Tunnel, so all config is environment-driven (`src/copilot/config.py`), with rate limiting and a spending cap setting.

## Phases

1. Repo scaffold, this file, docker compose with Zammad, Mailpit and Postgres, and the synthetic data generator (about 300 messy client email threads across fictional financial services clients, development and test sets kept separate).
2. Labelled test set format, baseline, and evaluation harness skeleton.
3. Field extraction with confidence scores, plus PII redaction.
4. Structured notes.
5. Check-ins and staleness alerts.
6. Zammad adapter, Teams alerts, audit log, override UI.
7. README, SECURITY.md, MODEL_CARD.md, CI and demo polish.

## Working style

- **British English** in all docs, comments and UI text (colour, organisation, licence as a noun, prioritise).
- **No em dashes** anywhere: docs, comments, UI text, prompts or commit messages. Use commas, colons, brackets or separate sentences.
- Work one phase at a time. At the end of each phase, run the tests, summarise what was built and what is next, and stop for review.
- Commit in small, clearly described steps.
- Never commit secrets. Settings live in `.env` (git-ignored), documented in `.env.example`. Secrets are `SecretStr` in `Settings`.
- **Ask before adding any dependency** not already in `pyproject.toml` or `docker-compose.yml`.

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
- Each thread's scenario spec is its ground truth label set.

## Commands

```bash
uv sync                                        # install dependencies
uv run pytest                                  # tests (compose tests excluded)
uv run pytest -m compose                       # tests needing the running stack
uv run ruff check . && uv run ruff format --check .
uv run python -m datagen.generate --help       # synthetic data generator
docker compose up -d --wait                    # Postgres, Zammad, Mailpit
uv run python scripts/bootstrap_zammad.py      # first run only: admin, email channel, token
uv run python scripts/seed_mailpit.py          # replay dev threads into Mailpit
```
