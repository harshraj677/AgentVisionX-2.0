"""Detection, redaction and policy tests. These run the real Presidio engine (local, no network)."""
import re

import pytest

from app.core.config import Settings
from app.main import shared_detector
from app.privacy.policy import DEFAULT_POLICY, PolicyEngine
from app.services.privacy_service import PrivacyService

OPENAI_KEY = "sk-proj-" + "A1b2C3d4" * 4
GOOGLE_KEY = "AIza" + "SyA1234567890abcdefghijklmnopqrstuv"  # 4 + 35 chars, like real keys
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_" + "a1B2" * 9


@pytest.fixture(scope="module")
def service() -> PrivacyService:
    settings = Settings(_env_file=None)
    return PrivacyService(settings, shared_detector(settings.privacy_min_score))


def categories(service: PrivacyService, text: str) -> dict[str, str]:
    return {text[f.start : f.end]: f.category for f in service._findings(text)}


# ---- detection ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, value, category",
    [
        ("Write to jane.doe@example.com today", "jane.doe@example.com", "EMAIL_ADDRESS"),
        ("Call +91 98765 43210 please", "+91 98765 43210", "PHONE_NUMBER"),
        ("Call (415) 555-2671 please", "(415) 555-2671", "PHONE_NUMBER"),
        ("My mobile is 9876543210", "9876543210", "PHONE_NUMBER"),
        (f"use {OPENAI_KEY} for it", OPENAI_KEY, "API_KEY"),
        (f"key={GOOGLE_KEY}", GOOGLE_KEY, "API_KEY"),
        (f"aws {AWS_KEY}", AWS_KEY, "API_KEY"),
        (f"token {GITHUB_TOKEN}", GITHUB_TOKEN, "API_KEY"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123", "Bearer abcdefghijklmnopqrstuvwxyz0123", "API_KEY"),
        ("password = hunter2secret", "password = hunter2secret", "PASSWORD_OR_SECRET"),
        ("card 4111 1111 1111 1111", "4111 1111 1111 1111", "CREDIT_CARD"),
        ("ssn 219-09-9999", "219-09-9999", "US_SSN"),
        ("aadhaar 2345 6789 0123", "2345 6789 0123", "IN_AADHAAR"),
        ("pan ABCDE1234F", "ABCDE1234F", "IN_PAN"),
        ("server 192.168.1.10 is down", "192.168.1.10", "IP_ADDRESS"),
        ("My name is John Smith", "John Smith", "PERSON"),
    ],
)
def test_detects_supported_categories(service, text, value, category):
    assert categories(service, text).get(value) == category


def test_detects_multiline_private_key(service):
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc123\nxyz\n-----END RSA PRIVATE KEY-----"
    found = categories(service, f"here:\n{pem}\nthanks")
    assert found == {pem: "PRIVATE_KEY"}


@pytest.mark.parametrize(
    "text",
    [
        "Hello, can you explain recursion?",
        "Explain TCP vs UDP in 3 bullet points",
        "Summarize the French Revolution of 1789 in Paris",
        "Python 3.11.2 released; order 1234567890123 shipped",
        "Write a function that sorts a list of numbers",
    ],
)
def test_benign_text_has_no_findings(service, text):
    assert service._findings(text) == []


def test_bracketed_secret_cannot_hide_behind_placeholder_masking(service):
    assert categories(service, f"[{AWS_KEY}]") == {AWS_KEY: "API_KEY"}


# ---- redaction ---------------------------------------------------------------------------

def test_redaction_replaces_values_and_keeps_surrounding_text(service):
    text = f"send it to jane.doe@example.com and use {OPENAI_KEY} now"
    redacted = service.apply_redaction(text, service._findings(text))
    assert redacted == "send it to [EMAIL_ADDRESS] and use [API_KEY] now"


def test_redacted_text_rescans_clean(service):
    text = f"Mail a@b.com, call +91 98765 43210, key {AWS_KEY}, password: hunter2secret, card 4111 1111 1111 1111"
    redacted = service.apply_redaction(text, service._findings(text))
    assert service._findings(redacted) == []
    for secret in ("a@b.com", "98765", AWS_KEY, "hunter2secret", "4111"):
        assert secret not in redacted


def test_redaction_offsets_survive_emoji(service):
    text = "hello 😀😀 mail x@y.com bye"
    redacted = service.apply_redaction(text, service._findings(text))
    assert redacted == "hello 😀😀 mail [EMAIL_ADDRESS] bye"


def test_overlapping_matches_collapse_into_one_finding(service):
    text = "password = 4111 1111 1111 1111"  # secret-assignment overlaps a card number
    findings = service._findings(text)
    assert len(findings) == 1
    assert findings[0].action == "block"
    assert service.apply_redaction(text, findings) == f"[{findings[0].category}]"


# ---- policy ------------------------------------------------------------------------------

def test_default_policy_actions():
    policy = PolicyEngine()
    assert policy.action_for("API_KEY") == "block"
    assert policy.action_for("CREDIT_CARD") == "block"
    assert policy.action_for("EMAIL_ADDRESS") == "redact"
    assert policy.action_for("PERSON") == "warn"
    assert policy.action_for("SOMETHING_NEW") == "warn"  # unknown -> default action


def test_policy_overrides_and_default_action():
    policy = PolicyEngine({"email_address": "allow", "PERSON": "block"}, default_action="redact")
    assert policy.action_for("EMAIL_ADDRESS") == "allow"
    assert policy.action_for("PERSON") == "block"
    assert policy.action_for("UNKNOWN") == "redact"


def test_invalid_policy_is_rejected():
    with pytest.raises(ValueError):
        PolicyEngine({"EMAIL_ADDRESS": "ignore"})
    with pytest.raises(ValueError):
        PolicyEngine(default_action="nope")
    with pytest.raises(ValueError):  # also rejected at settings level (fail at startup, not at request time)
        Settings(_env_file=None, privacy_policy={"EMAIL_ADDRESS": "ignore"})


def test_strictest_action_wins():
    assert PolicyEngine.strictest([]) == "allow"
    assert PolicyEngine.strictest(["warn", "block", "redact"]) == "block"
    assert PolicyEngine.strictest(["warn", "redact"]) == "redact"


def test_decision_is_strictest_finding(service):
    cases = {
        "just a question": ("allow", True, False),
        "My name is John Smith": ("warn", True, True),
        "mail a@b.com": ("redact", False, False),
        f"key {AWS_KEY} and mail a@b.com": ("block", False, False),
    }
    for text, (decision, can_proceed, needs_confirm) in cases.items():
        r = service._response(service._findings(text))
        assert (r.decision, r.can_proceed, r.requires_confirmation) == (decision, can_proceed, needs_confirm), text


def test_configured_policy_changes_decisions():
    settings = Settings(_env_file=None, privacy_policy={"EMAIL_ADDRESS": "allow", "PERSON": "block"})
    svc = PrivacyService(settings, shared_detector(settings.privacy_min_score))
    assert svc._findings("mail a@b.com") == []
    assert svc._response(svc._findings("My name is John Smith")).decision == "block"


def test_default_policy_covers_every_scanned_category_that_is_sensitive():
    for category in ("API_KEY", "PRIVATE_KEY", "PASSWORD_OR_SECRET", "EMAIL_ADDRESS", "PHONE_NUMBER"):
        assert category in DEFAULT_POLICY


# ---- no sensitive values retained -----------------------------------------------------------

def test_detector_cache_is_keyed_by_hash_not_text(service):
    secret_text = f"remember {OPENAI_KEY}"
    service._findings(secret_text)
    for key in service.detector._cache:
        assert re.fullmatch(r"[0-9a-f]{64}", key)
        assert OPENAI_KEY not in key


def test_findings_contain_no_value_fields(service):
    dumped = [f.model_dump() for f in service._findings(f"key {OPENAI_KEY}")]
    assert set(dumped[0]) == {"category", "start", "end", "score", "action"}
    assert OPENAI_KEY not in str(dumped)
