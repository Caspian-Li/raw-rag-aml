import pytest
from rawrag_aml.provenance import verify_upstream
from rawrag_aml.settings import Settings


def test_all_upstream_sources_unchanged():
    assert verify_upstream() == 731


def test_academic_model_cannot_silently_fall_back(monkeypatch):
    monkeypatch.setenv("AML_EMBEDDING_MODEL", "text-embedding-3-small")
    with pytest.raises(ValueError, match="fixed to text-embedding-v4"):
        Settings.from_env()
