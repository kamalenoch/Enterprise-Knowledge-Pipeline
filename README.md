# Enterprise Knowledge Pipeline

Production-oriented MVP for a multi-tenant RAG pipeline with PostgreSQL RLS, pgvector search, Redis semantic caching, local CPU embeddings, Groq chat completions, FastAPI services, and a Next.js telemetry dashboard.

## Services

- `database/migration.sql` enables pgvector, creates tenant-isolated tables with `VECTOR(384)`, forces RLS, and adds an HNSW cosine index.
- `backend/` exposes `/api/ingest` and `/api/query` with tenant-scoped SQL transactions, local `all-MiniLM-L6-v2` embeddings, Groq `llama-3.3-70b-versatile` chat completions, and Redis vector cache lookups.
- `frontend/` provides a control-panel dashboard for tenant switching, ingestion, chat, telemetry, and raw logs.

## Run Locally

1. Start PostgreSQL with pgvector and Redis Stack:

```bash
docker compose up -d
```

This compose file publishes PostgreSQL on `localhost:5433` and Redis on `localhost:6379`. PostgreSQL still runs on port `5432` inside the container, but the host uses `5433` so it can coexist with a local PostgreSQL service.

2. Confirm the backend environment is present at `backend/.env`:

```env
DATABASE_URL=postgresql://enterprise_admin:super_secure_password_2026@localhost:5433/knowledge_pipeline
REDIS_URL=redis://localhost:6379
GROQ_API_KEY=your_groq_key
```

The backend automatically applies `database/migration.sql` on startup when `document_chunks` does not exist. It also rebuilds the local schema when an older `document_chunks.embedding` dimension does not match the configured `VECTOR(384)`, so no native `psql` command is required. The first ingestion or query may download the `all-MiniLM-L6-v2` model into the local Hugging Face cache.

3. Install and start the backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

4. Install and start the frontend:

```bash
cd frontend
npm install
npx next dev -H 127.0.0.1 -p 3000
```

5. Open the dashboard:

[http://127.0.0.1:3000](http://127.0.0.1:3000)

The frontend sends requests to `http://localhost:8000` by default. Override that with `NEXT_PUBLIC_API_BASE_URL` if the API runs elsewhere.
