# PII redaction

No email text reaches a model until personal details have been replaced with placeholders. The redactor is `src/copilot/redaction.py`; the model client (`src/copilot/llm.py`) refuses text that has not been through it.

## What is detected

Three layers run on every email, and anything any of them finds is redacted. Where spans overlap, the longest wins.

1. **Custom patterns** for UK formats: phone numbers (with or without +44), sort codes, any standalone eight-digit number (treated as an account number, because clients misspell "account" too often for a keyword to be reliable), National Insurance numbers, dates in dd/mm/yyyy form, card numbers, email addresses and client reference numbers such as `HLB-123456`. Names after a title (Mrs, Dr and so on) or in phrases about starters, leavers and customers are caught here too.
2. **The MSP's directory**: every client contact and engineer, by full name and in "Surname, First name" form.
3. **Microsoft Presidio** with spaCy's small English model, for names the other layers do not know, plus card, IBAN and NHS numbers.

Once a full name is found, the same person's first name or surname on its own ("Thanks, Patrick") gets the same placeholder. Placeholders are numbered per thread, so `[PERSON_1]` is the same person in every email of a ticket.

## What is kept

Some things look personal but an engineer needs them, and they are not personal data:

- job titles, team names, client names, site names and service names, which the small spaCy model sometimes tags as people;
- mailboxes that belong to a function rather than a person, such as `billing@` or `noreply@`, so a phishing report keeps the sender it claimed to be;
- the MSP's own support address.

## Restoring

The mapping from placeholder to original stays in the copilot's process. `Redactor.restore` puts the originals back into model output before an engineer sees it. Text in an email that already looks like a placeholder is changed to `(PERSON 1)` before redaction, so an email cannot get a real value restored in its place.

## How well it works

The harness scores the redactor on its own every run (`uv run python -m copilot.evaluation`, "Redaction" table): leakage per type, and how many of the labelled facts' words survive. On the dev split no labelled personal value survives redaction, and about 97% of the other words in the labelled facts are kept; most of the rest are client reference numbers, which are redacted on purpose.

## Limits

- The synthetic data is regular. Real email has more unusual name formats, so expect some names to get through spaCy's small model. Engineers' notes should still avoid copying personal details into tickets where they are not needed.
- Detection is English only.
- Presidio's email recogniser is not used, because it downloads the public suffix list at run time and redaction must never make a network call. The custom pattern covers email addresses instead.
