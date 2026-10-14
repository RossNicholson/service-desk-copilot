# Service Desk Copilot

An open-source AI assistant for MSP service desks that takes ticket admin off engineers at every tier. It fills in ticket fields from client emails, writes structured notes, keeps tickets up to date and spots clients who are chasing before anyone has replied.

Everything runs on synthetic data for a fictional MSP, **Northgate Managed Services**. The repository contains no real company, client or employee data.

> Status: early development (Phase 1 of 7). A full README arrives in Phase 7.

## Quick start

```bash
cp .env.example .env          # then edit the passwords
docker compose up -d --wait   # Postgres, Zammad and Mailpit
uv sync                       # Python 3.12 dependencies
uv run python scripts/bootstrap_zammad.py   # admin user, email channel, API token
uv run pytest                 # unit and dataset tests
uv run pytest -m compose      # smoke tests against the running stack
```

Mail sent to `support@northgate-ms.example` through Mailpit's SMTP port (1025) is collected by Zammad over POP3 and becomes a ticket. Zammad's automatic replies to customers are switched off.

- Zammad: http://localhost:8080
- Mailpit: http://localhost:8025

## Synthetic data

See [`datagen/README.md`](datagen/README.md) for how the email threads in `data/` are generated, and how to regenerate them with your own AI provider.

## Licence

MIT. See [LICENSE](LICENSE).
