import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from config import Settings
from database import tenant_transaction
from services.cache import SemanticCache
from services.ingestion import embed_texts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryResult:
    answer: str
    telemetry: dict[str, Any]
    logs: list[dict[str, Any]]


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _currency_saved(tokens_bypassed: int, settings: Settings) -> float:
    blended_cost = settings.llm_input_cost_per_1k + settings.llm_output_cost_per_1k
    return round((tokens_bypassed / 1000) * blended_cost, 6)


async def _retrieve_chunks(
    tenant_id: str,
    query_vector: list[float],
    settings: Settings,
) -> tuple[list[dict[str, Any]], float]:
    start = time.perf_counter()
    async with tenant_transaction(tenant_id) as transaction:
        result = await transaction.execute(
            text(
                """
                SELECT id::text, file_name, content, embedding <=> CAST(:embedding AS vector) AS distance
                FROM document_chunks
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT 3
                """
            ),
            {"embedding": query_vector},
        )
        chunks = [dict(row._mapping) for row in result]
    return chunks, _elapsed_ms(start)


async def answer_query(
    tenant_id: str,
    user_id: str,
    prompt: str,
    settings: Settings,
    cache: SemanticCache,
) -> QueryResult:
    request_start = time.perf_counter()
    logs: list[dict[str, Any]] = [{"event": "query.received", "tenant_id": tenant_id, "prompt": prompt}]

    query_vector = (await embed_texts(settings, [prompt]))[0]
    cache_start = time.perf_counter()
    cached = await cache.get_semantic_cache(tenant_id, query_vector)
    cache_lookup_ms = _elapsed_ms(cache_start)

    if cached:
        tokens_bypassed = int(cached.get("metadata", {}).get("tokens_used", 0))
        logs.append(
            {
                "event": "cache.hit",
                "tenant_id": tenant_id,
                "similarity": cached["similarity"],
                "vector_distance": cached["distance"],
                "rls_assertion": "tenant_id filter enforced before response release",
            }
        )
        return QueryResult(
            answer=cached["response_text"],
            telemetry={
                "latency_ms": _elapsed_ms(request_start),
                "cache_status": "HIT",
                "cache_hit": True,
                "database_lookup_ms": 0,
                "cache_lookup_ms": cache_lookup_ms,
                "llm_engine_ms": 0,
                "tokens_used": 0,
                "tokens_bypassed": tokens_bypassed,
                "currency_saved": _currency_saved(tokens_bypassed, settings),
            },
            logs=logs,
        )

    chunks, database_lookup_ms = await _retrieve_chunks(tenant_id, query_vector, settings)
    logs.append(
        {
            "event": "database.vector_search",
            "tenant_id": tenant_id,
            "rls_assertion": "SET LOCAL app.current_tenant_id applied inside transaction",
            "matches": [
                {"chunk_id": item["id"], "file_name": item["file_name"], "vector_distance": float(item["distance"])}
                for item in chunks
            ],
        }
    )

    context = "\n\n".join(f"[{item['file_name']}] {item['content']}" for item in chunks)
    llm_start = time.perf_counter()
    chat_client = AsyncOpenAI(
        api_key=os.getenv("GROQ_API_KEY", settings.groq_api_key.get_secret_value()),
        base_url=settings.groq_base_url,
    )
    completion = await chat_client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Answer only from the supplied enterprise context. "
                    "If the answer is not present, say that the corpus does not contain enough evidence."
                ),
            },
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion:\n{prompt}"},
        ],
        temperature=0.2,
    )
    llm_engine_ms = _elapsed_ms(llm_start)
    answer = completion.choices[0].message.content or ""
    usage = completion.usage
    tokens_used = int((usage.total_tokens if usage else 0) or 0)

    async with tenant_transaction(tenant_id) as transaction:
        await transaction.execute(
            text(
                """
                INSERT INTO audit_logs (tenant_id, user_id, action, metadata)
                VALUES (:tenant_id, :user_id, :action, :metadata)
                """
            ).bindparams(bindparam("metadata", type_=JSONB)),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "action": "query.answer",
                "metadata": {
                    "prompt": prompt,
                    "cache_status": "MISS",
                    "chunks": [{"id": item["id"], "distance": float(item["distance"])} for item in chunks],
                    "tokens_used": tokens_used,
                    "latency_ms": _elapsed_ms(request_start),
                },
            },
        )

    asyncio.create_task(
        cache.set_semantic_cache(
            tenant_id=tenant_id,
            query_text=prompt,
            query_vector=query_vector,
            response_text=answer,
            metadata={"tokens_used": tokens_used, "source_chunk_count": len(chunks)},
        )
    )
    logs.append({"event": "cache.write_scheduled", "tenant_id": tenant_id, "tokens_used": tokens_used})

    return QueryResult(
        answer=answer,
        telemetry={
            "latency_ms": _elapsed_ms(request_start),
            "cache_status": "MISS",
            "cache_hit": False,
            "database_lookup_ms": database_lookup_ms,
            "cache_lookup_ms": cache_lookup_ms,
            "llm_engine_ms": llm_engine_ms,
            "tokens_used": tokens_used,
            "tokens_bypassed": 0,
            "currency_saved": 0,
        },
        logs=logs,
    )
