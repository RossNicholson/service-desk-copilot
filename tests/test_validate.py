import pytest

from datagen.validate import safety_problems


@pytest.mark.parametrize(
    "text",
    [
        "Call me on 07700 900123 or 020 7946 0456",
        "Leeds office 0113 496 0789",
        "Email jo.bloggs@harbourline-bs.example or see www.harbourline-bs.example",
        "Card 4111 1111 1111 1111",
        "NI number QQ 12 34 56 C",
        "File saved as Q3 forecast.xlsx",
    ],
)
def test_fictional_values_pass(text):
    assert safety_problems(text) == []


@pytest.mark.parametrize(
    "text, problem",
    [
        ("Email jo@realcompany.co.uk", "email address"),
        ("See https://www.realbank.com/login", "real-looking domain"),
        ("Ring 07911 123456", "phone number"),
        ("Ring 0113 222 3333", "phone number"),
        ("Card 4929 1234 5678 9012", "card number"),
        ("NI number AB 12 34 56 C", "NI number"),
        ("Done — thanks", "em dash"),
    ],
)
def test_real_looking_values_fail(text, problem):
    problems = safety_problems(text)
    assert any(problem in p for p in problems), problems
