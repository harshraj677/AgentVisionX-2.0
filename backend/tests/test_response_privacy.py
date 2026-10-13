"""Response-privacy unit tests: detection, false positives, policy table, redaction integrity. Real Presidio, no network."""
import pytest

from app.core.config import Settings
from app.main import shared_detector
from app.privacy.response_policy import DEFAULT_RESPONSE_POLICY, ResponsePolicy
from app.services.privacy_service import PrivacyService
from app.services.response_privacy_service import ResponsePrivacyService

SECRET = "sk-proj-" + "Zq9Xw8Vr" * 4
GOOGLE = "AIzaSyA1234567890abcdefghijklmnopqrstuv"
EMAIL = "priya.sharma@example.com"


def make(**kw) -> ResponsePrivacyService:
    settings = Settings(_env_file=None, **kw)
    return ResponsePrivacyService(settings, shared_detector(settings.privacy_min_score))


@pytest.fixture(scope="module")
def svc() -> ResponsePrivacyService:
    return make()


def scan(service, text):
    return service._scan(text)


# ---- detection and default handling ------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, category, status",
    [
        (f"Use the key {SECRET} in your config.", "API_KEY", "redacted"),
        (f"Set GOOGLE_KEY={GOOGLE}", "API_KEY", "redacted"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "PRIVATE_KEY", "redacted"),
        ("password = hunter2secret", "PASSWORD_OR_SECRET", "redacted"),
        ("Test card 4111 1111 1111 1111 works in sandbox.", "CREDIT_CARD", "redacted"),
        ("Aadhaar 2345 6789 0123", "IN_AADHAAR", "redacted"),
        ("SSN 219-09-9999", "US_SSN", "redacted"),
        (f"Write to {EMAIL} for help.", "EMAIL_ADDRESS", "warned"),
        ("Call +1 (415) 555-2671.", "PHONE_NUMBER", "warned"),
        ("Your PAN is ABCDE1234F.", "IN_PAN", "warned"),
    ],
)
def test_default_policy_handling_per_category(svc, text, category, status):
    out, summary, failure = scan(svc, text)
    assert failure is None and summary.status == status
    assert category in summary.categories
    if status == "redacted":
        assert f"[{category}]" in out


@pytest.mark.parametrize(
    "text",
    [
        "Recursion is when a function calls itself. For example, factorial(5) = 120.",
        "Here is code:\n\n```python\ndef add(a, b):\n    return a + b\n```\n\nSee https://docs.python.org/3/ and version 3.11.2 (2022).",
        "Send it to [EMAIL_ADDRESS] with key [API_KEY] and password [PASSWORD_OR_SECRET] or [PHONE_NUMBER].",
        "Albert Einstein and Marie Curie won Nobel Prizes. Ask Dr. Smith at the Paris office.",  # names are ignored by default
        "1. First step\n2. Second step\n3. Third step on 2024-05-01 at 10:30",
        "The server at 192.168.1.10 is down.",  # IP addresses are ignored by default
        "Order 1234567890123 totals 99.99 USD. Call it Project Falcon.",
        "Capitalized Words Are Not Sensitive. Neither Is Title Case Text.",
        "I'm sorry, I can't help with that.",
    ],
)
def test_benign_and_placeholder_text_is_clean(svc, text):
    out, summary, failure = scan(svc, text)
    assert (summary.status, summary.findings_count, failure) == ("clean", 0, None)
    assert out == text


def test_empty_and_whitespace_replies_are_clean_and_unscanned(svc):
    for text in ("", "   \n\t "):
        out, summary, failure = scan(svc, text)
        assert out == text and summary.status == "clean" and failure is None


def test_reply_beyond_size_limit_is_withheld_not_passed_through():
    service = make(response_privacy_max_chars=1000)
    out, summary, failure = scan(service, "word " * 400)
    assert failure == "response_privacy_unavailable"


# ---- redaction integrity -----------------------------------------------------------------------------------

def test_redaction_replaces_only_sensitive_spans_and_keeps_formatting(svc):
    text = f"Config:\n\n```env\nAPI={SECRET}\nURL=https://example.com/a?b=1\n```\n\n- keep this line\n- and this one\n"
    out, summary, _ = scan(svc, text)
    assert out == "Config:\n\n```env\nAPI=[API_KEY]\nURL=https://example.com/a?b=1\n```\n\n- keep this line\n- and this one\n"
    assert summary.redacted and summary.redacted_spans == 1


def test_multiple_spans_and_unicode_offsets(svc):
    text = f"😀 key {SECRET} 日本語 and again {GOOGLE} — card 4111 1111 1111 1111 🎉"
    out, summary, _ = scan(svc, text)
    assert out == "😀 key [API_KEY] 日本語 and again [API_KEY] — card [CREDIT_CARD] 🎉"
    assert summary.redacted_spans == 3 and summary.categories == {"API_KEY": 2, "CREDIT_CARD": 1}


def test_warn_level_findings_are_left_untouched_when_redacting_a_secret(svc):
    out, summary, _ = scan(svc, f"mail {EMAIL} key {SECRET}")
    assert out == f"mail {EMAIL} key [API_KEY]"
    assert summary.status == "redacted" and summary.categories == {"EMAIL_ADDRESS": 1, "API_KEY": 1}


def test_overlapping_matches_are_redacted_once(svc):
    out, summary, _ = scan(svc, "password = 4111 1111 1111 1111")
    assert out.startswith("[") and out.endswith("]") and "4111" not in out
    assert summary.redacted_spans == 1


def test_redacted_output_rescans_clean_and_leaks_no_value(svc):
    text = f"{SECRET} {GOOGLE} -----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY----- password: hunter2secret 219-09-9999"
    out, summary, failure = scan(svc, text)
    assert failure is None
    assert [f for f in svc._findings(out) if f.action in ("redact", "block")] == []
    for secret in (SECRET, GOOGLE, "hunter2secret", "219-09-9999", "abc\n"):
        assert secret not in out
    assert SECRET not in str(summary.model_dump())


def test_summary_contains_only_names_and_counts(svc):
    _, summary, _ = scan(svc, f"{SECRET} {EMAIL}")
    assert set(summary.model_dump()) == {"status", "policy", "findings_count", "categories", "redacted", "redacted_spans"}
    assert EMAIL not in str(summary) and SECRET not in str(summary)


# ---- policy -------------------------------------------------------------------------------------------------

def test_default_table_redacts_secrets_and_warns_on_contact_data():
    p = ResponsePolicy()
    assert {p.action_for(c) for c in ("API_KEY", "PRIVATE_KEY", "PASSWORD_OR_SECRET", "CREDIT_CARD")} == {"redact"}
    assert p.action_for("EMAIL_ADDRESS") == p.action_for("PHONE_NUMBER") == "warn"
    assert p.action_for("PERSON") == "ignore"
    assert p.action_for("SOMETHING_NEW") == "warn"  # RESPONSE_PRIVACY_DEFAULT_ACTION


@pytest.mark.parametrize("mode", ["allow", "warn", "redact", "block"])
def test_uniform_modes_apply_to_every_reported_category_but_not_ignored_ones(mode):
    p = ResponsePolicy(mode)
    assert p.action_for("API_KEY") == p.action_for("EMAIL_ADDRESS") == mode
    assert p.action_for("PERSON") == "ignore"  # noisy categories stay ignored unless listed explicitly


def test_explicit_overrides_win_over_mode_and_table():
    p = ResponsePolicy("block", {"email_address": "allow", "PERSON": "warn"})
    assert p.action_for("EMAIL_ADDRESS") == "allow" and p.action_for("PERSON") == "warn" and p.action_for("API_KEY") == "block"
    assert ResponsePolicy("policy", {"API_KEY": "block"}).action_for("API_KEY") == "block"


def test_invalid_policy_values_are_rejected():
    with pytest.raises(ValueError):
        ResponsePolicy("shout")
    with pytest.raises(ValueError):
        ResponsePolicy("policy", {"API_KEY": "delete"})
    with pytest.raises(ValueError):
        Settings(_env_file=None, response_privacy_mode="shout")
    with pytest.raises(ValueError):
        Settings(_env_file=None, response_privacy_policy={"API_KEY": "delete"})


def test_response_policy_is_independent_of_input_policy():
    settings = Settings(_env_file=None, privacy_policy={"EMAIL_ADDRESS": "block"}, response_privacy_policy={"EMAIL_ADDRESS": "allow"})
    detector = shared_detector(settings.privacy_min_score)
    inbound, outbound = PrivacyService(settings, detector), ResponsePrivacyService(settings, detector)
    assert inbound.policy.action_for("EMAIL_ADDRESS") == "block"
    assert outbound.policy.action_for("EMAIL_ADDRESS") == "allow"
    # and the built-in input defaults are untouched by response defaults
    assert PrivacyService(Settings(_env_file=None), detector).policy.action_for("EMAIL_ADDRESS") == "redact"
    assert DEFAULT_RESPONSE_POLICY["EMAIL_ADDRESS"] == "warn"


def test_allow_mode_records_findings_without_changing_text():
    out, summary, failure = scan(make(response_privacy_mode="allow"), f"key {SECRET}")
    assert out == f"key {SECRET}" and summary.status == "allowed" and summary.categories == {"API_KEY": 1} and failure is None


def test_block_mode_withholds_on_any_reported_finding():
    out, summary, failure = scan(make(response_privacy_mode="block"), f"mail {EMAIL}")
    assert failure == "response_privacy_blocked" and summary.status == "blocked"
    assert scan(make(response_privacy_mode="block"), "just text")[2] is None
