"""Local checks only: no model calls, no AML submission, no publishing."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rawrag_aml.provenance import verify_upstream
from rawrag_aml.settings import Settings

if __name__ == "__main__":
    verify_upstream()
    settings = Settings.from_env()
    print(json.dumps({"profile": settings.profile(), "profile_sha256": settings.fingerprint(),
                      "credentials": "present; not displayed", "live_connectivity": "not tested"}, indent=2))
