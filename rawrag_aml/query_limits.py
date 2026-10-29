"""Provider input protection for query embeddings, independent of stored indexes."""
from functools import lru_cache
import re

import tiktoken

QUERY_EMBEDDING_TOKEN_BUDGET = 8192
QUERY_LENGTH_FALLBACKS = 3


@lru_cache(maxsize=1)
def query_encoding():
    return tiktoken.get_encoding("cl100k_base")


def query_prefix(text, limit=QUERY_EMBEDDING_TOKEN_BUDGET):
    """Keep an exact Unicode prefix; the local tokenizer is a reference budget."""
    encoding = query_encoding()
    tokens = encoding.encode(text, disallowed_special=())
    if len(tokens) <= limit:
        return text
    # A token boundary can split UTF-8. Drop an incomplete trailing character,
    # rather than introducing replacement characters into the original query.
    return encoding.decode_bytes(tokens[:limit]).decode("utf-8", errors="ignore")


def is_provider_length_error(exc):
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    return (
        isinstance(error, dict)
        and getattr(exc, "status_code", None) == 400
        and error.get("code") == "InvalidParameter"
        and re.search(r"Range of input length should be \[\s*1\s*,\s*\d+\s*\]",
                      str(error.get("message", ""))) is not None
    )


def query_embedding_policy():
    return {
        "version": "prefix-cl100k-8192-v1",
        "scope": "query_embedding_only",
        "local_token_budget": QUERY_EMBEDDING_TOKEN_BUDGET,
        "local_tokenizer": "cl100k_base",
        "provider_tokenizer_equivalence": False,
        "keep": "prefix",
        "length_error_fallbacks_per_embedding_attempt": QUERY_LENGTH_FALLBACKS,
        "fallback": "halve_current_prefix_token_budget",
        "provider_usage_budget": QUERY_EMBEDDING_TOKEN_BUDGET,
        "provider_usage_correction": "shrink_prefix_proportionally_and_reembed",
        "reranker_query": "original",
    }
