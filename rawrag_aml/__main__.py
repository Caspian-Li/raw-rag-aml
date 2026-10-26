import argparse
import uvicorn
from .provenance import verify_upstream


def main():
    parser = argparse.ArgumentParser(description="Serve RAW-RAG AML Add/Search (one worker)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    verify_upstream()
    uvicorn.run("rawrag_aml.api:create_app", factory=True, host=args.host, port=args.port,
                workers=1, limit_concurrency=32, timeout_keep_alive=5, access_log=False)


if __name__ == "__main__":
    main()
