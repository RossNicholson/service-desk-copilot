"""Derives ground-truth labels from a thread plan and its rendered emails."""

from __future__ import annotations

from datetime import timedelta

from copilot.priority import priority
from datagen.plan import SCOPE_IMPACT, ThreadPlan
from datagen.schema import (
    ExpectedAlert,
    MessageLabel,
    NoteFacts,
    PiiItem,
    RenderInfo,
    SecondaryIssue,
    Thread,
    ThreadLabels,
    TicketFields,
    Timing,
)
from datagen.world import World

DEADLINE_QUESTION = "Is there a deadline or business impact?"


def _affected(plan: ThreadPlan, caller_name: str) -> str:
    if plan.final_scope_text:
        return plan.final_scope_text
    return f"{caller_name} only" if plan.final_scope == "single" else plan.final_scope


def build_labels(
    world: World, plan: ThreadPlan, thread: Thread, render: RenderInfo
) -> ThreadLabels:
    client = world.client(plan.client_id)
    issue = world.issue(plan.issue_id)
    caller = next(p for p in client.people if p.id == plan.caller_id)
    impact = SCOPE_IMPACT[plan.final_scope]
    pri = priority(impact, plan.final_urgency)

    fields = TicketFields(
        caller=caller.email,
        client_company=client.name,
        category=issue.category,
        subcategory=issue.subcategory,
        affected_service=client.services[issue.service],
        impact=impact,
        urgency=plan.final_urgency,
        priority=pri,
        assignment_group=world.group_name(issue.group),
        short_description=issue.short_description,
    )

    questions = {d.key: d.question for d in issue.details}
    open_questions = [questions[f.key] for f in plan.details_missing if f.key in questions]
    if plan.urgency_text is None:
        open_questions.append(DEADLINE_QUESTION)
    note = NoteFacts(
        reported=plan.problem,
        affected=_affected(plan, caller.name),
        tried=plan.tried,
        asks=plan.asks,
        deadline_or_impact=plan.urgency_text,
        details=[f.text for f in plan.details_stated],
        open_questions=open_questions,
    )

    def updates(m) -> dict[str, str | int]:
        if m.kind != "escalation":
            return {}
        before_impact = SCOPE_IMPACT[plan.scope]
        before = {
            "impact": before_impact,
            "urgency": plan.urgency,
            "priority": priority(before_impact, plan.urgency),
        }
        after = {"impact": impact, "urgency": plan.final_urgency, "priority": pri}
        return {k: v for k, v in after.items() if v != before[k]}

    messages = [
        MessageLabel(
            index=m.index,
            direction=m.direction,
            is_chase=m.kind in ("chase", "resend"),
            frustration=m.frustration if m.kind in ("chase", "resend") else 0,
            false_alarm=m.kind == "false_alarm",
            injection=m.injection_type,
            field_updates=updates(m),
        )
        for m in plan.messages
    ]

    target = world.msp.response_targets_minutes[priority(SCOPE_IMPACT[plan.scope], plan.urgency)]
    first_inbound = plan.messages[0].sent_at
    first_response = next((m.sent_at for m in plan.messages if m.direction == "outbound"), None)
    deadline = first_inbound + timedelta(minutes=target)
    breached = first_response is None and plan.observed_at > deadline
    timing = Timing(
        first_inbound_at=first_inbound,
        response_target_minutes=target,
        first_response_at=first_response,
        observed_at=plan.observed_at,
        response_breached=breached,
        breach_at=deadline if breached else None,
    )

    alerts = [
        ExpectedAlert(kind="chase", message_index=m.index, at=m.sent_at)
        for m in plan.messages
        if m.kind in ("chase", "resend")
    ]
    if breached:
        alerts.append(ExpectedAlert(kind="silence", at=deadline))
    alerts.sort(key=lambda a: a.at)

    # PII: every known personal value that actually appears in the emails.
    text = "\n".join(
        f"{e.from_name} {e.from_address} {' '.join(e.to + e.cc)} {e.body}" for e in thread.messages
    )
    pii: list[PiiItem] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str | None) -> None:
        if value and value in text and (kind, value) not in seen:
            seen.add((kind, value))
            pii.append(PiiItem(type=kind, value=value))

    for person in client.people:
        add("person", person.name)
        add("email", person.email)
        add("phone", person.phone)
        add("phone", person.mobile)
    for kind, value in plan.pii_values:
        add(kind, value)
    if issue.id == "new-starter":
        add("person", plan.fills["colleague"])

    return ThreadLabels(
        thread_id=plan.thread_id,
        split=plan.split,
        client_id=client.id,
        caller_id=caller.id,
        reported_by_id=plan.reported_by_id,
        issue_id=issue.id,
        fields=fields,
        note=note,
        secondary_issues=[
            SecondaryIssue(
                issue_id=s.issue_id,
                category=s.category,
                subcategory=s.subcategory,
                summary=s.problem,
            )
            for s in plan.secondary
        ],
        traits=plan.traits,
        messages=messages,
        pii=pii,
        timing=timing,
        expected_alerts=alerts,
        render=render,
    )
