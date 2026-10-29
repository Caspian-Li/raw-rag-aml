import httpx
import pytest
from types import SimpleNamespace
from openai import BadRequestError

from conftest import add_payload, search_payload
from rawrag_aml.query_limits import query_encoding, query_prefix


def length_error(message="Range of input length should be [1, 33000]", code="InvalidParameter"):
    return BadRequestError(message, response=httpx.Response(400, request=httpx.Request("POST", "https://embed.test/v1/embeddings")),
                           body={"code": code, "message": message})


@pytest.mark.parametrize("text", ["  short question\n", "literal <|endoftext|> text", "中文😀测试 " * 5000])
def test_query_prefix_is_exact_unicode_prefix(text):
    result = query_prefix(text)
    assert text.startswith(result)
    assert len(query_encoding().encode(result, disallowed_special=())) <= 8192
    if len(query_encoding().encode(text, disallowed_special=())) <= 8192:
        assert result == text


def test_only_embedding_receives_truncated_query(harness):
    h = harness
    assert h.client.post("/v1/memories/add", headers=h.headers, json=add_payload()).status_code == 200
    original = "Synthetic table row, no benchmark content. " * 5000
    response = h.client.post("/v1/memories/search", headers=h.headers,
                             json={**search_payload(), "query": original})
    assert response.status_code == 200
    assert h.backend.embedding_inputs[-1] == [query_prefix(original)]
    assert h.backend.rerank_requests[-1]["query"] == original
    assert h.client.get("/version").json()["query_embedding_policy"]["local_token_budget"] == 8192


def test_provider_length_rejection_gets_shorter_prefix(harness):
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
    original = "synthetic query " * 300
    response = h.client.post("/v1/memories/search", headers=h.headers,
                             json={**search_payload(), "query": original})
    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0] == [original] and original.startswith(calls[1][0])
    assert 0 < len(calls[1][0]) < len(original)
    assert h.backend.rerank_requests[-1]["query"] == original


def test_unrelated_bad_request_is_not_reinterpreted_as_length_error(harness):
    h = harness
    h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        raise length_error("Unsupported dimension parameter")

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    original = search_payload()["query"]
    response = h.client.post("/v1/memories/search", headers=h.headers, json=search_payload())
    assert response.status_code == 503
    assert calls == [[original]]


def test_length_fallback_is_bounded_and_leaves_index_unchanged(harness):
    h = harness
    h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    before = h.app.state.engine.store.snapshot("user-1", True)
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        raise length_error()

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    original = "synthetic query " * 300
    response = h.client.post("/v1/memories/search", headers=h.headers,
                             json={**search_payload(), "query": original})
    assert response.status_code == 503 and len(calls) == 4
    assert all(len(a[0]) > len(b[0]) for a, b in zip(calls, calls[1:]))
    after = h.app.state.engine.store.snapshot("user-1", True)
    assert before["index"] == after["index"] and before["messages"] == after["messages"]
    assert not h.backend.rerank_requests


def test_add_does_not_use_query_truncation_or_length_fallback(harness):
    h = harness
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        raise length_error()

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    response = h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    assert response.status_code == 503 and len(calls) == 1


def test_provider_usage_corrects_tokenizer_mismatch(harness):
    h = harness
    h.client.post("/v1/memories/add", headers=h.headers, json=add_payload())
    endpoint = h.backend.create
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs["input"])
        response = await endpoint(**kwargs)
        response.usage = SimpleNamespace(total_tokens=14000 if len(calls) == 1 else 8000)
        return response

    h.adapter.embedding_client.embeddings.endpoint = type("Endpoint", (), {"create": staticmethod(provider)})()
    original = "synthetic data table " * 4000
    response = h.client.post("/v1/memories/search", headers=h.headers,
                             json={**search_payload(), "query": original})
    assert response.status_code == 200 and len(calls) == 2
    assert original.startswith(calls[0][0]) and calls[0][0].startswith(calls[1][0])
    assert len(calls[1][0]) < len(calls[0][0])
    assert h.backend.rerank_requests[-1]["query"] == original
