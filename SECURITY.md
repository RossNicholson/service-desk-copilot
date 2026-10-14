# Security

Service Desk Copilot reads client emails, sends text to a model provider of the MSP's choosing, writes to the ticket system, and can send one kind of email to clients. This page says how each of those is protected, and how to report a problem.

## Reporting a vulnerability

Please report security problems privately through GitHub's "Report a vulnerability" button on this repository (Security, Advisories), not in a public issue. Include what you found and how to reproduce it. If that button is not there, open an issue asking for a private contact, without the details.

This is a portfolio project that runs on synthetic data. There is no production service and no real client data to protect, but the design is meant to be safe to run for real, so reports are welcome.

## Personal data and the model

- **Redaction before every model call.** Names, email addresses, phone numbers, account and card numbers, sort codes, National Insurance numbers, dates of birth and client references are replaced with placeholders (`[PERSON_1]`, `[PHONE_2]`) before any text reaches a model. It uses Microsoft Presidio with spaCy, UK-specific patterns and the MSP's own contact list ([`docs/redaction.md`](docs/redaction.md)).
- **Enforced in code.** Every model call goes through `copilot.llm.ModelClient.complete`, which only accepts `Redacted` text, and only the redactor's output is wrapped in `Redacted`.
- **Measured.** The evaluation harness checks every piece of text sent to a model against the labelled personal details in the synthetic data, and reports the leakage rate. It is 0% on both the development and held-out test sets.
- **Restored only for engineers.** Placeholders in model output are swapped back for the real values only when an engineer sees them.

## Email is untrusted data

Client emails are treated as data, never instructions:

- Email text goes in fenced blocks tagged with a random value per call, which the email cannot close, and never in the system prompt. The system prompt tells the model the blocks are untrusted.
- Values a model returns are checked against the options it was given; anything else is dropped and flagged for an engineer.
- Notes keep only items whose quotes appear in the client's email.
- The copilot cannot close tickets, assign them, or email clients on a model's say-so. The only email it sends is the suggestion email below, and the only close is the one a client confirms.
- Threads that look like they are addressing the AI (`copilot.injection`) get no suggestion email. On the synthetic development set the check flags every injection thread and no others.

## Secrets

- Secrets (model API key, Zammad token, database password, Teams webhook URL, link signing secret, SMTP password) live in `.env`, which is git-ignored, and are held as `SecretStr` so they are masked in logs and errors.
- The API never returns a secret, only whether one is set. The Teams webhook URL is never echoed in errors.
- Keys are handed to LiteLLM only at the point of the call.
- CI never sees a real key: tests use a fake model.

## Engineers stay in control

- Every AI field change, note, alert, suggestion email, close, reopen and override is in an append-only audit log in Postgres, with the prompt version, provider, model and cost of each model run.
- An engineer's change locks the field: the copilot leaves it alone until the engineer undoes it.
- A spending cap and a rate limit stop model calls once reached.

## Suggestion emails and the confirm link

- Off by default, and on only when switched on globally and per client, with a way to send mail, a link secret, and harness scores for the configured model showing no unsafe drafts and at least 98% judged safe.
- Skipped for P1 and P2, security, account access, lost device and request tickets, low-confidence routing and suspected injection.
- The model writes only the steps; the rest of the email is fixed text saying it was written by AI. Steps with links, requests for credentials or personal details, admin actions, placeholders or promises are dropped, never edited.
- The "this fixed it" link is HMAC-signed, expires after 14 days, holds only a ticket and suggestion id, and works once. Opening it shows a confirm button and changes nothing, so mail scanners that follow links cannot close tickets. Only the button closes the ticket, the close is logged as the client's, and the engineer is told.

## Deployment

The demo runs behind a Cloudflare Tunnel with no public ports, and Cloudflare Access in front of the copilot and Zammad. Only the client confirm page (`/fixed/`) is outside Access. See [`docs/deploy.md`](docs/deploy.md).
