from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_COMMIT = "bce0b2437118226b6e717026b7ae353c20738b4f"


@dataclass(frozen=True)
class Settings:
    database: Path
    service_key: str = field(repr=False)
    embedding_key: str = field(repr=False)
    embedding_url: str
    rerank_key: str = field(repr=False)
    rerank_url: str = "https://api.cohere.com"
    auth_scheme: str = "bearer"
    add_concurrency: int = 2
    search_concurrency: int = 2
    max_body_bytes: int = 16 * 1024 * 1024
    retention_days: int = 29
    operation_timeout: int = 1700

    def __post_init__(self):
        if not all((self.service_key.strip(), self.embedding_key.strip(), self.rerank_key.strip())):
            raise ValueError("Configure AML_MEMORY_KEY, AML_EMBEDDING_API_KEY and AML_RERANK_API_KEY")
        if len(self.service_key) < 32:
            raise ValueError("AML_MEMORY_KEY must have at least 32 characters")
        if self.auth_scheme not in {"bearer", "token", "x-api-key"}:
            raise ValueError("AML_AUTH_SCHEME must be bearer, token or x-api-key")
        for url in (self.embedding_url, self.rerank_url):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Upstream API base URLs must use HTTPS without credentials, query or fragment")
        if not 1 <= self.add_concurrency <= 8 or not 1 <= self.search_concurrency <= 8:
            raise ValueError("Concurrency must be in [1, 8]")
        if not 1 <= self.retention_days <= 29:
            raise ValueError("Idle retention must be in [1, 29] days")
        if not 1 <= self.operation_timeout <= 1700 or self.max_body_bytes < 1024:
            raise ValueError("Invalid operation timeout or payload limit")

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        if os.getenv("AML_EMBEDDING_MODEL", "text-embedding-v4") != "text-embedding-v4":
            raise ValueError("This academic submission is fixed to text-embedding-v4")
        return cls(
            database=Path(os.getenv("AML_DATABASE", str(ROOT / "data/memory.sqlite3"))),
            service_key=os.getenv("AML_MEMORY_KEY", ""),
            embedding_key=os.getenv("AML_EMBEDDING_API_KEY", ""),
            embedding_url=os.getenv("AML_EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
            rerank_key=os.getenv("AML_RERANK_API_KEY", ""),
            rerank_url=os.getenv("AML_RERANK_BASE_URL", "https://api.cohere.com").rstrip("/"),
            auth_scheme=os.getenv("AML_AUTH_SCHEME", "bearer").lower(),
            add_concurrency=int(os.getenv("AML_ADD_CONCURRENCY", "2")),
            search_concurrency=int(os.getenv("AML_SEARCH_CONCURRENCY", "2")),
            max_body_bytes=int(os.getenv("AML_MAX_BODY_BYTES", str(16 * 1024 * 1024))),
            retention_days=int(os.getenv("AML_RETENTION_DAYS", "29")),
            operation_timeout=int(os.getenv("AML_OPERATION_TIMEOUT_SECONDS", "1700")),
        )

    def profile(self):
        return {
            "upstream_commit": UPSTREAM_COMMIT,
            "adapter_version": "0.1.0",
            "chunking": {"strategy": "token", "chunk_size": 256, "chunk_overlap": 32,
                         "tokenizer_model": "gpt-4", "expand_turns_on_display": True,
                         "dedup_turns_on_display": False},
            "embedding": {"model": "text-embedding-v4", "dimensions": 1024,
                          "encoding_format": "float", "batch_size": 10,
                          "base_url": self.embedding_url},
            "rerank": {"model": "rerank-v3.5", "base_url": self.rerank_url, "endpoint": "/v2/rerank"},
            "search": {"embedding_top_k": 128, "rerank_top_k": 15,
                       "dynamic_top_k": False, "sort_selected_chunks_by_time": False},
            "answer": "disabled; AML platform responsibility",
        }

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.profile(), sort_keys=True).encode()).hexdigest()
