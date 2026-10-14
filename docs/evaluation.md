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

### Redaction

A separate table scores the redactor on its own, over every email in the split, with no model involved. It runs on every harness run, including in CI.

| Metric | Meaning |
|---|---|
| leakage rate, per type | Share of the labelled personal values still present after redaction |
| details kept | Share of the words in the labelled facts (other than personal values) that survive redaction. A low number means the redactor is removing things engineers need |
| seconds per thread | Time to redact a whole thread |

### Known limits

- The default note scores are lexical. They catch missing facts and obvious inventions cheaply, but not paraphrase. `--judge` adds model-graded completeness and faithfulness (`src/copilot/evaluation/judge.py`, one call per thread per system, on redacted text), with the judge's cost reported separately. The judge is a model too, so treat its numbers as a second opinion, not ground truth.
- Escalation is the only labelled check-in event for now. Engineers would also want fields corrected when a missing detail arrives; those changes count against precision until they are labelled.
- Response targets are wall-clock minutes.
- The committed data is template-rendered, so wording is more regular than real email. Expect lower scores on model-rendered data.

## The copilot

`src/copilot/extraction.py` registers as `copilot`. It needs a model, so the harness only runs it when `LLM_API_KEY` (or `LLM_API_BASE` for a local model) is set; otherwise it prints that only the baseline ran. Asking for it with `--system copilot` and no model exits with an error rather than silently skipping it. A run is limited by `SPEND_CAP_USD` and `RATE_LIMIT_PER_MINUTE`; hitting the cap stops the run and marks it as failed.

For each inbound email the copilot makes two calls: one for the fields and one for the note (`src/copilot/notes.py`). Each follow-up from the client costs a third call, for chase detection (`src/copilot/chasing.py`), unless it plainly resends an earlier email, which counts as a chase without a call. Every note item must quote the client's email, and items whose quote is not in the emails are dropped. Open questions come from the desk's triage checklist for the ticket's subcategory (`src/copilot/knowledge/triage.yaml`). The chase check reads redacted text with how long the client has waited, and is told that "no rush" and added information are not chases. Alerts go to the assigned engineer and escalate to the team lead when the client chases again with no reply since the last alert, is angry or asks to escalate, or the ticket is still unanswered at twice its response target. The harness scores which alerts are raised, not who they go to. Its silence alert uses its own priority.

In service, `src/copilot/scheduler.py` runs a check-in every `CHECKIN_INTERVAL_MINUTES` (5 by default): tickets with new emails are processed again, every open ticket is checked for silence, and each alert is sent once.

## The baseline

`src/copilot/baseline.py` is what a few hours of keyword rules achieves, with no model. It takes the first sender as the caller (wrong for forwarded emails), the first matching keyword for the category, a handful of phrases for impact and urgency, copies likely-looking sentences into notes with no open questions, and treats any follow-up containing "update", "chasing", "still" or "again" as a chase.
