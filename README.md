# Service Desk Copilot

An open-source AI assistant for MSP service desks that takes ticket admin off engineers at every tier. It fills in ticket fields from client emails, writes structured notes, keeps tickets up to date and spots clients who are chasing before anyone has replied.

Everything runs on synthetic data for a fictional MSP, **Northgate Managed Services**. The repository contains no real company, client or employee data.

> Status: early development (Phase 6 of 8). A full README arrives in Phase 8.

## Quick start

```bash
cp .env.example .env          # then edit the passwords
docker compose up -d --wait   # Postgres, Zammad and Mailpit
uv sync                       # Python 3.12 dependencies
uv run python scripts/bootstrap_zammad.py   # admin user, email channel, copilot fields, API token
uv run pytest                 # unit and dataset tests
uv run pytest -m compose      # smoke tests against the running stack
```

To load the synthetic client emails into Zammad as tickets:

```bash
uv run python scripts/seed_mailpit.py --limit 20   # or no --limit for all 200 dev threads
```

Mail sent to `support@northgate-ms.example` through Mailpit's SMTP port (1025) is collected by Zammad over POP3 and becomes a ticket. Zammad's automatic replies to customers are switched off.

- Zammad: http://localhost:8080
- Mailpit: http://localhost:8025

## The copilot's screens

```bash
uv run uvicorn copilot.web.app:app --reload   # then open http://localhost:8000
```

A dashboard, the ticket list, a ticket view where every AI field shows its confidence and reason and can be changed or undone, alerts, harness scores and AI settings. `uv run python scripts/export_ui_preview.py` writes the same screens as one HTML file that opens without a server.

By default (`COPILOT_SOURCE=demo`) the screens show the synthetic dev threads with the rules baseline's values, and changes last until a restart. To run the copilot on Zammad, put the token from the bootstrap script in `ZAMMAD_TOKEN` and set `COPILOT_SOURCE=zammad` in `.env`. The app then:

- checks open tickets every `CHECKIN_INTERVAL_MINUTES` and fills in the copilot's fields (shown in Zammad as "Copilot: ..." fields) and an internal note;
- uses your model if `LLM_API_KEY` is set, and the rules baseline otherwise;
- sends chase and silence alerts to Teams if `TEAMS_WEBHOOK_URL` is set (create a "Post to a channel when a webhook request is received" workflow on the channel), and only lists them on the Alerts screen otherwise;
- saves engineers' changes to Zammad, and the copilot leaves a changed field alone until it is undone;
- writes every field change, note, alert, override and model run (with prompt version, model and cost) to the `audit_log` table in Postgres.

## Evaluation

```bash
uv run python -m copilot.evaluation
```

Scores every system on field accuracy, note faithfulness and completeness, check-ins, chase detection, alert precision and recall, PII leakage and cost per ticket, against a rule-based baseline, and checks the PII redactor on its own. See [`docs/evaluation.md`](docs/evaluation.md).

The copilot itself needs a model of your choice. Add `LLM_MODEL` and `LLM_API_KEY` to `.env` (any provider LiteLLM supports, or `LLM_API_BASE` for a local Ollama server) and the same command scores it next to the baseline. Without a key, only the baseline and the redaction check run.

## Privacy

Personal details are replaced with placeholders such as `[PERSON_1]` before any text reaches a model, using Microsoft Presidio, UK-specific patterns and the MSP's own contact list. Email text is passed to the model as fenced, untrusted data. See [`docs/redaction.md`](docs/redaction.md).

## Synthetic data

See [`datagen/README.md`](datagen/README.md) for how the email threads in `data/` are generated, and how to regenerate them with your own AI provider.

## Continuous integration

GitHub Actions runs linting, the data validator and the tests on every push and pull request, plus a job that brings up the compose stack and checks that an email becomes a Zammad ticket.

## Licence

MIT. See [LICENSE](LICENSE).
