# Synthetic data generator

Generates messy client email threads sent to the service desk of **Northgate Managed Services**, a fictional MSP, by staff at five fictional financial services clients. Every thread comes with ground-truth labels for the evaluation harness.

Everything here is fictional. No real company, client or employee data is used.

## What is generated

| Split | Threads | Purpose |
|---|---|---|
| `data/dev/` | 200 | Building and tuning prompts, heuristics and code |
| `data/test/` | 100 | Held out for scoring only. Do not read or tune against it. |

Each split has two files, one JSON object per line (the format is defined in `src/copilot/evaluation/dataset.py`):

- `threads.jsonl`: the emails only (sender, recipients, date header, subject, body). This is what the copilot sees.
- `labels.jsonl`: the ground truth for each thread, kept separate so a model under evaluation never sees it.

`data/MANIFEST.json` records counts, seeds, the trait and priority mix, the renderer and model used, the cost, and a hash of every input file.

### Labels

Per thread:

- **fields**: caller, client company, category, subcategory, affected service, impact, urgency, priority, assignment group and a reference short description.
- **note**: what a faithful structured note must contain: what was reported, who is affected, what was tried, what is asked, deadline or business impact, specific details stated, and the open questions for facts the client left out.
- **messages**: per email, whether it is a chase (and how frustrated), a false-alarm chase ("no rush"), carries a prompt injection, or changes ticket fields (for example a problem spreading to a whole site).
- **timing** and **expected_alerts**: the response target for the priority, when (if ever) Northgate first responded, the moment alerts are judged, whether the target was breached, and every chase or silence alert that should fire.
- **pii**: every personal value that appears in the emails (names, email addresses, phone numbers, account numbers, sort codes, dates of birth, NI numbers, test card numbers and client references), for measuring PII leakage.
- **traits**: forwarded, multi-issue, chasing, false alarm, silence, adversarial, PII-heavy, escalation (the problem spreads in a later email).

Response targets are wall-clock minutes for now (`datagen/world/northgate.yaml`). Business hours can come later if the check-in work needs them.

## How a thread is made

1. **Plan** (`plan.py`, seeded). Picks the client, caller, issue (`world/issues.yaml`), scope, urgency, which details are stated or left out, what was tried, the asks, and the message timeline: Northgate's acknowledgement (unless the thread is a silence case), follow-ups adding detail, problems getting worse, false alarms and chases. Traits are allocated exactly to the shares in `scenarios/mix.yaml`.
2. **Render**. Writes the text each sender typed, from the plan.
   - `template` (default): phrase templates, no API key needed. Used for the committed data and in tests.
   - `model`: your own model via LiteLLM, prompted with `prompts/render_thread.v1.md`. The output is checked for every planned fact and retried if any are missing.
3. **Messify** (`messify.py`, seeded). Adds signatures and regulatory disclaimers, "Sent from my iPhone", forwarded and quoted chains, stacked RE: and FW: prefixes, typos at each person's own rate, and mixed date formats and time zones.
4. **Label and validate** (`labels.py`, `validate.py`). Labels come from the plan, so they hold for either renderer. Generation stops if any thread fails validation.

## Running it

```bash
uv run python -m datagen.generate --dry-run            # list the plans, write nothing
uv run python -m datagen.generate                      # template renderer, both splits
uv run python -m datagen.validate                      # check the committed data
```

### With your own model

Set your provider in `.env` (see `.env.example`), for example:

```bash
LLM_MODEL=anthropic/claude-haiku-5-5
LLM_API_KEY=your-key
```

then:

```bash
uv run python -m datagen.generate --renderer model --max-cost 5
```

The run stops cleanly at `--max-cost` (default `SPEND_CAP_USD`) and writes nothing for a split it could not finish. Calls are spaced to `RATE_LIMIT_PER_MINUTE`. Your key is passed to LiteLLM and is never written to the data, the manifest or the logs.

Model-rendered data reads more naturally but is not byte-for-byte reproducible, so the reproducibility test is skipped when the manifest says a model was used.

## Keeping it fictional

`validate.py` runs in the tests and fails on:

- email addresses or domains outside the reserved `.example` TLD;
- UK phone numbers outside Ofcom's drama ranges (for example `07700 900xxx`, `020 7946 0xxx`, `0113 496 0xxx`);
- card numbers that are not documented test numbers;
- National Insurance numbers without the `QQ` prefix used in official examples;
- em dashes.

Account numbers and sort codes are random digits and are not linked to any person. Names are random combinations from `names.py`; any resemblance to a real person is coincidental. Client names were checked against web search results and none matched a real firm.

## Changing the data

Edit the world (`clients/`, `world/`), the mix (`scenarios/mix.yaml`) or the templates (`pools.py`), regenerate, and commit the data with the change. Bump the prompt file name (`render_thread.v2.md`) rather than editing a prompt in place, so the manifest's prompt version stays meaningful.
