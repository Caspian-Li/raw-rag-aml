"""Reuse upstream algorithms; restrict transport and disable answer/logging."""
from contextvars import ContextVar
import hashlib
import logging
import math
import sys

from openai import BadRequestError

from .query_limits import (
    QUERY_EMBEDDING_TOKEN_BUDGET, QUERY_LENGTH_FALLBACKS,
    is_provider_length_error, query_encoding, query_prefix,
)
from .settings import ROOT

EVERCORE = ROOT / "vendor/raw-rag/methods/EverCore"
sys.path.insert(0, str(EVERCORE))

from evaluation.src.adapters.rag_adapter import RAGAdapter  # noqa: E402
from evaluation.src.core.data_models import Conversation, Message  # noqa: E402

add_vector_cache = ContextVar("add_vector_cache", default=None)
query_embedding_active = ContextVar("query_embedding_active", default=False)
logger = logging.getLogger("rawrag_aml")


class FloatEmbeddingEndpoint:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    async def create(self, **kwargs):
        inputs = kwargs["input"]
        if query_embedding_active.get():
            inputs = [query_prefix(text) for text in inputs]
            if inputs != kwargs["input"]:
                logger.warning("Query embedding input truncated to local token budget")
        for attempt in range(QUERY_LENGTH_FALLBACKS + 1):
            try:
                response = await self.endpoint.create(
                    **{**kwargs, "input": inputs}, encoding_format="float", dimensions=1024)
                usage = getattr(response, "usage", None)
                used = getattr(usage, "total_tokens", None)
                if query_embedding_active.get() and len(inputs) == 1 and isinstance(used, int) and used > QUERY_EMBEDDING_TOKEN_BUDGET:
                    if attempt == QUERY_LENGTH_FALLBACKS:
                        raise RuntimeError("Query embedding could not satisfy provider token budget")
                    count = len(query_encoding().encode(inputs[0], disallowed_special=()))
                    limit = max(1, min(count - 1, count * QUERY_EMBEDDING_TOKEN_BUDGET // used))
                    shorter = query_prefix(inputs[0], limit)
                    if not shorter.strip() or shorter == inputs[0]:
                        raise RuntimeError("Query embedding could not shorten input safely")
                    inputs = [shorter]
                    logger.warning("Query embedding prefix shortened to satisfy provider token usage")
                    continue
                break
            except BadRequestError as exc:
                if not query_embedding_active.get() or not is_provider_length_error(exc) or attempt == QUERY_LENGTH_FALLBACKS:
                    raise
                encoding = query_encoding()
                shorter = [query_prefix(text, max(1, len(encoding.encode(text, disallowed_special=())) // 2))
                           for text in inputs]
                if shorter == inputs or any(not text.strip() for text in shorter):
                    raise
                inputs = shorter
                logger.warning("Query embedding prefix shortened after provider input-length rejection")
        data = response.data
        expected = len(kwargs["input"])
        if len(data) != expected or {item.index for item in data} != set(range(expected)):
            raise ValueError("Embedding response has missing or duplicated rows")
        data.sort(key=lambda item: item.index)
        for item in data:
            vector = item.embedding
            if len(vector) != 1024 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector) or not any(vector):
                raise ValueError("Embedding response has invalid dimensions or values")
        return response


class EmbeddingClient:
    def __init__(self, client):
        self.client = client
        self.embeddings = FloatEmbeddingEndpoint(client.embeddings)

    def __getattr__(self, name):
        return getattr(self.client, name)


class CompetitionAdapter(RAGAdapter):
    def __init__(self, settings):
        config = {
            "adapter": "rag", "sample_workers": 2,
            "chunking": settings.profile()["chunking"],
            "embedding": {"model": "text-embedding-v4", "api_key": settings.embedding_key,
                          "base_url": settings.embedding_url, "batch_size": 10, "max_retries": 3},
            "storage": {"backend": "local"},
            "rerank": {"enabled": True, "model": "rerank-v3.5", "api_key": settings.rerank_key,
                       "base_url": settings.rerank_url, "endpoint": "/v2/rerank",
                       "timeout_seconds": 120, "max_retries": 3},
            "search": {"embedding_top_k": 128, "rerank_top_k": 15,
                       "sort_selected_chunks_by_time": False, "dynamic_top_k": {"enabled": False}},
            # Upstream construction requires a reader client. It is never called;
            # point it to the embedding endpoint, using the same real credential.
            "llm": {"model": "gpt-4o-mini", "api_key": settings.embedding_key,
                    "base_url": settings.embedding_url, "temperature": 0, "max_tokens": 16384},
            "answer": {"prompt_key": "answer_prompt_cot"},
        }
        super().__init__(config, output_dir=settings.database.parent)
        if self._encoding is None or self._encoding.name != "cl100k_base":
            raise RuntimeError("Required cl100k_base tokenizer unavailable; word fallback forbidden")
        self.embedding_client = EmbeddingClient(self.embedding_client)

    async def answer(self, *args, **kwargs):
        raise RuntimeError("Answer is disabled; the AML platform generates answers")

    async def _write_request_log(self, **kwargs):
        # Do not retain payloads, vectors, upstream responses or credentials in logs.
        return None

    async def _embed_texts(self, texts, **kwargs):
        cache = add_vector_cache.get()
        if cache is None:
            token = query_embedding_active.set(kwargs.get("stage") == "search")
            try:
                return await super()._embed_texts(texts, **kwargs)
            finally:
                query_embedding_active.reset(token)
        digests = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        missing = dict((digest, text) for digest, text in zip(digests, texts) if digest not in cache)
        if missing:
            vectors = await super()._embed_texts(list(missing.values()), **kwargs)
            if len(vectors) != len(missing):
                raise ValueError("Incomplete embedding batch")
            cache.update(zip(missing, vectors))
        return [cache[digest] for digest in digests]
