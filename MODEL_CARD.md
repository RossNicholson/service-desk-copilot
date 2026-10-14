# Model card: Service Desk Copilot

Service Desk Copilot is not a trained model. It is a set of prompts and checks around a general-purpose model that the MSP chooses. This card describes what the model is asked to do, what it is never trusted with, and how it is measured.

## The model

- **Bring your own.** Any model LiteLLM supports: Anthropic, OpenAI, Azure OpenAI, Google, AWS Bedrock, or a local model through Ollama. Set `LLM_MODEL` and `LLM_API_KEY`.
- **Default:** `anthropic/claude-haiku-5-5`, a cheap, fast model. The tasks are short and structured, so a small model is the starting point; the harness says whether it is good enough.
- **Settings:** temperature 0, structured output against a Pydantic schema for every call.

## What the model is asked to do

| Task | Prompt | Output | What the code does with it |
|---|---|---|---|
| Ticket fields | `extract_fields.v1` | Caller, route, impact, urgency and short description, each with a confidence and a reason | Category, subcategory, service and team come from the MSP's routing table for the chosen route; priority from the impact and urgency matrix. Anything outside the options given is dropped and flagged. Below 60% confidence is flagged for review. |
| Note | `write_note.v1` | Reported, affected, tried, asks, deadline, with a quote for each item | Items whose quote is not in the client's email are dropped. Open questions come from the desk's own checklist for the subcategory. |
| Chase check | `assess_followup.v1` | Chase or not, frustration 0 to 3, confidence, reason | Plain resends are caught without a call. Escalation rules are in code. |
| Suggestion steps | `suggest_steps.v1` | Two to four steps, or none | Skip rules run first and in code. Steps failing the draft check are dropped. The email around them is fixed text. |
| Judge (evaluation only) | `judge_note.v1`, `judge_suggestion.v1` | Which facts a note covers, which items are unsupported; whether steps are safe and useful | Reported separately, with its own cost. |

Prompts live in `src/copilot/prompts/`. A change that could move the scores gets a new version number.

## Intended use

Helping service desk engineers at a managed service provider triage and keep up with client email tickets: proposing ticket fields, writing first-draft notes, keeping fields current, and flagging clients who are waiting. Every output is a proposal an engineer can change.

It is **not** for deciding anything on its own: it does not close tickets on its own judgement, assign work to people, change ticket states, or reply to clients, apart from the labelled suggestion email when the MSP switches it on.

## Data

- **Inputs:** client emails, after personal details are replaced with placeholders. The model never sees names, email addresses, phone numbers or account numbers ([`docs/redaction.md`](docs/redaction.md)).
- **Evaluation data:** about 300 synthetic email threads from fictional financial services clients of a fictional MSP, with typos, forwards, multiple issues, chasing, false alarms, prompt injection attempts and heavy personal details. 200 are for development and 100 are held out for scoring only. Each thread's scenario is its ground truth ([`datagen/README.md`](datagen/README.md)).
- **No training.** Nothing is fine-tuned, and emails are not used to train anything.

## Evaluation

`uv run python -m copilot.evaluation` scores every system against a rule-based baseline. Reports go to `reports/`.

| Area | Measured as |
|---|---|
| Ticket fields | Accuracy per field and overall, share flagged for review, accuracy of unflagged fields |
| Notes | Completeness and faithfulness (lexical, and model-judged with `--judge`), open question recall |
| Check-ins | Recall and precision of field updates when a later email changes the facts |
| Chasing and alerts | Chase precision, recall and false alarm rate; alert precision and recall by kind |
| Suggestion emails | Drafted rate, skip accuracy, unsafe drafts (must be 0), dropped rate, judged safety and usefulness |
| Privacy | Personal details leaked to the model (must be 0), leakage by type, and the share of other details the redactor keeps |
| Cost | Calls, tokens and US dollars per ticket |

**Baseline, held-out test split (100 threads):** 73.3% of fields right, notes 100% faithful but 38% complete, alerts 69% precise and 61% recalled. The redactor leaked none of the labelled personal details and kept 98.1% of other details.

**Copilot:** not yet scored, because the repository's CI has no model key. Run the harness with your own key to score the model you plan to use; the report records the model and prompt versions it scored.

## Limitations

- Scores are on synthetic data written to resemble real service desk email. Real email will differ; score a sample of your own (redacted) tickets before relying on it.
- Lexical note scores undercount paraphrases; the judge scores are the better guide, but a model grading a model has its own errors.
- The injection check is a phrase list. It is a second line of defence behind treating email as data, not a guarantee.
- Redaction can miss unusual names or formats. The harness measures it on the synthetic data, which cannot cover everything.
- English only, and tuned for UK formats.
