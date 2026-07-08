from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from config import Settings
from database import tenant_transaction
from services.ingestion import chunk_text, embed_texts


@dataclass(frozen=True)
class DocumentSummary:
    file_name: str
    chunks: int
    content_preview: str
    updated_at: str


async def list_documents(tenant_id: str) -> list[DocumentSummary]:
    async with tenant_transaction(tenant_id) as transaction:
        result = await transaction.execute(
            text(
                """
                SELECT
                    file_name,
                    COUNT(*)::int AS chunks,
                    LEFT(STRING_AGG(content, ' ' ORDER BY chunk_index, created_at), 180) AS content_preview,
                    MAX(created_at)::text AS updated_at
                FROM document_chunks
                WHERE tenant_id = :tenant_id
                GROUP BY file_name
                ORDER BY MAX(created_at) DESC, file_name ASC
                """
            ),
            {"tenant_id": tenant_id},
        )
        return [DocumentSummary(**dict(row._mapping)) for row in result]


async def read_document(tenant_id: str, file_name: str) -> dict[str, Any] | None:
    async with tenant_transaction(tenant_id) as transaction:
        result = await transaction.execute(
            text(
                """
                SELECT file_name, content, chunk_index, created_at::text AS created_at
                FROM document_chunks
                WHERE tenant_id = :tenant_id
                  AND file_name = :file_name
                ORDER BY chunk_index, created_at
                """
            ),
            {"tenant_id": tenant_id, "file_name": file_name},
        )
        rows = [dict(row._mapping) for row in result]

    if not rows:
        return None

    return {
        "file_name": file_name,
        "content": "\n\n".join(row["content"] for row in rows),
        "chunks": len(rows),
        "created_at": rows[0]["created_at"],
    }


async def replace_document(tenant_id: str, file_name: str, content: str, settings: Settings) -> int:
    chunks = chunk_text(content)
    embeddings = await embed_texts(settings, chunks)

    async with tenant_transaction(tenant_id) as transaction:
        await transaction.execute(
            text("DELETE FROM document_chunks WHERE tenant_id = :tenant_id AND file_name = :file_name"),
            {"tenant_id": tenant_id, "file_name": file_name},
        )
        for chunk_index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            await transaction.execute(
                text(
                    """
                    INSERT INTO document_chunks (tenant_id, file_name, chunk_index, content, embedding)
                    VALUES (:tenant_id, :file_name, :chunk_index, :content, :embedding)
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "file_name": file_name,
                    "chunk_index": chunk_index,
                    "content": chunk,
                    "embedding": embedding,
                },
            )

    return len(chunks)


async def delete_document(tenant_id: str, file_name: str) -> int:
    async with tenant_transaction(tenant_id) as transaction:
        result = await transaction.execute(
            text("DELETE FROM document_chunks WHERE tenant_id = :tenant_id AND file_name = :file_name"),
            {"tenant_id": tenant_id, "file_name": file_name},
        )
        return int(result.rowcount or 0)
