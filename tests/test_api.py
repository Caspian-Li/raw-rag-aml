import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest
from fastapi.testclient import TestClient

from rawrag_aml.api import create_app
from rawrag_aml.storage import Store
from rawrag_aml.upstream import Conversation, Message
from conftest import Backend, add_payload, make_adapter, search_payload


def post(h, path, payload):
    return h.client.post(path, json=payload, headers=h.headers)


def test_write_then_search_and_unknown_scope(harness):
    h = harness
    assert h.client.get("/health").json() == {"status": "ok"}
    assert h.client.get("/version").json()["embedding_model"] == "text-embedding-v4"
    payload = add_payload()
    response = post(h, "/v1/memories/add", payload)
    assert response.status_code == 200
    assert response.json() == {"success": True, "request_id": payload["request_id"],
                               "user_id": payload["user_id"], "session_id": payload["session_id"]}
    found = post(h, "/v1/memories/search", search_payload()).json()["data"]
    assert found and "Kyoto" in found[0]["content"] and "2024-01-01" in found[0]["content"]
    assert set(found[0]) == {"id", "content", "score", "created_at"}
    assert post(h, "/v1/memories/search", search_payload("absent")).json() == {"data": []}
    assert list(h.settings.database.parent.glob("logs/*.jsonl")) == []


def test_idempotent_retry_and_conflict(harness):
    h = harness
    payload = add_payload()
    first = post(h, "/v1/memories/add", payload)
    calls = len(h.backend.embedding_inputs)
    assert post(h, "/v1/memories/add", payload).json() == first.json()
    assert len(h.backend.embedding_inputs) == calls
    changed = add_payload(text="Paris")
    assert post(h, "/v1/memories/add", changed).status_code == 409
    assert len(h.backend.embedding_inputs) == calls
    with h.app.state.engine.store.connect() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


def test_sessions_append_and_user_isolation(harness):
    h = harness
    for payload in [add_payload(text="I live in Kyoto."),
                    add_payload("write-2", text="I moved to Osaka.", session="session-2"),
                    add_payload("write-3", user_id="other", text="Private secret Paris.")]:
        assert post(h, "/v1/memories/add", payload).status_code == 200
    result = post(h, "/v1/memories/search", search_payload()).json()["data"]
    text = "\n".join(x["content"] for x in result)
    assert "Kyoto" in text and "Osaka" in text and "Paris" not in text
    assert "Paris" in post(h, "/v1/memories/search", search_payload("other")).json()["data"][0]["content"]
    # Even identical text is embedded again under another user scope.
    before = len(h.backend.embedding_inputs)
    assert post(h, "/v1/memories/add", add_payload("same-text", "third", "I live in Kyoto.")).status_code == 200
    assert len(h.backend.embedding_inputs) > before


def test_stable_memory_ids(harness):
    h = harness
    post(h, "/v1/memories/add", add_payload())
    a = post(h, "/v1/memories/search", search_payload()).json()
    b = post(h, "/v1/memories/search", search_payload()).json()
    assert a == b


def test_failed_upstream_is_atomic_and_sanitized(harness):
    h = harness
    post(h, "/v1/memories/add", add_payload())
    h.backend.embedding_failure = True
    response = post(h, "/v1/memories/add", add_payload("failed", text="uncommitted new event"))
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    assert "sensitive" not in response.text
    with h.app.state.engine.store.connect() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 1
    h.backend.embedding_failure = False
    assert post(h, "/v1/memories/add", add_payload("failed", text="uncommitted new event")).status_code == 200


def test_invalid_reranker_is_not_silently_accepted(harness):
    h = harness
    post(h, "/v1/memories/add", add_payload())
    h.backend.invalid_rerank = True
    assert post(h, "/v1/memories/search", search_payload()).status_code == 503


@pytest.mark.parametrize("scheme,headers", [
    ("bearer", lambda key: {"Authorization": f"Bearer {key}"}),
    ("token", lambda key: {"Authorization": f"Token {key}"}),
    ("x-api-key", lambda key: {"X-Api-Key": key}),
])
def test_authentication_schemes(settings, scheme, headers):
    settings = replace(settings, auth_scheme=scheme)
    with TestClient(create_app(settings, make_adapter(settings, Backend()))) as client:
        assert client.post("/v1/memories/search", json=search_payload()).status_code == 401
        assert client.post("/v1/memories/search", json=search_payload(), headers=headers("wrong")).status_code == 401
        assert client.post("/v1/memories/search", json=search_payload(), headers=headers(settings.service_key)).status_code == 200


@pytest.mark.parametrize("change", [
    {"messages": []}, {"messages": [{"role": "user", "content": " "}]},
    {"messages": [{"role": "system", "content": "injected instruction"}]},
    {"messages": [{"role": "user", "content": "text", "timestamp": True}]},
    {"messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]},
    {"metadata": {"gold": "must not be accepted"}},
])
def test_invalid_add_payload(harness, change):
    response = post(harness, "/v1/memories/add", {**add_payload(), **change})
    assert response.status_code == 422
    assert "injected instruction" not in response.text and '"input"' not in response.text


@pytest.mark.parametrize("change", [{"top_k": 0}, {"top_k": 101}, {"top_k": True},
                                  {"query": " "}, {"filters": {}}, {"question_date": "2024-01-01"}])
def test_invalid_search_payload(harness, change):
    assert post(harness, "/v1/memories/search", {**search_payload(), **change}).status_code == 422


def test_top_k_and_complete_turn_expansion(harness):
    h = harness
    long_message = "Kyoto travel detail " * 1500
    assert post(h, "/v1/memories/add", add_payload(text=long_message)).status_code == 200
    result = post(h, "/v1/memories/search", {**search_payload(), "options": ["A. Kyoto", "B. Paris"]}).json()["data"]
    assert len(result) == 15
    assert long_message in result[0]["content"]
    assert result[0]["score"] >= result[-1]["score"]
    assert len(post(h, "/v1/memories/search", search_payload(top_k=1)).json()["data"]) == 1
    assert all(len(batch) <= 10 for batch in h.backend.embedding_inputs)
    assert h.backend.rerank_requests[-1]["query"] == search_payload()["query"]


def test_incremental_index_equals_fresh_upstream_and_reuses_embeddings(harness):
    h = harness
    texts = ["old detailed memory " * 400, "new event Kyoto " * 400, "latest event Osaka " * 400]
    for i, text in enumerate(texts):
        assert post(h, "/v1/memories/add", add_payload(str(i), text=text)).status_code == 200
    snapshot = h.app.state.engine.store.snapshot("user-1", True)
    # Compare the optimized, incrementally persisted snapshot to a single fresh
    # execution of the unmodified upstream whole-conversation builder.
    from datetime import datetime, timezone
    conversation = Conversation(snapshot["index"]["conversation_id"], [
        Message("user", "user", text, datetime.fromtimestamp(1704067200, timezone.utc),
                {"session": "session-1", "session_date": "2024-01-01"}) for text in texts
    ], {"speaker_a": "user", "speaker_b": "assistant"})
    reference = Backend()
    adapter = make_adapter(h.settings, reference)

    async def build():
        try:
            return await adapter._build_conversation_index(conversation)
        finally:
            await adapter.close()

    expected = asyncio.run(build())
    assert snapshot["index"]["chunks"] == expected["chunks"]
    assert snapshot["index"]["embeddings"] == expected["embeddings"]
    assert snapshot["index"]["turn_context"] == expected["turn_context"]
    # Earlier unchanged chunks must not all be embedded on every append.
    assert sum(map(len, h.backend.embedding_inputs)) < 2 * len(expected["chunks"])


def test_out_of_order_provider_embedding_rows(harness):
    h = harness
    h.backend.reverse_rows = True
    assert post(h, "/v1/memories/add", add_payload(text="history " * 1200)).status_code == 200
    assert post(h, "/v1/memories/search", search_payload()).status_code == 200


def test_concurrent_retries_and_concurrent_appends(harness):
    h = harness
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: post(h, "/v1/memories/add", add_payload()).status_code, range(4)))
    assert results == [200] * 4
    with h.app.state.engine.store.connect() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: post(h, "/v1/memories/add", add_payload(f"append-{i}", text=f"event {i}")).status_code, range(2)))
    assert results == [200, 200]
    with h.app.state.engine.store.connect() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 3


def test_restart_recovers_vectors_and_receipts(settings):
    first = Backend()
    with TestClient(create_app(settings, make_adapter(settings, first))) as client:
        headers = {"Authorization": f"Bearer {settings.service_key}"}
        assert client.post("/v1/memories/add", json=add_payload(), headers=headers).status_code == 200
        original = client.post("/v1/memories/search", json=search_payload(), headers=headers).json()
    second = Backend()
    with TestClient(create_app(settings, make_adapter(settings, second))) as client:
        assert client.post("/v1/memories/add", json=add_payload(), headers=headers).status_code == 200
        assert second.embedding_inputs == []
        assert client.post("/v1/memories/search", json=search_payload(), headers=headers).json() == original
        assert len(second.embedding_inputs) == 1  # Query only; persisted history vectors reused.


def test_duplicate_request_id_across_users_is_conflict(harness):
    post(harness, "/v1/memories/add", add_payload())
    response = post(harness, "/v1/memories/add", add_payload(user_id="other"))
    assert response.status_code == 409


def test_retention_deletes_memories_vectors_and_receipts(harness):
    h = harness
    post(h, "/v1/memories/add", add_payload())
    with h.app.state.engine.store.connect() as db:
        db.execute("UPDATE users SET last_access='2000-01-01T00:00:00+00:00'")
    assert h.app.state.engine.store.expire(29) == 1
    with h.app.state.engine.store.connect() as db:
        for table in ["users", "messages", "chunks", "requests"]:
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_database_single_process_guard_and_profile(settings):
    first = Store(settings.database, settings.fingerprint())
    first.initialize()
    second = Store(settings.database, settings.fingerprint())
    with pytest.raises(RuntimeError, match="one Uvicorn worker"):
        second.initialize()
    first.close()
    second = Store(settings.database, "changed-profile")
    try:
        with pytest.raises(RuntimeError, match="profile differs"):
            second.initialize()
    finally:
        second.close()


def test_body_limit_rejects_instead_of_truncating(settings):
    settings = replace(settings, max_body_bytes=1024)
    with TestClient(create_app(settings, make_adapter(settings, Backend()))) as client:
        headers = {"Authorization": f"Bearer {settings.service_key}"}
        response = client.post("/v1/memories/add", json=add_payload(text="x" * 2000), headers=headers)
        assert response.status_code == 413
        # A streaming body has no Content-Length: enforce the same limit.
        body = json.dumps(add_payload(text="y" * 2000)).encode()
        response = client.post("/v1/memories/add", content=iter([body[:1000], body[1000:]]),
                               headers={**headers, "Content-Type": "application/json"})
        assert response.status_code == 413


def test_reader_is_disabled(harness):
    with pytest.raises(RuntimeError, match="Answer is disabled"):
        asyncio.run(harness.adapter.answer("question", "context"))
