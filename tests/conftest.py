import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rawrag_aml.api import create_app
from rawrag_aml.settings import Settings
from rawrag_aml.upstream import CompetitionAdapter


class Backend:
    def __init__(self):
        self.embedding_inputs = []
        self.rerank_requests = []
        self.embedding_failure = False
        self.invalid_rerank = False
        self.reverse_rows = False

    async def create(self, *, input, model, encoding_format, dimensions):
        assert model == "text-embedding-v4" and encoding_format == "float" and dimensions == 1024
        await asyncio.sleep(0)
        if self.embedding_failure:
            raise RuntimeError("upstream error with sensitive text that must not escape")
        self.embedding_inputs.append(list(input))
        data = []
        for i, text in enumerate(input):
            digest = hashlib.sha256(text.encode()).digest()
            vector = [float(x + 1) / 256 for x in digest] + [0.0] * (1024 - len(digest))
            data.append(SimpleNamespace(index=i, embedding=vector))
        return SimpleNamespace(data=list(reversed(data)) if self.reverse_rows else data)

    def post(self, *, url, payload, timeout_seconds):
        assert url == "https://rerank.test/v2/rerank"
        assert payload["model"] == "rerank-v3.5"
        self.rerank_requests.append(payload)
        if self.invalid_rerank:
            return {"results": []}
        return {"results": [{"index": i, "relevance_score": 1 / (i + 1)}
                            for i in reversed(range(len(payload["documents"])))]}


@pytest.fixture
def settings(tmp_path):
    return Settings(database=tmp_path / "db/memory.sqlite3", service_key="test-memory-key-" * 3,
                    embedding_key="offline-embedding", embedding_url="https://embed.test/v1",
                    rerank_key="offline-rerank", rerank_url="https://rerank.test")


def make_adapter(settings, backend):
    adapter = CompetitionAdapter(settings)
    adapter.embedding_client.embeddings.endpoint = backend
    adapter._post_json = backend.post
    adapter.embedding_max_retries = 1
    adapter.rerank_max_retries = 1
    return adapter


@pytest.fixture
def harness(settings):
    backend = Backend()
    adapter = make_adapter(settings, backend)
    app = create_app(settings, adapter)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, backend=backend, adapter=adapter, app=app,
                              settings=settings, headers={"Authorization": f"Bearer {settings.service_key}"})


def add_payload(request_id="write-1", user_id="user-1", text="My favorite city is Kyoto.", session="session-1"):
    return {"request_id": request_id, "user_id": user_id, "session_id": session,
            "messages": [{"role": "user", "content": text, "timestamp": 1704067200000}]}


def search_payload(user_id="user-1", top_k=100):
    return {"user_id": user_id, "query": "What is my favorite city?", "top_k": top_k}
