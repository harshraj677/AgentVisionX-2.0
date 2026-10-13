"""The response gate through the real HTTP API: what the client receives when the provider's reply is sensitive."""
import logging
import socket

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.privacy_service import PrivacyService
from app.services.response_privacy_service import ResponsePrivacyService
from tests.helpers import PRICES, ScriptedProvider, make_settings, reply, timeout

SECRET = "sk-proj-" + "Zq9Xw8Vr" * 4
EMAIL = "priya.sharma@example.com"
EMAIL_LOCAL = "priya.sharma"
UNIQUE = "ConfidentialFalconMerger"
BODY = {"messages": [{"role": "user", "content": "Explain recursion"}]}


def provider_replying(*texts: str, models=("flash",), default=None, script=None) -> ScriptedProvider:
    scripted = {"flash": [reply(t) for t in texts]} if script is None else script
    return ScriptedProvider("gemini", models=models, default=default, script=scripted)


def build(provider, **settings_kw) -> TestClient:
    app = create_app(make_settings(**settings_kw))
    app.state.registry._providers = {"gemini": provider}
    return TestClient(app, raise_server_exceptions=False)


# ---- default policy: redact secrets, warn on contact data ---------------------------------------------------

def test_secret_in_reply_is_redacted_before_it_reaches_the_client():
    provider = provider_replying(f"Use this key: {SECRET} in your .env")
    r = build(provider).post("/api/chat", json={**BODY, "prompt_source": "optimized"})
    assert r.status_code == 200
    assert SECRET not in r.text  # nowhere in the HTTP response
    data = r.json()
    assert data["message"] == {"role": "assistant", "content": "Use this key: [API_KEY] in your .env"}
    assert data["metadata"]["response_privacy"] == {
        "status": "redacted", "policy": "policy", "findings_count": 1,
        "categories": {"API_KEY": 1}, "redacted": True, "redacted_spans": 1,
    }
    assert provider.calls == ["flash"]  # scanning made no extra provider call


def test_existing_metadata_is_preserved_alongside_response_privacy():
    r = build(provider_replying(f"key {SECRET}")).post("/api/chat", json={**BODY, "prompt_source": "edited"})
    meta = r.json()["metadata"]
    assert meta["request_id"] == r.headers["x-request-id"]
    assert (meta["provider"], meta["model"], meta["status"], meta["prompt_source"]) == ("gemini", "flash", "success", "edited")
    assert meta["usage"] == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "source": "provider"}
    assert meta["privacy"]["decision"] == "allow" and isinstance(meta["latency_ms"], int)
    assert meta["routing"]["selected_model"] == "flash" and meta["routing"]["fallback_used"] is False
    assert meta["routing"]["attempts"] == [{"provider": "gemini", "model": "flash", "outcome": "success"}]


def test_contact_data_is_returned_with_a_warning_status():
    provider = provider_replying(f"Write to {EMAIL} for help.")
    data = build(provider).post("/api/chat", json=BODY).json()
    assert EMAIL in data["message"]["content"]  # warn does not alter the text
    assert data["metadata"]["response_privacy"]["status"] == "warned"
    assert data["metadata"]["response_privacy"]["categories"] == {"EMAIL_ADDRESS": 1}
    assert data["metadata"]["response_privacy"]["redacted"] is False


def test_clean_reply_is_reported_clean():
    data = build(provider_replying("Recursion is a function calling itself.")).post("/api/chat", json=BODY).json()
    assert data["message"]["content"] == "Recursion is a function calling itself."
    assert data["metadata"]["response_privacy"] == {
        "status": "clean", "policy": "policy", "findings_count": 0, "categories": {}, "redacted": False, "redacted_spans": 0,
    }


def test_whitespace_only_reply_is_handled():
    r = build(provider_replying("   ")).post("/api/chat", json=BODY)
    assert r.status_code == 200 and r.json()["metadata"]["response_privacy"]["status"] == "clean"


def test_multiple_spans_and_unicode_through_the_api():
    text = f"😀 {SECRET} 日本語 card 4111 1111 1111 1111 and {SECRET}\nline two"
    data = build(provider_replying(text)).post("/api/chat", json=BODY).json()
    assert data["message"]["content"] == "😀 [API_KEY] 日本語 card [CREDIT_CARD] and [API_KEY]\nline two"
    assert data["metadata"]["response_privacy"]["redacted_spans"] == 3


# ---- configurable modes ------------------------------------------------------------------------------------

def test_allow_mode_returns_original_and_records_the_outcome():
    data = build(provider_replying(f"key {SECRET}"), response_privacy_mode="allow").post("/api/chat", json=BODY).json()
    assert SECRET in data["message"]["content"]  # explicit operator choice
    assert data["metadata"]["response_privacy"]["status"] == "allowed"
    assert data["metadata"]["response_privacy"]["policy"] == "allow"


def test_uniform_redact_mode_redacts_contact_data_too():
    data = build(provider_replying(f"mail {EMAIL}"), response_privacy_mode="redact").post("/api/chat", json=BODY).json()
    assert data["message"]["content"] == "mail [EMAIL_ADDRESS]" and EMAIL not in str(data)


def test_per_category_override_changes_only_that_category():
    client = build(provider_replying(f"mail {EMAIL}", f"key {SECRET}"), response_privacy_policy={"EMAIL_ADDRESS": "redact"})
    assert client.post("/api/chat", json=BODY).json()["message"]["content"] == "mail [EMAIL_ADDRESS]"
    assert client.post("/api/chat", json=BODY).json()["message"]["content"] == "key [API_KEY]"


# ---- block mode --------------------------------------------------------------------------------------------

def test_block_mode_never_returns_the_original_reply():
    provider = provider_replying(f"{UNIQUE}: write to {EMAIL} or use {SECRET}")
    r = build(provider, response_privacy_mode="block").post("/api/chat", json=BODY)
    assert r.status_code == 403
    for leaked in (EMAIL, EMAIL_LOCAL, SECRET, UNIQUE):
        assert leaked not in r.text
    body = r.json()
    assert set(body) == {"error"}  # no `message` carrying a reply, real or synthetic
    err = body["error"]
    assert err["code"] == "response_privacy_blocked" and err["request_id"] == r.headers["x-request-id"]
    assert err["details"]["response_privacy"] == {
        "status": "blocked", "policy": "block", "findings_count": 2,
        "categories": {"EMAIL_ADDRESS": 1, "API_KEY": 1}, "redacted": False, "redacted_spans": 0,
    }
    assert provider.calls == ["flash"]  # one call: no second request to "get a better reply"


def test_category_level_block_applies_under_the_default_policy():
    client = build(provider_replying(f"key {SECRET}", f"mail {EMAIL}"), response_privacy_policy={"API_KEY": "block"})
    assert client.post("/api/chat", json=BODY).status_code == 403
    assert client.post("/api/chat", json=BODY).status_code == 200  # email only warns


def test_block_error_keeps_routing_context_without_content():
    provider = ScriptedProvider("gemini", models=("flash",), script={"flash": [reply(f"{SECRET}")]})
    err = build(provider, response_privacy_mode="block", model_profiles=PRICES).post("/api/chat", json=BODY).json()["error"]
    routing = err["details"]["routing"]
    assert routing["selected_model"] == "flash" and routing["routing_policy"] == "balanced"
    assert routing["attempts"] == [{"provider": "gemini", "model": "flash", "outcome": "success"}]


def test_a_blocked_reply_does_not_mark_the_model_unhealthy():
    provider = provider_replying(f"{SECRET}", "fine now")
    client = build(provider, response_privacy_policy={"API_KEY": "block"})
    assert client.post("/api/chat", json=BODY).status_code == 403
    assert client.app.state.router.tracker.is_available(("gemini", "flash"))
    assert client.post("/api/chat", json=BODY).json()["message"]["content"] == "fine now"


# ---- provider errors stay distinguishable --------------------------------------------------------------------

def test_provider_failure_is_not_reported_as_a_privacy_block():
    provider = ScriptedProvider("gemini", models=("flash",), script={"flash": [timeout()]})
    err = build(provider, response_privacy_mode="block").post("/api/chat", json=BODY).json()["error"]
    assert err["code"] == "provider_timeout" and not err["code"].startswith("response_privacy")


# ---- routing and fallback interplay --------------------------------------------------------------------------------

def test_reply_from_fallback_provider_is_scanned_and_routing_metadata_kept():
    provider = ScriptedProvider(
        "gemini", models=("pro", "flash"), default="pro",
        script={"pro": [timeout()], "flash": [reply(f"key {SECRET}", "flash")]},
    )
    data = build(provider, model_profiles=PRICES).post("/api/chat", json=BODY).json()
    assert provider.calls == ["pro", "flash"]  # exactly the permitted fallback; scanning added no call
    routing = data["metadata"]["routing"]
    assert routing["fallback_used"] is True and routing["selected_model"] == "flash"
    assert routing["fallback_reason"] == "gemini/pro failed (provider_timeout)"
    assert data["message"]["content"] == "key [API_KEY]" and data["metadata"]["response_privacy"]["status"] == "redacted"


def test_blocked_reply_does_not_change_the_selected_provider_or_trigger_fallback():
    provider = ScriptedProvider(
        "gemini", models=("pro", "flash"), default="pro", script={"pro": [reply(f"{SECRET}")], "flash": [reply("alternative")]},
    )
    r = build(provider, model_profiles=PRICES, response_privacy_policy={"API_KEY": "block"}).post("/api/chat", json=BODY)
    assert r.status_code == 403 and provider.calls == ["pro"]  # flash was never asked


# ---- failure handling (fail closed) ----------------------------------------------------------------------------------

def test_scanner_crash_withholds_the_reply():
    provider = provider_replying(f"{UNIQUE} {SECRET}")
    client = build(provider)
    detector = client.app.state.privacy.detector
    real, calls = detector.detect, {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] > 1:  # first call is the INPUT scan; the second is the response scan
            raise RuntimeError(f"engine failed on {text}")
        return real(text)

    detector.detect = flaky
    try:
        r = client.post("/api/chat", json=BODY)
    finally:
        detector.detect = real
    assert r.status_code == 503 and r.json()["error"]["code"] == "response_privacy_unavailable"
    assert SECRET not in r.text and UNIQUE not in r.text and set(r.json()) == {"error"}
    assert provider.calls == ["flash"]


def test_failed_redaction_verification_withholds_the_reply(monkeypatch):
    provider = provider_replying(f"key {SECRET}")
    client = build(provider)
    monkeypatch.setattr(PrivacyService, "apply_redaction", staticmethod(lambda text, findings: text))  # redaction does nothing
    r = client.post("/api/chat", json=BODY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "response_privacy_redaction_failed"
    assert SECRET not in r.text


def test_oversized_reply_is_withheld_not_passed_unscanned():
    provider = provider_replying("word " * 400)
    r = build(provider, response_privacy_max_chars=1000).post("/api/chat", json=BODY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "response_privacy_unavailable"


# ---- ordering, input behaviour, logs, network -----------------------------------------------------------------------

def test_scan_runs_after_the_provider_and_before_the_http_response(monkeypatch):
    order = []
    provider = provider_replying(f"key {SECRET}")
    original_generate = provider.generate

    async def traced_generate(messages, model):
        order.append("provider")
        return await original_generate(messages, model)

    provider.generate = traced_generate
    client = build(provider)
    real_process = client.app.state.response_privacy.process

    async def traced_process(text, request_id):
        order.append(("scan_input_contains_secret", SECRET in text))
        return await real_process(text, request_id)

    monkeypatch.setattr(client.app.state.response_privacy, "process", traced_process)
    r = client.post("/api/chat", json=BODY)
    assert order == ["provider", ("scan_input_contains_secret", True)]  # the scan saw the raw reply, then it was redacted
    assert SECRET not in r.text


def test_input_privacy_is_unchanged_and_runs_before_any_response_logic(monkeypatch):
    provider = provider_replying("never used")
    client = build(provider)

    async def must_not_run(*a, **k):
        raise AssertionError("response scanner invoked for a request that input privacy blocks")

    monkeypatch.setattr(client.app.state.response_privacy, "process", must_not_run)
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": f"my key {SECRET}"}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy_blocked" and provider.calls == []


def test_input_policy_setting_does_not_affect_response_policy():
    # The input policy would block e-mail addresses in prompts; the reply policy still only warns about them.
    client = build(provider_replying(f"write to {EMAIL}"), privacy_policy={"EMAIL_ADDRESS": "block"})
    assert client.post("/api/chat", json=BODY).json()["metadata"]["response_privacy"]["status"] == "warned"


@pytest.mark.parametrize(
    "settings_kw, reply_text",
    [
        ({}, f"{UNIQUE} {SECRET} {EMAIL}"),
        ({"response_privacy_mode": "block"}, f"{UNIQUE} {SECRET} {EMAIL}"),
        ({"response_privacy_mode": "allow"}, f"{UNIQUE} {SECRET} {EMAIL}"),
    ],
)
def test_debug_logs_never_contain_reply_text_or_matches(caplog, settings_kw, reply_text):
    caplog.set_level(logging.DEBUG)
    build(provider_replying(reply_text), **settings_kw).post("/api/chat", json=BODY)
    for leaked in (SECRET, EMAIL, EMAIL_LOCAL, UNIQUE):
        assert leaked not in caplog.text
    assert "response privacy" in caplog.text  # the outcome is logged, content is not


def test_debug_logs_stay_clean_when_the_scanner_fails(caplog):
    client = build(provider_replying(f"{UNIQUE} {SECRET}"))
    detector = client.app.state.privacy.detector
    real, calls = detector.detect, {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError(f"boom {text}")
        return real(text)

    detector.detect = flaky
    caplog.set_level(logging.DEBUG)
    try:
        client.post("/api/chat", json=BODY)
    finally:
        detector.detect = real
    assert SECRET not in caplog.text and UNIQUE not in caplog.text


def test_response_scanning_makes_no_network_calls(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    r = build(provider_replying(f"key {SECRET}")).post("/api/chat", json=BODY)
    assert r.status_code == 200 and r.json()["metadata"]["response_privacy"]["status"] == "redacted"


def test_health_reports_the_active_response_policy_read_only():
    data = build(provider_replying("x"), response_privacy_mode="redact").get("/health").json()
    assert data["privacy"]["response_policy"] == "redact" and data["privacy"]["engine"] == "presidio"
    assert {"status", "version", "providers", "privacy"} <= set(data)


def test_service_objects_are_wired_with_separate_policies():
    client = build(provider_replying("x"), privacy_policy={"EMAIL_ADDRESS": "allow"})
    assert isinstance(client.app.state.response_privacy, ResponsePrivacyService)
    assert client.app.state.privacy.policy.action_for("EMAIL_ADDRESS") == "allow"
    assert client.app.state.response_privacy.policy.action_for("EMAIL_ADDRESS") == "warn"
