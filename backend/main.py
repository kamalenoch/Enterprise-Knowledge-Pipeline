from contextlib import asynccontextmanager
import logging
import time
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import get_settings
from database import bootstrap_database, close_database
from services.cache import SemanticCache
from services.documents import delete_document, list_documents, read_document, replace_document
from services.ingestion import ingest_text
from services.rag import answer_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

settings = get_settings()
semantic_cache = SemanticCache(settings)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    migration_applied = await bootstrap_database()
    logging.getLogger(__name__).info("Database bootstrap completed", extra={"migration_applied": migration_applied})
    await semantic_cache.ensure_index()
    try:
        yield
    finally:
        await semantic_cache.redis.aclose()
        await close_database()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class IngestRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    user_id: str = Field(default="demo-user", min_length=1)


class DocumentUpdateRequest(BaseModel):
    content: str = Field(min_length=1)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Any:
    logging.getLogger(__name__).exception("Unhandled request failure", extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Unhandled service error"})


@app.post("/api/ingest")
async def ingest(payload: IngestRequest) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        result = await ingest_text(
            tenant_id=payload.tenant_id,
            file_name=payload.file_name,
            content=payload.content,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cache_entries_removed = await semantic_cache.invalidate_tenant(result.tenant_id)
    return {
        "status": "ok",
        "tenant_id": result.tenant_id,
        "file_name": result.file_name,
        "chunks_inserted": result.chunks_inserted,
        "telemetry": {
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "cache_status": "BYPASS",
            "database_lookup_ms": 0,
            "tokens_used": 0,
            "currency_saved": 0,
        },
        "logs": [
            {
                "event": "ingest.completed",
                "tenant_id": result.tenant_id,
                "file_name": result.file_name,
                "chunks_inserted": result.chunks_inserted,
                "cache_entries_removed": cache_entries_removed,
                "rls_assertion": "document chunks inserted with tenant transaction scope",
            }
        ],
    }


@app.get("/api/documents")
async def documents(x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> dict[str, Any]:
    summaries = await list_documents(x_tenant_id)
    return {"tenant_id": x_tenant_id, "documents": [summary.__dict__ for summary in summaries]}


@app.get("/api/documents/{file_name}")
async def document_detail(file_name: str, x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> dict[str, Any]:
    document = await read_document(x_tenant_id, file_name)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found for tenant")
    return {"tenant_id": x_tenant_id, "document": document}


@app.put("/api/documents/{file_name}")
async def document_update(
    file_name: str,
    payload: DocumentUpdateRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
) -> dict[str, Any]:
    chunks = await replace_document(x_tenant_id, file_name, payload.content, settings)
    cache_entries_removed = await semantic_cache.invalidate_tenant(x_tenant_id)
    return {
        "status": "ok",
        "tenant_id": x_tenant_id,
        "file_name": file_name,
        "chunks_inserted": chunks,
        "cache_entries_removed": cache_entries_removed,
    }


@app.delete("/api/documents/{file_name}")
async def document_delete(file_name: str, x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> dict[str, Any]:
    chunks_deleted = await delete_document(x_tenant_id, file_name)
    cache_entries_removed = await semantic_cache.invalidate_tenant(x_tenant_id)
    return {
        "status": "ok",
        "tenant_id": x_tenant_id,
        "file_name": file_name,
        "chunks_deleted": chunks_deleted,
        "cache_entries_removed": cache_entries_removed,
    }


@app.post("/api/query")
async def query(
    payload: QueryRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
) -> dict[str, Any]:
    result = await answer_query(
        tenant_id=x_tenant_id,
        user_id=payload.user_id,
        prompt=payload.query,
        settings=settings,
        cache=semantic_cache,
    )
    return {"answer": result.answer, "telemetry": result.telemetry, "logs": result.logs}
