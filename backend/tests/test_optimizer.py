"""Unit tests for the deterministic optimizer: behaviour, meaning preservation and the safety guard."""
import re

import pytest

from app.core.config import Settings
from app.main import shared_detector
from app.optimizer.optimizer import RuleBasedOptimizer, check_preservation
from app.optimizer.rules import RULES, Rule, protected_spans
from app.optimizer.tokens import HeuristicCounter, TiktokenCounter
from app.services.privacy_service import PrivacyService

opt = RuleBasedOptimizer()
KEY = "sk-proj-" + "A1b2C3d4" * 4


def run(text: str) -> str:
    return opt.optimize(text).text


# ---- exact, deterministic behaviour --------------------------------------------------------

@pytest.mark.parametrize(
    "original, expected",
    [
        (
            "Hello, could you please explain in order to understand how HTTPS works?   Thanks in advance!",
            "Explain to understand how HTTPS works?",
        ),
        (
            "Hi there! I was wondering if you could write a summary of the French Revolution. "
            "Write a summary of the French Revolution. Please keep it under 100 words.",
            "Write a summary of the French Revolution. Keep it under 100 words.",
        ),
        (
            "I want you to basically list 5 fruits, do not include apples, in JSON format. Thank you.",
            "List 5 fruits, do not include apples, in JSON format.",
        ),
        ("Explain the the difference between TCP and UDP, please.", "Explain the difference between TCP and UDP."),
        ("Write a poem.\n\n\n\nMake it rhyme.  Make it rhyme.", "Write a poem.\n\nMake it rhyme."),
    ],
)
def test_optimizations(original, expected):
    assert run(original) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Summarize this text in 3 bullet points.",
        "Hello world program in C",  # "Hello" without a comma is content, not a greeting
        "I had had enough of that that thing.",  # legitimate doubled words
        "Write a function.",
    ],
)
def test_already_clean_prompts_are_left_alone(text):
    result = opt.optimize(text)
    assert result.text == text and not result.changed and result.verified


def test_is_deterministic_and_idempotent():
    text = "Hello, could you please explain DNS? Explain DNS? Thanks!"
    first = run(text)
    assert run(text) == first
    assert run(first) == first


def test_never_longer_than_original():
    for text in [
        "Please, could you kindly make use of the the cache in order to be fast?   Thank you so much.",
        "Hey, basically write tests.\n\n\n\n\nbasically write tests.",
    ]:
        assert len(run(text)) <= len(text)


# ---- meaning preservation -------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, must_keep",
    [
        ("Please list 5 fruits, do not include apples, in JSON format.", ["5", "do not include apples", "JSON"]),
        ("Could you please summarize this in under 100 words and never use jargon?", ["under 100 words", "never use jargon"]),
        ("Basically, answer only with yes or no, without explanation.", ["only", "yes or no", "without explanation"]),
        ("Write exactly 3 sentences about 2024 budgets, at most 50 words. Thanks!", ["exactly 3", "2024", "at most 50 words"]),
        ("I want you to output a markdown table with columns Name, Age.", ["markdown table", "Name, Age"]),
        ("Please don't change the function name `get_user`.", ["on't change", "`get_user`"]),
    ],
)
def test_constraints_numbers_and_formats_survive(text, must_keep):
    optimized = run(text)
    for fragment in must_keep:
        assert fragment in optimized, f"lost {fragment!r}: {optimized!r}"
    assert opt.optimize(text).verified


def test_protected_content_is_never_modified():
    text = (
        'Please translate "please do not   touch" to French.\n'
        "```python\nx  =  1\n\n\n\nprint( 'please' )\n```\n"
        f"See https://example.com/a?b=1 or mail jane@example.com, key {KEY}. Thanks!"
    )
    optimized = run(text)
    assert set(protected_spans(text)) == set(protected_spans(optimized))
    for piece in ['"please do not   touch"', "x  =  1\n\n\n\nprint( 'please' )", "https://example.com/a?b=1", "jane@example.com", KEY]:
        assert piece in optimized


def test_optimizer_does_not_hide_sensitive_values_from_privacy_scan():
    settings = Settings(_env_file=None)
    privacy = PrivacyService(settings, shared_detector(settings.privacy_min_score))
    text = f"Hey, please debug this: key={KEY} and mail jane@example.com"
    # PERSON is excluded: it is NER-based, so re-capitalising a sentence start ("Debug") can change it.
    # The selected prompt is always re-scanned anyway; what matters is that secrets/contact data stay visible.
    sensitive = lambda t: {f.category for f in privacy._findings(t)} - {"PERSON"}
    assert sensitive(text) == sensitive(run(text)) == {"API_KEY", "EMAIL_ADDRESS"}


def test_placeholders_survive():
    assert "[EMAIL_ADDRESS]" in run("Please write to [EMAIL_ADDRESS] about the the meeting")


# ---- safety guard: bad rewrites are rejected ------------------------------------------------

def _broken(name, fn):
    return RuleBasedOptimizer([Rule(name, "broken on purpose", lambda t: (fn(t), 1))])


def test_guard_rejects_rewrite_that_drops_a_negation():
    result = _broken("drop_not", lambda t: t.replace("do not ", "")).optimize("List fruits, do not include apples.")
    assert result.text == "List fruits, do not include apples."
    assert not result.verified and not result.changed
    assert any(c.name == "no_content_words_lost" and not c.passed for c in result.checks)


def test_guard_rejects_rewrite_that_changes_a_number():
    result = _broken("bad_number", lambda t: t.replace("100", "10")).optimize("Keep it under 100 words.")
    assert result.text == "Keep it under 100 words." and not result.verified


def test_guard_rejects_rewrite_that_alters_a_quote_or_empties_the_text():
    original = 'Say "hello there" loudly.'
    # Rules only ever see masked text, so they cannot touch a quote; the check still catches it if one did.
    failed = {c.name for c in check_preservation(original, 'Say "hi" loudly.') if not c.passed}
    assert "protected_spans_kept" in failed
    assert not _broken("empty", lambda t: "").optimize(original).verified
    assert _broken("quote_edit", lambda t: t.replace("hello there", "hi")).optimize(original).text == original


def test_check_preservation_reports_each_check():
    names = [c.name for c in check_preservation("a b", "a b")]
    assert names == ["not_empty", "protected_spans_kept", "numbers_kept", "no_content_words_lost"]


def test_every_rule_has_unique_name_and_description():
    assert len({r.name for r in RULES}) == len(RULES)
    assert all(r.description for r in RULES)


# ---- token counters are labelled as estimates -------------------------------------------------

def test_heuristic_counter_is_labelled_as_a_rough_estimate():
    c = HeuristicCounter()
    assert c.count("abcdefgh") == 2
    assert "heuristic" in c.method and "rough" in c.note.lower()


def test_tiktoken_counter_when_available():
    try:
        counter = TiktokenCounter()
    except Exception:
        pytest.skip("tiktoken vocabulary unavailable (offline first run)")
    assert counter.count("Explain how HTTPS works.") > 0
    assert counter.method.startswith("tiktoken:")
    assert re.search(r"estimate", counter.note, re.I)
    assert counter.count(run("Hello, could you please explain DNS? Thanks!")) < counter.count(
        "Hello, could you please explain DNS? Thanks!"
    )
