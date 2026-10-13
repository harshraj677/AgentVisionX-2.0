"""End-to-end privacy enforcement through the HTTP API, with a counting fake provider."""
import logging

import pytest

from tests.conftest import OK_RESULT, FakeProvider

SECRET = "sk-proj-" + "Zq9Xw8Vr" * 4
EMAIL = "priya.sharma@example.com"
EMAIL_LOCAL = "priya.sharma"


def chat_body(*contents: str, **extra) -> dict:
    roles = ["user", "assistant"]
    msgs = [{"role": roles[i % 2], "content": c} for i, c in enumerate(contents)]
    return {"messages": msgs, **extra}


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(OK_RESULT)


@pytest.fixture
def client(make_client, provider):
    return make_client(provider)


# ---- scan / redact endpoints ---------------------------------------------------------------

def test_scan_endpoint_reports_categories_without_echoing_values(client):
    r = client.post("/api/privacy/scan", json={"text": f"my key is {SECRET} and mail {EMAIL}"})
    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "block"
    assert data["can_proceed"] is False
    assert data["categories"] == {"API_KEY": 1, "EMAIL_ADDRESS": 1}
    assert SECRET not in r.text and EMAIL not in r.text


def test_scan_clean_text_is_allowed(client):
    data = client.post("/api/privacy/scan", json={"text": "Explain photosynthesis"}).json()
    assert (data["decision"], data["can_proceed"], data["findings"]) == ("allow", True, [])


def test_scan_rejects_oversized_text(make_client):
    r = make_client(max_message_chars=20).post("/api/privacy/scan", json={"text": "x" * 21})
    assert r.status_code == 422 and r.json()["error"]["code"] == "message_too_long"


def test_redact_endpoint_returns_rescanned_clean_text(client):
    r = client.post("/api/privacy/redact", json={"text": f"use {SECRET}, mail {EMAIL}"})
    data = r.json()
    assert data["redacted_text"] == "use [API_KEY], mail [EMAIL_ADDRESS]"
    assert data["scan"]["decision"] == "allow"  # re-scan of the final text


# ---- chat: blocked requests never reach the provider ---------------------------------------

def test_blocked_prompt_never_reaches_provider(client, provider):
    r = client.post("/api/chat", json=chat_body(f"my api key is {SECRET}"))
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "privacy_blocked"
    assert err["details"]["categories"] == {"API_KEY": 1}
    assert provider.calls == 0
    assert SECRET not in r.text


def test_redaction_required_prompt_never_reaches_provider(client, provider):
    r = client.post("/api/chat", json=chat_body(f"email me at {EMAIL}"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "privacy_redaction_required"
    assert provider.calls == 0
    assert EMAIL not in r.text


def test_privacy_gate_runs_before_provider_configuration_check(make_client):
    # Gemini is NOT configured here; a blocked prompt must still get the privacy error, not a provider error.
    r = make_client().post("/api/chat", json=chat_body(f"key {SECRET}"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy_blocked"


def test_secret_in_earlier_history_message_is_blocked(client, provider):
    r = client.post("/api/chat", json=chat_body(f"my key {SECRET}", "ok", "now summarize that"))
    assert r.status_code == 403
    assert r.json()["error"]["details"]["findings"][0]["message_index"] == 0
    assert provider.calls == 0


def test_warn_requires_confirmation_then_proceeds(client, provider):
    body = chat_body("My name is John Smith, help me write a cover letter")
    r = client.post("/api/chat", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "privacy_confirmation_required"
    assert provider.calls == 0

    r = client.post("/api/chat", json={**body, "privacy_ack": True})
    assert r.status_code == 200
    assert r.json()["metadata"]["privacy"] == {"checked": True, "decision": "warn", "categories": {"PERSON": 1}}
    assert provider.calls == 1


def test_acknowledgement_does_not_unlock_block_or_redact(client, provider):
    for text in (f"key {SECRET}", f"mail {EMAIL}"):
        r = client.post("/api/chat", json=chat_body(text, privacy_ack=True))
        assert r.status_code == 403
    assert provider.calls == 0


# ---- edited / redacted prompts ---------------------------------------------------------------

def test_edited_prompt_is_rescanned_and_allowed_when_clean(client, provider):
    assert client.post("/api/chat", json=chat_body(f"debug this: key={SECRET}")).status_code == 403
    r = client.post("/api/chat", json=chat_body("debug this: key=<removed>"))
    assert r.status_code == 200
    assert r.json()["metadata"]["privacy"]["decision"] == "allow"
    assert provider.calls == 1


def test_edit_that_still_contains_a_secret_is_still_blocked(client, provider):
    r = client.post("/api/chat", json=chat_body(f"debug this (edited): {SECRET}"))
    assert r.status_code == 403
    assert provider.calls == 0


def test_redacted_prompt_flows_through_to_provider(client, provider):
    redacted = client.post("/api/privacy/redact", json={"text": f"key {SECRET} mail {EMAIL}"}).json()["redacted_text"]
    r = client.post("/api/chat", json=chat_body(redacted))
    assert r.status_code == 200
    assert provider.calls == 1


def test_clean_prompt_keeps_normal_response_shape(client):
    data = client.post("/api/chat", json=chat_body("Explain recursion")).json()
    assert data["message"]["content"] == "Hello!"
    assert data["metadata"]["usage"]["total_tokens"] == 5
    assert data["metadata"]["privacy"]["decision"] == "allow"
    assert data["metadata"]["request_id"]


# ---- failure handling and logging ------------------------------------------------------------

def test_privacy_engine_failure_fails_closed(client, provider, monkeypatch):
    def boom(text):
        raise RuntimeError(f"engine exploded on {text}")

    monkeypatch.setattr(client.app.state.privacy.detector, "detect", boom)
    r = client.post("/api/chat", json=chat_body("totally harmless"))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "privacy_engine_unavailable"
    assert provider.calls == 0
    assert "harmless" not in r.text


def test_logs_never_contain_sensitive_values(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/api/privacy/scan", json={"text": f"key {SECRET} mail {EMAIL}"})
    client.post("/api/privacy/redact", json={"text": f"key {SECRET} mail {EMAIL}"})
    client.post("/api/chat", json=chat_body(f"key {SECRET}"))
    client.post("/api/chat", json=chat_body(f"mail {EMAIL}"))
    log = caplog.text
    assert "privacy" in log  # something was logged...
    assert SECRET not in log and EMAIL not in log and EMAIL_LOCAL not in log  # ...but never the values


def test_failure_log_does_not_leak_prompt(client, caplog, monkeypatch):
    def boom(text):
        raise RuntimeError(f"failed on {text}")

    monkeypatch.setattr(client.app.state.privacy.detector, "detect", boom)
    caplog.set_level(logging.DEBUG)
    client.post("/api/chat", json=chat_body(f"secret thing {SECRET}"))
    assert SECRET not in caplog.text and "secret thing" not in caplog.text


def test_validation_error_does_not_echo_secret(client):
    r = client.post("/api/chat", json={"messages": [{"role": "bogus", "content": SECRET}]})
    assert r.status_code == 422
    assert SECRET not in r.text


def test_health_reports_privacy_engine(client):
    data = client.get("/health").json()
    assert data["privacy"]["engine"] == "presidio"
    assert isinstance(data["privacy"]["ready"], bool)


def test_debug_log_level_does_not_leak_prompts_via_presidio(client, caplog):
    # Presidio logs the analysed text at DEBUG; the app must keep that logger quiet even when the root is DEBUG.
    caplog.set_level(logging.DEBUG)
    client.post("/api/privacy/scan", json={"text": f"please email {EMAIL} about project Falcon"})
    assert EMAIL not in caplog.text and EMAIL_LOCAL not in caplog.text and "Falcon" not in caplog.text
