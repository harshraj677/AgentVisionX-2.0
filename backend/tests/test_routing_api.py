"""Routing through the real HTTP API with scripted providers: metadata, privacy-first ordering, fallback, leaks."""
import logging
import socket
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.main import create_app
from app.providers.gemini import GeminiProvider
from tests.helpers import PRICES, ScriptedProvider, make_settings, timeout

KEY = "sk-proj-" + "Zq9Xw8Vr" * 4
API_KEY_VALUE = "AIzaSyFAKE-backend-key-0123456789abcdef"
BODY = {"messages": [{"role": "user", "content": "Explain recursion"}]}


def chat_body(text: str, **extra) -> dict:
    return {"messages": [{"role": "user", "content": text}], **extra}


def build(providers: dict, **settings_kw) -> TestClient:
    app = create_app(make_settings(**settings_kw))
    app.state.registry._providers = dict(providers)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def gemini():
    return ScriptedProvider("gemini", models=("flash", "pro", "lite"), default="flash")


# ---- metadata and backward compatibility ---------------------------------------------------------------

def test_gemini_only_default_flow_and_metadata(gemini):
    r = build({"gemini": gemini}).post("/api/chat", json=BODY)
    assert r.status_code == 200
    data = r.json()
    meta = data["metadata"]
    assert data["message"] == {"role": "assistant", "content": "reply from gemini/flash"}
    # existing fields are unchanged
    assert meta["provider"] == "gemini" and meta["model"] == "flash" and meta["status"] == "success"
    assert meta["usage"] == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "source": "provider"}
    assert meta["privacy"]["decision"] == "allow" and meta["prompt_source"] is None
    assert meta["request_id"] == r.headers["x-request-id"] and isinstance(meta["latency_ms"], int)
    # new routing block
    routing = meta["routing"]
    assert routing["task_type"] == "unknown" and routing["task_confidence"] == "low"
    assert routing["routing_policy"] == "balanced"
    assert (routing["selected_provider"], routing["selected_model"]) == ("gemini", "flash")
    assert routing["fallback_used"] is False and routing["fallback_reason"] is None
    assert routing["attempts"] == [{"provider": "gemini", "model": "flash", "outcome": "success"}]
    assert "default flash" in routing["routing_reason"]


def test_old_style_request_with_explicit_provider_still_works(gemini):
    r = build({"gemini": gemini}).post("/api/chat", json={**BODY, "provider": "gemini"})
    assert r.status_code == 200 and r.json()["metadata"]["provider"] == "gemini"


def test_task_mapping_is_applied_and_reported(gemini):
    client = build({"gemini": gemini}, router_task_models={"coding": "pro"})
    r = client.post("/api/chat", json=chat_body("Fix this bug:\n```py\nx=1\n```", routing_policy="quality"))
    routing = r.json()["metadata"]["routing"]
    assert routing["task_type"] == "coding" and routing["selected_model"] == "pro"
    assert routing["routing_policy"] == "quality" and "mapped to pro" in routing["routing_reason"]
    assert gemini.calls == ["pro"]


def test_per_request_cost_policy_uses_configured_prices(gemini):
    client = build({"gemini": gemini}, model_profiles=PRICES)
    routing = client.post("/api/chat", json={**BODY, "routing_policy": "cost"}).json()["metadata"]["routing"]
    assert routing["selected_model"] == "lite" and "lowest configured price" in routing["routing_reason"]


def test_cost_policy_without_prices_says_so_and_uses_default(gemini):
    routing = build({"gemini": gemini}).post("/api/chat", json={**BODY, "routing_policy": "cost"}).json()["metadata"]["routing"]
    assert routing["selected_model"] == "flash" and "price not configured" in routing["routing_reason"]


# ---- manual selection -------------------------------------------------------------------------------------

def test_manual_model_valid_and_invalid(gemini):
    client = build({"gemini": gemini})
    ok = client.post("/api/chat", json={**BODY, "model": "pro"})
    assert ok.status_code == 200 and ok.json()["metadata"]["routing"]["routing_policy"] == "manual"
    assert gemini.calls == ["pro"]

    bad = client.post("/api/chat", json={**BODY, "model": "gpt-9"})
    err = bad.json()["error"]
    assert bad.status_code == 422 and err["code"] == "model_not_eligible"
    assert err["details"]["eligible_models"] == ["gemini/flash", "gemini/pro", "gemini/lite"]
    assert err["request_id"] == bad.headers["x-request-id"]
    assert gemini.calls == ["pro"]  # the invalid request reached no provider


def test_manual_policy_without_model_and_conflicting_options(gemini):
    client = build({"gemini": gemini})
    assert client.post("/api/chat", json={**BODY, "routing_policy": "manual"}).json()["error"]["code"] == "model_required"
    r = client.post("/api/chat", json={**BODY, "routing_policy": "cost", "model": "pro"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "conflicting_routing"
    assert gemini.calls == []


def test_invalid_policy_value_is_rejected(gemini):
    assert build({"gemini": gemini}).post("/api/chat", json={**BODY, "routing_policy": "cheapest"}).status_code == 422


# ---- missing / ineligible providers -----------------------------------------------------------------------

def test_missing_credentials_is_clean_error(gemini):
    gem_off = ScriptedProvider("gemini", configured=False)
    r = build({"gemini": gem_off}).post("/api/chat", json=BODY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider_not_configured"
    assert "GEMINI_API_KEY" in r.json()["error"]["message"] and gem_off.calls == []


def test_explicit_openai_without_credentials_is_not_silently_replaced_by_gemini(gemini):
    openai = ScriptedProvider("openai", configured=False)
    r = build({"gemini": gemini, "openai": openai}).post("/api/chat", json={**BODY, "provider": "openai"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider_not_configured"
    assert gemini.calls == [] and openai.calls == []


def test_unimplemented_openai_is_never_chosen_automatically(gemini):
    stub = ScriptedProvider("openai", models=("gpt-x",), generates=False)
    client = build({"gemini": gemini, "openai": stub}, router_default_policy="cost")
    assert client.post("/api/chat", json=BODY).status_code == 200 and stub.calls == []
    r = client.post("/api/chat", json={**BODY, "provider": "openai"})
    assert r.status_code == 501 and r.json()["error"]["code"] == "provider_not_implemented"


def test_all_models_cooling_down_gives_actionable_503(gemini):
    client = build({"gemini": ScriptedProvider("gemini", models=("flash",))})
    client.app.state.router.tracker.record_failure(("gemini", "flash"), "provider_auth_error")
    r = client.post("/api/chat", json=BODY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "no_eligible_provider"


# ---- fallback over HTTP -----------------------------------------------------------------------------------

def test_fallback_is_reported_in_metadata():
    prov = ScriptedProvider("gemini", models=("pro", "flash"), default="pro", script={"pro": [timeout()]})
    client = build({"gemini": prov}, model_profiles=PRICES)
    meta = client.post("/api/chat", json=BODY).json()["metadata"]
    routing = meta["routing"]
    assert meta["model"] == "flash" and routing["selected_model"] == "flash"
    assert routing["fallback_used"] is True and routing["fallback_reason"] == "gemini/pro failed (provider_timeout)"
    assert [a["outcome"] for a in routing["attempts"]] == ["provider_timeout", "success"]
    assert prov.calls == ["pro", "flash"]


def test_failure_after_permitted_attempts_returns_provider_error_with_attempts():
    prov = ScriptedProvider("gemini", models=("flash",), script={"flash": [timeout()]})
    r = build({"gemini": prov}).post("/api/chat", json=BODY)
    err = r.json()["error"]
    assert r.status_code == 504 and err["code"] == "provider_timeout" and err["request_id"] == r.headers["x-request-id"]
    assert err["details"]["routing"]["attempts"] == [{"provider": "gemini", "model": "flash", "outcome": "provider_timeout"}]
    assert prov.calls == ["flash"]  # one attempt: no blind retry


def test_no_fallback_when_disabled():
    prov = ScriptedProvider("gemini", models=("pro", "flash"), default="pro", script={"pro": [timeout()]})
    client = build({"gemini": prov}, model_profiles=PRICES, router_fallback="off")
    assert client.post("/api/chat", json=BODY).status_code == 504 and prov.calls == ["pro"]


# ---- privacy comes first -----------------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["original", "optimized", "edited"])
def test_blocked_prompt_never_reaches_any_provider_whatever_its_source(source):
    a = ScriptedProvider("gemini", models=("flash", "pro"))
    b = ScriptedProvider("openai", models=("gpt-x",))
    client = build({"gemini": a, "openai": b}, router_fallback="any_eligible")
    r = client.post("/api/chat", json=chat_body(f"debug key={KEY}", prompt_source=source))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy_blocked"
    assert a.calls == [] and b.calls == []
    assert KEY not in r.text


def test_edited_prompt_is_rescanned_then_routed(gemini):
    client = build({"gemini": gemini})
    assert client.post("/api/chat", json=chat_body(f"optimized text with {KEY}", prompt_source="optimized")).status_code == 403
    assert gemini.calls == []
    ok = client.post("/api/chat", json=chat_body("edited text without secrets", prompt_source="edited"))
    assert ok.status_code == 200 and ok.json()["metadata"]["prompt_source"] == "edited" and gemini.calls == ["flash"]


def test_confirmed_personal_data_is_only_routed_to_sensitive_providers():
    a = ScriptedProvider("gemini", models=("flash",))
    b = ScriptedProvider("openai", models=("gpt-x",))
    client = build({"gemini": a, "openai": b}, router_allowed_providers="openai,gemini", router_sensitive_providers="gemini")
    plain = client.post("/api/chat", json=chat_body("Explain recursion")).json()["metadata"]["routing"]
    assert plain["selected_provider"] == "openai"
    personal = client.post("/api/chat", json=chat_body("Help John Smith write a cover letter", privacy_ack=True))
    assert personal.status_code == 200 and personal.json()["metadata"]["routing"]["selected_provider"] == "gemini"


def test_privacy_error_responses_have_no_routing_block(gemini):
    err = build({"gemini": gemini}).post("/api/chat", json=chat_body(f"key {KEY}")).json()["error"]
    assert "routing" not in str(err)


# ---- no leaks, no network ------------------------------------------------------------------------------------

def test_credentials_and_prompts_never_appear_in_logs_or_error_bodies(caplog):
    # Real Gemini adapter with a fake SDK client that fails, so the real error-handling path runs.
    settings = make_settings(gemini_api_key=API_KEY_VALUE)
    provider = GeminiProvider(settings)

    async def boom(model, contents):
        raise errors.ClientError(403, {"error": {"message": f"API key {API_KEY_VALUE} rejected", "status": "PERMISSION_DENIED"}})

    provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=boom)))
    app = create_app(settings)
    app.state.registry._providers["gemini"] = provider
    client = TestClient(app, raise_server_exceptions=False)

    caplog.set_level(logging.DEBUG)
    secret_prompt = "Please plan the confidential Falcon merger timeline"
    responses = [client.post("/api/chat", json=chat_body(secret_prompt)), client.get("/api/router/models"), client.get("/health")]
    blob = caplog.text + "".join(r.text for r in responses)
    for leaked in (API_KEY_VALUE, "Falcon", "confidential"):
        assert leaked not in blob
    assert responses[0].json()["error"]["code"] == "provider_auth_error"
    assert "router" in caplog.text  # routing was logged, just without content


def test_routing_makes_no_network_calls(gemini, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    client = build({"gemini": gemini}, model_profiles=PRICES)
    for policy in ("balanced", "cost", "quality", "latency"):
        assert client.post("/api/chat", json={**BODY, "routing_policy": policy}).status_code == 200
    assert client.get("/api/router/models").status_code == 200


# ---- router/models endpoint ------------------------------------------------------------------------------------

def test_models_endpoint_lists_only_what_is_known():
    prov = ScriptedProvider("gemini", models=("flash", "pro"), default="flash")
    openai = ScriptedProvider("openai", configured=False)
    client = build({"gemini": prov, "openai": openai}, model_profiles={"flash": PRICES["flash"]}, router_default_policy="quality")
    data = client.get("/api/router/models").json()
    assert data["default_policy"] == "quality" and data["default_model"] == "flash"
    assert data["policies"] == ["balanced", "cost", "quality", "latency", "manual"]
    gemini_row = next(p for p in data["providers"] if p["name"] == "gemini")
    flash, pro = gemini_row["models"]
    assert flash["input_cost_per_mtok"] == 1.0 and pro["input_cost_per_mtok"] is None
    assert flash["measured_latency_ms"] is None and flash["available"] is True
    openai_row = next(p for p in data["providers"] if p["name"] == "openai")
    assert openai_row["configured"] is False and openai_row["models"] == []
    # measurements appear only after real calls
    for _ in range(3):
        client.post("/api/chat", json=BODY)
    flash_after = client.get("/api/router/models").json()["providers"][0]["models"][0]
    assert flash_after["latency_samples"] == 3 and flash_after["measured_latency_ms"] is not None


def test_health_contract_is_unchanged(gemini):
    data = build({"gemini": gemini}).get("/health").json()
    assert {"status", "version", "providers", "privacy"} <= set(data)


# ---- the real Gemini adapter feeds the router --------------------------------------------------------------------

def test_real_gemini_adapter_lists_configured_models_and_is_routed():
    settings = make_settings(gemini_api_key="k", gemini_model="m-default", gemini_models="m-extra, m-default")
    provider = GeminiProvider(settings)
    seen = []

    async def generate_content(model, contents):
        seen.append(model)
        return SimpleNamespace(text="hello", usage_metadata=SimpleNamespace(prompt_token_count=4, candidates_token_count=2, total_token_count=6))

    provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    assert provider.list_models() == ["m-default", "m-extra"]
    app = create_app(settings)
    app.state.registry._providers["gemini"] = provider
    client = TestClient(app, raise_server_exceptions=False)
    meta = client.post("/api/chat", json=BODY).json()["metadata"]
    assert seen == ["m-default"] and meta["model"] == "m-default" and meta["usage"]["total_tokens"] == 6
    assert client.post("/api/chat", json={**BODY, "model": "m-extra"}).json()["metadata"]["model"] == "m-extra"
    assert client.post("/api/chat", json={**BODY, "model": "other"}).status_code == 422
    assert seen == ["m-default", "m-extra"]
