from copilot.text import core_text, f1, forwarded_text, new_text, recall, tokens

REPLY = """Hi,

Any update on this?

Thanks,
Sam

Sam Jones
Office Manager

________________________________
From: Northgate Service Desk <support@northgate-ms.example>
Sent: Monday, 5 October 2026 10:38
To: sam.jones@harbourline-bs.example
Subject: RE: Printer

We've logged this."""

FORWARD = """Please see below. Thanks.

Kind regards,
Alex

---------- Forwarded message ---------
From: Kim Lee <kim.lee@harbourline-bs.example>
Date: Mon, 5 Oct 2026 at 09:14
Subject: Printer
To: Alex Ray <alex.ray@harbourline-bs.example>

Hi Alex,

The printer is offline.

Kim

Kim Lee
Cashier"""


def test_new_text_drops_quoted_history():
    assert "We've logged this" not in new_text(REPLY)
    assert "Any update" in new_text(REPLY)


def test_core_text_drops_the_signature():
    assert core_text(REPLY) == "Hi,\n\nAny update on this?"


def test_forwarded_text_extracts_the_original():
    original = forwarded_text(FORWARD)
    assert "The printer is offline." in original
    assert "Forwarded message" not in original
    assert "From: Kim Lee" not in original


def test_gmail_style_quotes_are_dropped():
    body = "Still broken.\n\nOn Mon, 5 Oct 2026 at 09:14, Kim Lee <kim@x.example> wrote:\n> hi"
    assert new_text(body).strip() == "Still broken."


def test_token_helpers():
    assert tokens("The printer is OFFLINE!") == ["printer", "offline"]
    assert recall("printer offline", "the printer is offline today") == 1.0
    assert f1("printer offline", "printer") == 2 / 3
