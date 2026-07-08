import json
import logging
from typing import Any

from redis.asyncio import Redis
from redisvl.schema import IndexSchema

from config import Settings

logger = logging.getLogger(__name__)


def _vector_bytes(vector: list[float]) -> bytes:
    import array

    return array.array("f", vector).tobytes()


def _escape_tag(value: str) -> str:
    escaped = []
    for char in value:
        if char.isalnum() or char == "_":
            escaped.append(char)
        else:
            escaped.append(f"\\{char}")
    return "".join(escaped)


class SemanticCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis: Redis = Redis.from_url(settings.redis_url, decode_responses=False)
        self.schema = IndexSchema.from_dict(
            {
                "index": {"name": settings.redis_cache_index, "prefix": "semantic-cache"},
                "fields": [
                    {"name": "tenant_id", "type": "tag"},
                    {"name": "query_text", "type": "text"},
                    {"name": "response_text", "type": "text"},
                    {"name": "metadata", "type": "text"},
                    {
                        "name": "query_vector",
                        "type": "vector",
                        "attrs": {
                            "dims": settings.embedding_dimensions,
                            "distance_metric": "cosine",
                            "algorithm": "hnsw",
                            "datatype": "float32",
                        },
                    },
                ],
            }
        )

    async def ensure_index(self) -> None:
        try:
            from redisvl.index import AsyncSearchIndex

            cache_keys = [key async for key in self.redis.scan_iter("semantic-cache:*")]
            if cache_keys:
                await self.redis.delete(*cache_keys)

            index = AsyncSearchIndex(self.schema)
            await index.set_client(self.redis)
            await index.create(overwrite=True)
        except Exception as exc:
            message = str(exc).lower()
            if "index already exists" not in message and "already exists" not in message:
                logger.exception("Unable to initialize Redis semantic cache index")
                raise

    async def get_semantic_cache(
        self,
        tenant_id: str,
        query_vector: list[float],
        threshold: float | None = None,
    ) -> dict[str, Any] | None:
        max_distance = 1.0 - (threshold or self.settings.cache_similarity_threshold)
        tenant_filter = _escape_tag(tenant_id)
        query = f"(@tenant_id:{{{tenant_filter}}})=>[KNN 1 @query_vector $vector AS distance]"
        result = await self.redis.execute_command(
            "FT.SEARCH",
            self.settings.redis_cache_index,
            query,
            "PARAMS",
            2,
            "vector",
            _vector_bytes(query_vector),
            "RETURN",
            4,
            "response_text",
            "query_text",
            "metadata",
            "distance",
            "SORTBY",
            "distance",
            "DIALECT",
            2,
        )

        if not result or result[0] == 0:
            return None

        fields = result[2]
        payload = {
            fields[index].decode("utf-8"): fields[index + 1].decode("utf-8")
            for index in range(0, len(fields), 2)
        }
        distance = float(payload.get("distance", "1"))
        if distance > max_distance:
            return None

        return {
            "response_text": payload["response_text"],
            "query_text": payload.get("query_text", ""),
            "metadata": json.loads(payload.get("metadata") or "{}"),
            "distance": distance,
            "similarity": 1.0 - distance,
        }

    async def set_semantic_cache(
        self,
        tenant_id: str,
        query_text: str,
        query_vector: list[float],
        response_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = f"semantic-cache:{tenant_id}:{abs(hash((tenant_id, query_text)))}"
        await self.redis.hset(
            key,
            mapping={
                "tenant_id": tenant_id,
                "query_text": query_text,
                "response_text": response_text,
                "metadata": json.dumps(metadata or {}),
                "query_vector": _vector_bytes(query_vector),
            },
        )

    async def invalidate_tenant(self, tenant_id: str) -> int:
        keys = [key async for key in self.redis.scan_iter(f"semantic-cache:{tenant_id}:*")]
        if not keys:
            return 0
        return int(await self.redis.delete(*keys))
