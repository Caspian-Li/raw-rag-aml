import httpx
import pytest
from openai import BadRequestError

from conftest import add_payload, search_payload
from rawrag_aml.query_limits import query_prefix


def length_error(message="Range of input length should be [1, 33000]"):
    return BadRequestError(message, response=httpx.Response(400, request=httpx.Request("POST", "https://embed.test/v1/embeddings")),
                           body={"code": "InvalidParameter", "message": message})


@pytest.mark.parametrize("text", ["  short question\n", "literal <|endoftext|> text", "中文😀测试 " * 5000])
def test_query_prefix_is_exact_unicode_prefix(text):
    result = query_prefix(text)
    assert text.startswith(result)
    assert len(result.encode("utf-8")) <= 8192
    if len(text.encode("utf-8")) <= 8192:
        assert result == text


def test_accepted_long_query_is_never_changed(harness):
    h = harness
    h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    original = "Synthetic table row, no benchmark content. " * 5000
    response = h.client.post("/v1/memories/search", headers=h.headers,
                             json={**search_payload(), "query": original})
    assert response.status_code == 200
    assert h.backend.embedding_inputs[-1] == [original]
    assert h.backend.rerank_requests[-1]["query"] == original
    assert h.client.get("/version").json()["query_embedding_policy"]["safe_prefix_utf8_bytes"] == 8192


def test_only_rejected_query_embedding_receives_truncated_prefix(harness):
    h = harness
    h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    calls = []
    endpoint = h.backend.create

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        if len(calls) == 1:
            raise length_error()
        return await endpoint(**kwargs)

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    original = "synthetic query " * 3000
    response = h.client.post("/v1/memories/search", headers=h.headers,
                             json={**search_payload(), "query": original})
    assert response.status_code == 200
    assert calls == [[original], [query_prefix(original)]]
    assert h.backend.rerank_requests[-1]["query"] == original


def test_unrelated_bad_request_does_not_trigger_truncation(harness):
    h = harness
    h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        raise length_error("Unsupported dimension parameter")

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    original = search_payload()["query"]
    response = h.client.post("/v1/memories/search", headers=h.headers, json=search_payload())
    assert response.status_code == 503 and calls == [[original]]


def test_single_truncated_retry_is_bounded_and_leaves_index_unchanged(harness):
    h = harness
    h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    before = h.app.state.engine.store.snapshot("user-1", True)
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        raise length_error()

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    original = "synthetic query " * 3000
    response = h.client.post("/v1/memories/search", headers=h.headers,
                             json={**search_payload(), "query": original})
    assert response.status_code == 503 and calls == [[original], [query_prefix(original)]]
    after = h.app.state.engine.store.snapshot("user-1", True)
    assert before["index"] == after["index"] and before["messages"] == after["messages"]
    assert not h.backend.rerank_requests


def test_add_does_not_use_query_truncation(harness):
    h = harness
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        raise length_error()

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    response = h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    assert response.status_code == 503 and len(calls) == 1
