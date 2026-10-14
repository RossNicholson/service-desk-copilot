# Evaluation harness

The evaluation harness is the centrepiece of Service Desk Copilot: every claim about the copilot is a number from this harness, next to the same number for a simple baseline.

```bash
uv run python -m copilot.evaluation                 # every system on the dev split
uv run python -m copilot.evaluation --split test    # the held-out test split
uv run python -m copilot.evaluation --system baseline --limit 20
```

It prints a Markdown comparison and writes to `reports/` (git-ignored):

- `eval-<split>.json`: every metric for every system;
- `eval-<split>.md`: the table that was printed;
- `results-<split>.jsonl`: every system's raw output per thread, for digging into failures.

CI runs it on the test split for every push and adds the table to the job summary.

## The contract

Every system implements one method:

```python
def process(self, thread: Thread, now: datetime) -> ThreadResult: ...
```

`Thread` is the emails only (`copilot.schemas`). `now` is the moment the thread is judged at, taken from the labels (`timing.observed_at`), so silence can be measured. The system never sees labels.

`ThreadResult` holds:

| Part | What it is |
|---|---|
| `fields` | Final value for each of the ten ticket fields, each with a confidence, a one-line reason and a `needs_review` flag |
| `field_changes` | Every value set or changed, with the email that prompted it (check-ins) |
| `notes` | A structured note after each client email |
| `chase_assessments` | For each follow-up email, whether it is the client chasing |
| `alerts` | Chase and silence alerts, with who to notify |
| `model_inputs` | Every piece of text sent to a model, so PII leakage can be measured |
| `usage` | Provider, model, prompt version, calls, tokens and cost |

There is deliberately no way for a result to reply to a client or close a ticket.

To add a system, implement `process` and register a factory in `SYSTEMS` in `src/copilot/evaluation/harness.py`.

## The labelled dataset

See `src/copilot/evaluation/dataset.py` for the models and `datagen/README.md` for how the data is made. Threads and labels are separate files so a system is only ever handed threads. The manifest records the label format version (`LABEL_FORMAT`), and loading fails if code and data disagree.

Only the harness reads `data/test/`. A test fails if anything else under `src/` refers to it.

## Metrics

| Group | Metric | Meaning |
|---|---|---|
| Fields | per-field accuracy, overall | Exact match after trimming and lower-casing; the short description counts as correct at a word-overlap F1 of 0.5 or more |
| | flagged for review | Share of field values the system flagged for a human |
| | unflagged accuracy | How often an unflagged value is right: what an engineer can trust without checking |
| Prompt injection | fields overall, priority, assignment group | The same accuracy on threads that carry an injection. A large drop means the email is steering the system |
| Notes | completeness | Share of labelled facts (what was reported, who is affected, what was tried, the asks, the deadline or impact, specific details) whose content words mostly (60%) appear in the final note |
| | faithfulness | Share of note items whose content words mostly (80%) appear in the client's emails. Invented details score low |
| | open questions recall | Share of expected open questions (facts the client left out) that the note asks |
| | unexpected questions per thread | Questions the labels do not expect |
| Check-ins | update recall, precision | Whether the system changed impact, urgency and priority on the email where the problem spread, to the right value. Precision counts any change after the first email that the labels do not expect as wrong |
| Chasing | precision, recall | Per follow-up email |
| | false alarm rate | Share of "no rush" style emails wrongly flagged as chasing |
| Alerts | precision, recall, per kind | Chase alerts must name the right email; silence alerts must fall within 15 minutes of the breach |
| PII | leakage rate, per type | Share of the personal values in a thread found in the text the system sent to a model. "n/a" when nothing was sent |
| Cost | per ticket | Mean cost in US dollars, model calls and tokens per thread |

### Known limits

- Note scores are lexical. They catch missing facts and obvious inventions cheaply, but not paraphrase. Phase 4 adds a model-graded judge alongside them.
- Escalation is the only labelled check-in event for now. Engineers would also want fields corrected when a missing detail arrives; those changes count against precision until they are labelled.
- Response targets are wall-clock minutes.
- The committed data is template-rendered, so wording is more regular than real email. Expect lower scores on model-rendered data.

## The baseline

`src/copilot/baseline.py` is what a few hours of keyword rules achieves, with no model. It takes the first sender as the caller (wrong for forwarded emails), the first matching keyword for the category, a handful of phrases for impact and urgency, copies likely-looking sentences into notes with no open questions, and treats any follow-up containing "update", "chasing", "still" or "again" as a chase.
