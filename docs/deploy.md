# Deploying the demo

The public demo runs the whole compose stack on one small VPS. Nothing listens on a public port: a Cloudflare Tunnel carries traffic to the copilot and Zammad, and Cloudflare Access asks visitors to sign in before they reach either. The one exception is the copilot's `/fixed/` page, which clients open from a suggestion email.

## What you need

- A VPS with Docker. Zammad is the heavy part: allow 2 vCPUs and about 8 GB of memory (Elasticsearch stays off unless you add `--profile search`, which needs more).
- A domain on Cloudflare.
- A Cloudflare Zero Trust organisation (the free plan is enough for Tunnels and Access).
- Optionally, a model API key and a Teams webhook.

## 1. Install and configure

```bash
git clone https://github.com/RossNicholson/service-desk-copilot.git
cd service-desk-copilot
cp .env.example .env
```

In `.env`, set:

- every password (`POSTGRES_PASSWORD`, `COPILOT_DB_PASSWORD`, `ZAMMAD_DB_PASSWORD`, `ZAMMAD_ADMIN_PASSWORD`, `MAILPIT_POP3_PASSWORD`), each a long random string;
- `PUBLIC_URL` to the copilot's public address, for example `https://copilot.example.com`;
- `SPEND_CAP_USD` and `RATE_LIMIT_PER_MINUTE` to what you are happy to spend on a public demo;
- `LLM_MODEL` and `LLM_API_KEY` if the demo should use a model.

Then start the stack and set Zammad up:

```bash
docker compose up -d --wait
uv run python scripts/bootstrap_zammad.py
```

Put the printed token in `ZAMMAD_TOKEN`, set `COPILOT_SOURCE=zammad`, and run `docker compose up -d` again. Load some synthetic tickets with `uv run python scripts/seed_mailpit.py --limit 20`.

## 2. The tunnel

1. In Cloudflare Zero Trust, go to Networks, Tunnels, and create a tunnel of type Cloudflared. Copy its token into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.
2. Add two public hostnames to the tunnel:
   - `copilot.example.com` to `http://copilot:8000`
   - `zammad.example.com` to `http://zammad-nginx:8080`
3. Start it: `docker compose --profile tunnel up -d --wait`.

The services' own ports stay bound to `127.0.0.1`, so the tunnel is the only way in.

## 3. Cloudflare Access

In Zero Trust, Access, Applications, add self-hosted applications:

1. **Copilot**, domain `copilot.example.com`, with an Allow policy for the people who may use it (for example emails ending in your domain, or a list of reviewers).
2. **Zammad**, domain `zammad.example.com`, with the same policy.
3. **Suggestion confirm page**, domain `copilot.example.com`, path `fixed`, with a Bypass policy for everyone. Clients reach this page from their email and cannot sign in. It shows a confirm button for one ticket and nothing else, and each link is signed and works once.

Behind Access, the copilot names engineers in the audit log from the `Cf-Access-Authenticated-User-Email` header that Access adds.

## 4. Optional extras

- **Teams alerts**: set `TEAMS_WEBHOOK_URL` to a "Post to a channel when a webhook request is received" workflow on the channel.
- **AI suggestion emails**: see the README. On the demo, mail goes to Mailpit unless you point `SMTP_HOST` at a real relay. Sending through Cloudflare's own email service is possible if the account has Workers Paid; it stays behind the same `SUGGESTION_EMAIL_TRANSPORT` setting.

## Updating

```bash
git pull
docker compose --profile tunnel up -d --build --wait
```

Postgres, Zammad and the audit log live in Docker volumes, so they survive updates. Back them up with `docker compose exec postgres pg_dumpall -U postgres > backup.sql`.
