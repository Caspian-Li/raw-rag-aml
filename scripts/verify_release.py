"""Verify upstream checksums and inspect the proposed tracked/public files."""
from pathlib import Path
import re
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rawrag_aml.provenance import verify_upstream


def main():
    count = verify_upstream()
    try:
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    except subprocess.CalledProcessError:
        raise SystemExit("Initialize/stage the release repository before this check")
    forbidden = []
    expected_vendor = {"vendor/raw-rag/" + name for name in json.loads((ROOT / "UPSTREAM.json").read_text())["files"]}
    for relative in filter(None, paths):
        path = ROOT / relative
        parts = Path(relative).parts
        if relative.startswith("vendor/raw-rag/") and relative not in expected_vendor:
            forbidden.append(relative)
        if path.is_symlink():
            forbidden.append(relative)
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            forbidden.append(relative)
        if not relative.startswith("vendor/raw-rag/") and (parts[0] in {"data", "logs", "dist", "research"} or path.suffix in {".sqlite3", ".db", ".pem", ".key", ".pyc"}):
            forbidden.append(relative)
        if path.is_file() and path.suffix not in {".png"}:
            content = path.read_text(errors="ignore")
            if re.search(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", content):
                forbidden.append(relative)
    if forbidden:
        raise SystemExit("Review excluded/secret-like files: " + ", ".join(sorted(set(forbidden))))
    print(f"Verified {count} unmodified upstream files; public-file checks passed")


if __name__ == "__main__":
    main()
