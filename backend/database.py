from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from pgvector.asyncpg import register_vector

from config import get_settings


settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.sqlalchemy_database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=True,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def register_pgvector(dbapi_connection: Any, _: Any) -> None:
    try:
        dbapi_connection.run_async(register_vector)
    except ValueError as exc:
        if "unknown type: public.vector" not in str(exc):
            raise


class TenantScopedConnection:
    def __init__(self, connection: AsyncConnection, tenant_id: str) -> None:
        self.connection = connection
        self.tenant_id = tenant_id

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return await self.connection.execute(statement, parameters or {})

    async def scalar(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return await self.connection.scalar(statement, parameters or {})


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0

    while index < len(sql):
        char = sql[index]
        current.append(char)

        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == ";" and quote is None:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []

        index += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)

    return statements


@asynccontextmanager
async def tenant_transaction(tenant_id: str) -> AsyncIterator[TenantScopedConnection]:
    if not tenant_id.strip():
        raise ValueError("tenant_id is required")

    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        yield TenantScopedConnection(connection=connection, tenant_id=tenant_id)


async def close_database() -> None:
    await engine.dispose()


async def bootstrap_database() -> bool:
    migration_path = Path(__file__).resolve().parent.parent / "database" / "migration.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")
    migration_applied = False
    expected_vector_type = f"vector({settings.embedding_dimensions})"

    async with engine.begin() as connection:
        vector_type = await connection.scalar(
            text(
                """
                SELECT format_type(attribute.atttypid, attribute.atttypmod)
                FROM pg_attribute attribute
                JOIN pg_class class ON class.oid = attribute.attrelid
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = 'public'
                  AND class.relname = 'document_chunks'
                  AND attribute.attname = 'embedding'
                  AND NOT attribute.attisdropped
                """
            )
        )
        if vector_type == expected_vector_type:
            has_chunk_index = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'document_chunks'
                          AND column_name = 'chunk_index'
                    )
                    """
                )
            )
            if not has_chunk_index:
                await connection.exec_driver_sql(
                    "ALTER TABLE document_chunks ADD COLUMN chunk_index INTEGER NOT NULL DEFAULT 0"
                )
                migration_applied = True
            return migration_applied

        if vector_type is not None:
            await connection.exec_driver_sql("DROP TABLE IF EXISTS audit_logs CASCADE")
            await connection.exec_driver_sql("DROP TABLE IF EXISTS document_chunks CASCADE")

        for statement in split_sql_statements(migration_sql):
            await connection.exec_driver_sql(statement)
        migration_applied = True

    if migration_applied:
        await engine.dispose()

    return migration_applied
