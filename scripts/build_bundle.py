"""Export only a clean Git HEAD; never package the live working directory."""
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, str(ROOT / "scripts/verify_release.py")], cwd=ROOT, check=True)
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT).strip():
        raise SystemExit("Commit reviewed source changes before exporting a release")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"raw-rag-aml-{commit[:12]}.tar.gz"
    subprocess.run(["git", "archive", "--format=tar.gz", "--prefix=raw-rag-aml/", "-o", str(archive), "HEAD"], cwd=ROOT, check=True)
    bundle = output / f"raw-rag-aml-{commit[:12]}.bundle"
    subprocess.run(["git", "bundle", "create", str(bundle), "main"], cwd=ROOT, check=True)
    for file in [archive, bundle]:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        file.with_suffix(file.suffix + ".sha256").write_text(f"{digest}  {file.name}\n")
        print(file)


if __name__ == "__main__":
    main()
