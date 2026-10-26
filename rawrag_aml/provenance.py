import hashlib
import json
from pathlib import Path

from .settings import ROOT, UPSTREAM_COMMIT


def verify_upstream(root=ROOT):
    manifest = json.loads((root / "UPSTREAM.json").read_text())
    if manifest["commit"] != UPSTREAM_COMMIT or len(manifest["files"]) != 731:
        raise RuntimeError("Unexpected upstream manifest")
    vendor = (root / "vendor/raw-rag").resolve()
    for relative, expected in manifest["files"].items():
        path = (vendor / relative).resolve()
        if not path.is_relative_to(vendor) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Upstream source mismatch: {relative}")
    return len(manifest["files"])
