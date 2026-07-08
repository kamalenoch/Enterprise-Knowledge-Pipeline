import asyncio
from dataclasses import dataclass
from typing import Iterable

from sentence_transformers import SentenceTransformer
from sqlalchemy import text

from config import Settings
from database import tenant_transaction

_embedding_model: SentenceTransformer | None = None


@dataclass(frozen=True)
class IngestionResult:
    chunks_inserted: int
    file_name: str
    tenant_id: str


def chunk_text(raw_text: str, size: int = 500, overlap: int = 50) -> list[str]:
    normalized = " ".join(raw_text.split())
    if not normalized:
        raise ValueError("content must contain text")

    chunks: list[str] = []
    start = 0
    stride = size - overlap
    while start < len(normalized):
        chunk = normalized[start : start + size].strip()
        if chunk:
            chunks.append(chunk)
        start += stride
    return chunks


def _get_embedding_model(settings: Settings) -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(settings.embedding_model)
    return _embedding_model


def _encode_texts(settings: Settings, texts: list[str]) -> list[list[float]]:
    model = _get_embedding_model(settings)
    vectors = model.encode(texts, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]


async def embed_texts(settings: Settings, texts: Iterable[str]) -> list[list[float]]:
    text_batch = list(texts)
    if not text_batch:
        return []

    embeddings = await asyncio.to_thread(_encode_texts, settings, text_batch)
    for embedding in embeddings:
        if len(embedding) != settings.embedding_dimensions:
            raise ValueError(
                f"embedding model returned {len(embedding)} dimensions; expected {settings.embedding_dimensions}"
            )
    return embeddings


async def ingest_text(
    tenant_id: str,
    file_name: str,
    content: str,
    settings: Settings,
) -> IngestionResult:
    chunks = chunk_text(content)
    embeddings = await embed_texts(settings, chunks)

    async with tenant_transaction(tenant_id) as transaction:
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

    return IngestionResult(chunks_inserted=len(chunks), file_name=file_name, tenant_id=tenant_id)
