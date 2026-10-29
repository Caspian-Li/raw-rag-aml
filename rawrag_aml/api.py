import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from . import __version__
from .engine import Engine
from .query_limits import query_embedding_policy
from .schemas import AddRequest, SearchRequest
from .settings import Settings, UPSTREAM_COMMIT
from .storage import Conflict

logger = logging.getLogger("rawrag_aml")


class ContractMiddleware:
    """Authenticate before parsing payloads; enforce an explicit byte limit."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope["headers"]}
        public = scope["method"] == "GET" and scope["path"] in {"/health", "/version"}
        if not public:
            if self.settings.auth_scheme == "x-api-key":
                actual = headers.get(b"x-api-key", b"")
            else:
                scheme, separator, value = headers.get(b"authorization", b"").partition(b" ")
                actual = value if separator and scheme.lower() == self.settings.auth_scheme.encode() else b""
            if not hmac.compare_digest(actual, self.settings.service_key.encode()):
                return await JSONResponse({"detail": {"reason": "invalid_credentials"}}, status_code=401)(scope, receive, send)
        if b"content-length" in headers:
            try:
                size = int(headers[b"content-length"])
            except ValueError:
                return await JSONResponse({"detail": {"reason": "invalid_content_length"}}, status_code=400)(scope, receive, send)
            if size < 0 or size > self.settings.max_body_bytes:
                return await JSONResponse({"detail": {"reason": "payload_too_large"}}, status_code=413)(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.settings.max_body_bytes:
                    raise HTTPException(status_code=413, detail={"reason": "payload_too_large"})
            return message

        return await self.app(scope, limited_receive, send)


def create_app(settings=None, adapter=None):
    settings = settings or Settings.from_env()
    engine = Engine(settings, adapter)

    async def purge_loop():
        while True:
            await asyncio.sleep(600)
            try:
                await asyncio.to_thread(engine.store.expire, settings.retention_days)
            except Exception as exc:
                logger.error("Retention cleanup failed: %s", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(app):
        await engine.start()
        cleanup = asyncio.create_task(purge_loop())
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            await engine.close()

    app = FastAPI(title="RAW-RAG AML Textual API", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.add_middleware(ContractMiddleware, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": {"reason": "invalid_request", "errors": [
            {"loc": list(item["loc"]), "type": item["type"]} for item in exc.errors()
        ]}}, status_code=422)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/version")
    async def version():
        return {"version": __version__, "upstream_commit": UPSTREAM_COMMIT,
                "deployment_commit": os.getenv("AML_BUILD_COMMIT", "unrecorded"),
                "configuration_sha256": settings.fingerprint(),
                "embedding_model": "text-embedding-v4", "rerank_model": "rerank-v3.5",
                "query_embedding_policy": query_embedding_policy(),
                "answer": "AML platform"}

    async def invoke(operation, payload):
        try:
            async with asyncio.timeout(settings.operation_timeout):
                return await operation(payload)
        except Conflict:
            return JSONResponse({"detail": {"reason": "request_id_conflict"}}, status_code=409)
        except TimeoutError:
            return JSONResponse({"detail": {"reason": "operation_timeout"}}, status_code=503,
                                headers={"Retry-After": "30"})
        except Exception as exc:
            logger.warning("Operation unavailable: %s", type(exc).__name__)
            return JSONResponse({"detail": {"reason": "operation_unavailable"}}, status_code=503,
                                headers={"Retry-After": "30"})

    @app.post("/v1/memories/add")
    async def add(payload: AddRequest):
        return await invoke(engine.add, payload)

    @app.post("/v1/memories/search")
    async def search(payload: SearchRequest):
        return await invoke(engine.search, payload)

    return app
