"""Optimizer API + the guarantees around it: privacy re-check of the selected prompt, no provider calls, no leaks."""
import logging
import socket

import pytest

from tests.conftest import OK_RESULT, FakeProvider

KEY = "sk-proj-" + "Zq9Xw8Vr" * 4
VERBOSE = "Hello, could you please explain in order to understand how HTTPS works?   Thanks in advance!"


class FakeCounter:
    method = "fake:words"
    note = "Test counter."

    def count(self, text: str) -> int:
        return len(text.split())


def chat_body(text: str, **extra) -> dict:
    return {"messages": [{"role": "user", "content": text}], **extra}


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(OK_RESULT)


@pytest.fixture
def client(make_client, provider):
    c = make_client(provider)
    c.app.state.optimizer._counter = FakeCounter()  # no tokenizer download in tests
    return c


def optimize(client, text):
    return client.post("/api/optimize", json={"text": text})


# ---- contract ------------------------------------------------------------------------------

def test_optimize_response_shape_and_labelled_estimates(client):
    r = optimize(client, VERBOSE)
    assert r.status_code == 200
    data = r.json()
    assert data["changed"] is True
    assert data["optimized_text"] == "Explain to understand how HTTPS works?"
    assert {c["rule"] for c in data["changes"]} >= {"remove_greeting", "remove_polite_padding"}
    assert data["preservation"]["verified"] is True
    t = data["tokens"]
    assert t["basis"] == "estimate" and t["method"] == "fake:words" and t["note"]
    assert t["original"] == 15 and t["optimized"] == 6
    assert t["saved"] == t["original"] - t["optimized"]
    assert t["saved_percent"] == round(100 * 9 / 15, 1)
    assert data["privacy"]["original"]["decision"] == "allow"
    assert r.headers["x-request-id"]


def test_unchanged_prompt_reports_zero_savings(client):
    data = optimize(client, "Summarize this text in 3 bullet points.").json()
    assert data["changed"] is False and data["changes"] == []
    assert data["optimized_text"] == "Summarize this text in 3 bullet points."
    assert data["tokens"]["saved"] == 0 and data["tokens"]["saved_percent"] == 0.0


def test_default_counter_is_labelled_whatever_it_is(make_client):
    c = make_client(FakeProvider(OK_RESULT))  # real counter: tiktoken, or heuristic fallback if offline
    t = optimize(c, VERBOSE).json()["tokens"]
    assert t["basis"] == "estimate" and (t["method"].startswith("tiktoken:") or t["method"].startswith("heuristic:"))
    assert t["optimized"] < t["original"]


def test_rejects_oversized_and_empty_input(make_client):
    c = make_client(max_message_chars=20)
    assert optimize(c, "x" * 21).json()["error"]["code"] == "message_too_long"
    assert optimize(c, "").status_code == 422


# ---- optimizing never reaches a provider or the network -------------------------------------------

def test_optimize_never_calls_provider_or_network(client, provider, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network access attempted during optimization")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for text in (VERBOSE, f"please use {KEY}", "plain prompt"):
        assert optimize(client, text).status_code == 200
    assert provider.calls == 0


def test_cancelling_after_comparison_sends_nothing(client, provider):
    optimize(client, VERBOSE)  # user opens the comparison screen...
    # ...and cancels: the app makes no /api/chat call. Nothing may have reached the provider.
    assert provider.calls == 0


# ---- privacy policy still governs the selected prompt ---------------------------------------------

def test_both_candidates_are_scanned(client):
    data = optimize(client, f"Hey, please debug this key={KEY}").json()
    assert data["privacy"]["original"]["decision"] == "block"
    assert data["privacy"]["optimized"]["decision"] == "block"
    assert KEY not in str(data["privacy"])  # scans carry offsets, not values


def test_optimized_prompt_with_secret_is_blocked_at_chat(client, provider):
    optimized = optimize(client, f"Hey, please debug this key={KEY}").json()["optimized_text"]
    assert KEY in optimized  # the optimizer must not silently strip or alter it
    r = client.post("/api/chat", json=chat_body(optimized, prompt_source="optimized"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy_blocked"
    assert provider.calls == 0


def test_optimized_prompt_needing_redaction_is_refused(client, provider):
    optimized = optimize(client, "Hi, please email jane.doe@example.com the report").json()["optimized_text"]
    r = client.post("/api/chat", json=chat_body(optimized, prompt_source="optimized"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy_redaction_required"
    assert provider.calls == 0


def test_warn_still_needs_confirmation_for_optimized_prompt(client, provider):
    optimized = optimize(client, "Hello, please help John Smith write a cover letter").json()["optimized_text"]
    assert client.post("/api/chat", json=chat_body(optimized, prompt_source="optimized")).status_code == 409
    assert provider.calls == 0
    ok = client.post("/api/chat", json=chat_body(optimized, prompt_source="optimized", privacy_ack=True))
    assert ok.status_code == 200 and provider.calls == 1


# ---- the three choices: original / optimized / edited ----------------------------------------------

def test_original_selected_is_sent_unchanged(client, provider):
    r = client.post("/api/chat", json=chat_body(VERBOSE, prompt_source="original"))
    assert r.status_code == 200
    assert provider.last_messages[-1].content == VERBOSE
    assert r.json()["metadata"]["prompt_source"] == "original"


def test_optimized_selected_is_sent_exactly(client, provider):
    optimized = optimize(client, VERBOSE).json()["optimized_text"]
    r = client.post("/api/chat", json=chat_body(optimized, prompt_source="optimized"))
    assert r.status_code == 200
    assert provider.last_messages[-1].content == optimized
    assert r.json()["metadata"]["prompt_source"] == "optimized"
    assert r.json()["metadata"]["usage"]["source"] == "provider"  # real usage still comes from the provider


def test_manually_edited_version_is_scanned_like_any_other(client, provider):
    optimized = optimize(client, VERBOSE).json()["optimized_text"]
    bad_edit = optimized + f" My key is {KEY}"
    assert client.post("/api/chat", json=chat_body(bad_edit, prompt_source="edited")).status_code == 403
    assert provider.calls == 0
    good_edit = optimized + " Use simple words."
    r = client.post("/api/chat", json=chat_body(good_edit, prompt_source="edited"))
    assert r.status_code == 200 and r.json()["metadata"]["prompt_source"] == "edited"
    assert provider.last_messages[-1].content == good_edit
    assert provider.calls == 1


def test_prompt_source_is_optional_and_validated(client):
    assert client.post("/api/chat", json=chat_body("hi")).json()["metadata"]["prompt_source"] is None
    assert client.post("/api/chat", json=chat_body("hi", prompt_source="magic")).status_code == 422


def test_existing_chat_behaviour_is_unchanged(client):
    r = client.post("/api/chat", json=chat_body("Explain recursion"))
    data = r.json()
    assert data["message"]["content"] == "Hello!"
    assert data["metadata"]["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5, "source": "provider"}
    assert data["metadata"]["request_id"] == r.headers["x-request-id"]


# ---- no leaks ---------------------------------------------------------------------------------------

def test_optimizer_logs_contain_no_prompt_text(client, caplog):
    caplog.set_level(logging.DEBUG)
    text = f"Hello, please email jane.doe@example.com about project Falcon, key {KEY}. Thanks!"
    optimize(client, text)
    log = caplog.text
    assert "optimize" in log
    for leaked in (KEY, "jane.doe", "Falcon", "project"):
        assert leaked not in log


def test_optimizer_failure_is_a_clean_error_without_prompt(client, caplog, monkeypatch):
    def boom(text):
        raise RuntimeError(f"failed on {text}")

    monkeypatch.setattr(client.app.state.optimizer._optimizer, "optimize", boom)
    caplog.set_level(logging.DEBUG)
    r = optimize(client, "super secret project Falcon")
    assert r.status_code == 500 and r.json()["error"]["code"] == "optimizer_error"
    assert "Falcon" not in r.text and "Falcon" not in caplog.text
