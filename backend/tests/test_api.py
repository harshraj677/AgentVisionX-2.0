from app.providers.base import ProviderError, ProviderResult
from app.schemas.chat import Usage
from tests.conftest import OK_RESULT, FakeProvider

BODY = {"messages": [{"role": "user", "content": "hi"}]}


def test_health(make_client):
    r = make_client().get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["providers"] == {"gemini": False, "openai": False}
    assert "x-request-id" in r.headers


def test_chat_success(make_client):
    r = make_client(FakeProvider(OK_RESULT)).post("/api/chat", json=BODY)
    assert r.status_code == 200
    data = r.json()
    assert data["message"] == {"role": "assistant", "content": "Hello!"}
    assert data["metadata"]["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5, "source": "provider"}
    assert data["metadata"]["request_id"] == r.headers["x-request-id"]


def test_usage_unavailable_is_not_invented(make_client):
    res = ProviderResult(text="x", model="m", usage=Usage())
    data = make_client(FakeProvider(res)).post("/api/chat", json=BODY).json()
    assert data["metadata"]["usage"] == {
        "input_tokens": None, "output_tokens": None, "total_tokens": None, "source": "unavailable"
    }


def test_gemini_not_configured(make_client):
    r = make_client().post("/api/chat", json=BODY)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_not_configured"


def test_openai_not_configured(make_client):
    r = make_client().post("/api/chat", json={**BODY, "provider": "openai"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_not_configured"


def test_provider_error_is_forwarded(make_client):
    p = FakeProvider(error=ProviderError(429, "rate_limited", "slow down"))
    r = make_client(p).post("/api/chat", json=BODY)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_validation_errors(make_client):
    c = make_client(FakeProvider(OK_RESULT))
    assert c.post("/api/chat", json={"messages": []}).status_code == 422
    assert c.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422
    r = c.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert r.json()["error"]["code"] == "invalid_conversation"


def test_message_too_long(make_client):
    c = make_client(FakeProvider(OK_RESULT), max_message_chars=10)
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "x" * 11}]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "message_too_long"


def test_payload_too_large(make_client):
    c = make_client(FakeProvider(OK_RESULT), max_request_bytes=50)
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "x" * 100}]})
    assert r.status_code == 413
