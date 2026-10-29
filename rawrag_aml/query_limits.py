"""Provider input protection for query embeddings, independent of stored indexes."""
import re

QUERY_EMBEDDING_SAFE_BYTES = 8192


def query_prefix(text, limit=QUERY_EMBEDDING_SAFE_BYTES):
    """Conservative UTF-8 prefix, used only after a provider length rejection."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


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
        "version": "on-length-error-utf8-prefix-8192-v1",
        "scope": "query_embedding_only",
        "trigger": "provider_400_InvalidParameter_input_length",
        "safe_prefix_utf8_bytes": QUERY_EMBEDDING_SAFE_BYTES,
        "keep": "prefix",
        "truncated_retries_per_embedding_attempt": 1,
        "reranker_query": "original",
    }
