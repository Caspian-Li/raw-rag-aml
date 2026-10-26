"""Manual synthetic connectivity check; --exercise explicitly permits model calls."""
import argparse
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

import httpx
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--exercise", action="store_true", help="Make billable Add/Search calls with synthetic text")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("Use a base URL without embedded credentials, query or fragment")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    with httpx.Client(timeout=1750) as client:
        for path in ["/health", "/version"]:
            response = client.get(base + path)
            response.raise_for_status()
            print(path, response.status_code)
        if not args.exercise:
            print("Health/version checked. Upstream APIs and persistence not exercised.")
            return
        key, scheme = os.getenv("AML_MEMORY_KEY", ""), os.getenv("AML_AUTH_SCHEME", "bearer").lower()
        if not key or scheme not in {"bearer", "token", "x-api-key"}:
            raise SystemExit("Configure AML_MEMORY_KEY and AML_AUTH_SCHEME locally")
        headers = {"X-Api-Key": key} if scheme == "x-api-key" else {"Authorization": f"{scheme.title()} {key}"}
        marker = uuid.uuid4().hex
        payload = {"request_id": "selfcheck-" + marker, "user_id": "selfcheck-" + marker,
                   "session_id": "synthetic-session", "messages": [
                       {"role": "user", "content": f"My reproducibility marker is {marker}.", "timestamp": int(time.time() * 1000)}]}
        expected = {k: payload[k] for k in ["request_id", "user_id", "session_id"]}
        for _ in range(2):
            response = client.post(base + "/v1/memories/add", headers=headers, json=payload)
            response.raise_for_status()
            assert response.json() == {"success": True, **expected}
        response = client.post(base + "/v1/memories/search", headers=headers, json={
            "query": "What is my reproducibility marker?", "user_id": payload["user_id"], "top_k": 100})
        response.raise_for_status()
        data = response.json()["data"]
        assert 1 <= len(data) <= 100
        assert all(isinstance(row["id"], str) and row["id"] and isinstance(row["content"], str) and row["content"] for row in data)
        assert any(marker in row["content"] for row in data)
        print("Synthetic Add/retry/Search checks passed. This is not an official AML Smoke.")


if __name__ == "__main__":
    main()
