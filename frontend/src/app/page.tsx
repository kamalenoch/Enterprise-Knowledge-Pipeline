"use client";

import { Activity, Database, DollarSign, FileText, RefreshCw, Save, Send, Trash2, UploadCloud } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

type Tenant = {
  id: string;
  label: string;
};

type Telemetry = {
  latency_ms: number;
  cache_status: "HIT" | "MISS" | "BYPASS";
  database_lookup_ms: number;
  cache_lookup_ms?: number;
  llm_engine_ms?: number;
  tokens_used: number;
  tokens_bypassed?: number;
  currency_saved: number;
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

type DocumentSummary = {
  file_name: string;
  chunks: number;
  content_preview: string;
  updated_at: string;
};

type WorkspaceTab = "ingest" | "ingested" | "chat";

type TenantWorkspace = {
  activeTab: WorkspaceTab;
  fileName: string;
  ingestText: string;
  query: string;
  messages: ChatMessage[];
  documents: DocumentSummary[];
  documentsLoaded: boolean;
  selectedFileName: string;
  editContent: string;
  telemetry: Telemetry;
  logs: Record<string, unknown>[];
};

const tenants: Tenant[] = [
  { id: "tenant-finance-corp", label: "Tenant A (Finance Corp)" },
  { id: "tenant-health-inc", label: "Tenant B (Health Inc)" },
];

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const defaultTelemetry: Telemetry = {
  latency_ms: 0,
  cache_status: "BYPASS",
  database_lookup_ms: 0,
  tokens_used: 0,
  currency_saved: 0,
};

function createWorkspace(tenant: Tenant): TenantWorkspace {
  return {
    activeTab: "ingest",
    fileName: tenant.id === "tenant-finance-corp" ? "finance-corp-brief.txt" : "health-inc-brief.txt",
    ingestText: "",
    query: "",
    messages: [],
    documents: [],
    documentsLoaded: false,
    selectedFileName: "",
    editContent: "",
    telemetry: defaultTelemetry,
    logs: [],
  };
}

export default function Home() {
  const [tenantId, setTenantId] = useState<string>(tenants[0].id);
  const [workspaces, setWorkspaces] = useState<Record<string, TenantWorkspace>>(() =>
    Object.fromEntries(tenants.map((item) => [item.id, createWorkspace(item)])),
  );
  const [busy, setBusy] = useState(false);
  const tenant = tenants.find((item) => item.id === tenantId) ?? tenants[0];
  const workspace = workspaces[tenant.id];
  const {
    activeTab,
    documents,
    documentsLoaded,
    editContent,
    fileName,
    ingestText,
    logs,
    messages,
    query,
    selectedFileName,
    telemetry,
  } = workspace;

  const cacheClass = useMemo(() => {
    if (telemetry.cache_status === "HIT") return "border-emerald-500 bg-emerald-50 text-emerald-800";
    if (telemetry.cache_status === "MISS") return "border-amber-500 bg-amber-50 text-amber-800";
    return "border-slate-400 bg-white text-slate-700";
  }, [telemetry.cache_status]);

  function updateWorkspace(targetTenantId: string, update: (current: TenantWorkspace) => TenantWorkspace) {
    setWorkspaces((current) => ({
      ...current,
      [targetTenantId]: update(current[targetTenantId]),
    }));
  }

  function pushLogs(targetTenantId: string, nextLogs: Record<string, unknown>[]) {
    updateWorkspace(targetTenantId, (current) => ({
      ...current,
      logs: [...nextLogs, ...current.logs].slice(0, 80),
    }));
  }

  function switchTab(nextTab: WorkspaceTab) {
    updateWorkspace(tenant.id, (current) => ({ ...current, activeTab: nextTab }));
    if (nextTab === "ingested" && !workspace.documentsLoaded) {
      void fetchDocuments(tenant.id);
    }
  }

  async function fetchDocuments(targetTenantId: string) {
    const response = await fetch(`${apiBase}/api/documents`, {
      headers: { "X-Tenant-ID": targetTenantId },
    });
    const payload = await response.json();
    const nextDocuments = (payload.documents ?? []) as DocumentSummary[];
    updateWorkspace(targetTenantId, (current) => {
      const selectedStillExists = nextDocuments.some((item) => item.file_name === current.selectedFileName);
      return {
        ...current,
        documents: nextDocuments,
        documentsLoaded: true,
        selectedFileName: selectedStillExists ? current.selectedFileName : "",
        editContent: selectedStillExists ? current.editContent : "",
      };
    });
  }

  async function openDocument(targetTenantId: string, targetFileName: string) {
    const response = await fetch(`${apiBase}/api/documents/${encodeURIComponent(targetFileName)}`, {
      headers: { "X-Tenant-ID": targetTenantId },
    });
    const payload = await response.json();
    updateWorkspace(targetTenantId, (current) => ({
      ...current,
      selectedFileName: targetFileName,
      editContent: payload.document?.content ?? "",
    }));
  }

  async function saveDocument() {
    if (!selectedFileName || !editContent.trim()) return;
    const activeTenantId = tenant.id;
    setBusy(true);
    try {
      const response = await fetch(`${apiBase}/api/documents/${encodeURIComponent(selectedFileName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Tenant-ID": activeTenantId },
        body: JSON.stringify({ content: editContent }),
      });
      const payload = await response.json();
      pushLogs(activeTenantId, [{ event: "document.updated", ...payload }]);
      await fetchDocuments(activeTenantId);
      await openDocument(activeTenantId, selectedFileName);
    } finally {
      setBusy(false);
    }
  }

  async function removeDocument() {
    if (!selectedFileName) return;
    const activeTenantId = tenant.id;
    const fileToDelete = selectedFileName;
    setBusy(true);
    try {
      const response = await fetch(`${apiBase}/api/documents/${encodeURIComponent(fileToDelete)}`, {
        method: "DELETE",
        headers: { "X-Tenant-ID": activeTenantId },
      });
      const payload = await response.json();
      pushLogs(activeTenantId, [{ event: "document.deleted", ...payload }]);
      updateWorkspace(activeTenantId, (current) => ({ ...current, selectedFileName: "", editContent: "" }));
      await fetchDocuments(activeTenantId);
    } finally {
      setBusy(false);
    }
  }

  async function ingest(event: FormEvent) {
    event.preventDefault();
    if (!ingestText.trim()) return;
    const activeTenant = tenant;
    const activeFileName = fileName;
    const activeIngestText = ingestText;
    setBusy(true);
    try {
      const response = await fetch(`${apiBase}/api/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: activeTenant.id,
          file_name: activeFileName,
          content: activeIngestText,
        }),
      });
      const payload = await response.json();
      updateWorkspace(activeTenant.id, (current) => ({
        ...current,
        ingestText: "",
        telemetry: payload.telemetry ?? current.telemetry,
      }));
      pushLogs(activeTenant.id, payload.logs ?? [{ event: "ingest.failed", payload }]);
      if (workspace.documentsLoaded) {
        await fetchDocuments(activeTenant.id);
      }
    } finally {
      setBusy(false);
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    const activeTenant = tenant;
    const activeQuery = query;
    updateWorkspace(activeTenant.id, (current) => ({
      ...current,
      query: "",
      messages: [...current.messages, { role: "user", content: activeQuery }],
    }));
    setBusy(true);
    try {
      const response = await fetch(`${apiBase}/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Tenant-ID": activeTenant.id },
        body: JSON.stringify({ query: activeQuery, user_id: "dashboard-user" }),
      });
      const payload = await response.json();
      updateWorkspace(activeTenant.id, (current) => ({
        ...current,
        telemetry: payload.telemetry ?? current.telemetry,
        messages: [...current.messages, { role: "assistant", content: payload.answer ?? "No answer returned." }],
      }));
      pushLogs(activeTenant.id, payload.logs ?? [{ event: "query.failed", payload }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen p-4 lg:p-6">
      <div className="mx-auto grid max-w-7xl gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(380px,0.85fr)]">
        <section className="space-y-4">
          <div className="border border-line bg-white p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h1 className="text-xl font-semibold">Enterprise Knowledge Pipeline</h1>
              <span className="text-sm text-slate-600">{tenant.id}</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {tenants.map((item) => (
                <button
                  key={item.id}
                  onClick={() => setTenantId(item.id)}
                  className={`border px-3 py-2 text-left text-sm font-medium ${
                    tenant.id === item.id ? "border-ink bg-ink text-white" : "border-line bg-panel text-ink"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2 border border-line bg-white p-2">
            {[
              { id: "ingest" as const, label: "Ingest", icon: UploadCloud },
              { id: "ingested" as const, label: "Ingested", icon: FileText },
              { id: "chat" as const, label: "Chat", icon: Database },
            ].map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  onClick={() => switchTab(item.id)}
                  className={`flex items-center justify-center gap-2 border px-3 py-2 text-sm font-semibold ${
                    activeTab === item.id ? "border-ink bg-ink text-white" : "border-line bg-panel text-ink"
                  }`}
                  type="button"
                >
                  <Icon size={16} /> {item.label}
                </button>
              );
            })}
          </div>

          {activeTab === "ingest" && (
          <form onSubmit={ingest} className="border border-line bg-white p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <UploadCloud size={18} />
                <h2 className="font-semibold">Document Ingestion</h2>
              </div>
              <span className="border border-line bg-panel px-2 py-1 text-xs font-semibold text-slate-700">
                {tenant.label}
              </span>
            </div>
            <input
              value={fileName}
              onChange={(event) =>
                updateWorkspace(tenant.id, (current) => ({ ...current, fileName: event.target.value }))
              }
              className="mb-3 w-full border border-line px-3 py-2 text-sm"
              aria-label="File name"
            />
            <textarea
              value={ingestText}
              onChange={(event) =>
                updateWorkspace(tenant.id, (current) => ({ ...current, ingestText: event.target.value }))
              }
              className="h-32 w-full resize-none border border-line px-3 py-2 text-sm"
              aria-label="Document text"
              placeholder="Paste tenant-specific source text here."
            />
            <button className="mt-3 flex items-center gap-2 bg-ink px-4 py-2 text-sm font-semibold text-white" disabled={busy}>
              <UploadCloud size={16} /> Ingest Text
            </button>
          </form>
          )}

          {activeTab === "ingested" && (
            <section className="border border-line bg-white p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <FileText size={18} />
                  <h2 className="font-semibold">Ingested Documents</h2>
                </div>
                <div className="flex items-center gap-2">
                  <span className="border border-line bg-panel px-2 py-1 text-xs font-semibold text-slate-700">
                    {tenant.label}
                  </span>
                  <button
                    className="border border-line bg-panel p-2 text-ink"
                    onClick={() => fetchDocuments(tenant.id)}
                    title="Refresh documents"
                    type="button"
                  >
                    <RefreshCw size={16} />
                  </button>
                </div>
              </div>
              <div className="grid gap-3 lg:grid-cols-[280px_minmax(0,1fr)]">
                <div className="h-96 overflow-y-auto border border-line bg-panel p-2">
                  {documents.length === 0 && (
                    <div className="p-3 text-sm text-slate-600">
                      {documentsLoaded ? "No documents ingested for this tenant." : "Refresh to load ingested documents."}
                    </div>
                  )}
                  {documents.map((document) => (
                    <button
                      key={document.file_name}
                      onClick={() => openDocument(tenant.id, document.file_name)}
                      className={`mb-2 w-full border p-3 text-left text-sm ${
                        selectedFileName === document.file_name ? "border-ink bg-white" : "border-line bg-white"
                      }`}
                      type="button"
                    >
                      <div className="font-semibold">{document.file_name}</div>
                      <div className="mt-1 text-xs text-slate-500">{document.chunks} chunks</div>
                      <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-700">{document.content_preview}</p>
                    </button>
                  ))}
                </div>
                <div className="min-w-0">
                  <input
                    className="mb-3 w-full border border-line px-3 py-2 text-sm font-semibold"
                    value={selectedFileName || "Select a document to edit"}
                    readOnly
                    aria-label="Selected document"
                  />
                  <textarea
                    className="h-72 w-full resize-none border border-line px-3 py-2 text-sm leading-6"
                    value={editContent}
                    onChange={(event) =>
                      updateWorkspace(tenant.id, (current) => ({ ...current, editContent: event.target.value }))
                    }
                    disabled={!selectedFileName}
                    aria-label="Ingested document content"
                  />
                  <div className="mt-3 flex gap-2">
                    <button
                      className="flex items-center gap-2 bg-ink px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                      disabled={busy || !selectedFileName || !editContent.trim()}
                      onClick={saveDocument}
                      type="button"
                    >
                      <Save size={16} /> Save Re-Embed
                    </button>
                    <button
                      className="flex items-center gap-2 border border-line bg-white px-4 py-2 text-sm font-semibold text-ink disabled:opacity-50"
                      disabled={busy || !selectedFileName}
                      onClick={removeDocument}
                      type="button"
                    >
                      <Trash2 size={16} /> Delete
                    </button>
                  </div>
                </div>
              </div>
            </section>
          )}

          {activeTab === "chat" && (
          <section className="border border-line bg-white p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Database size={18} />
                <h2 className="font-semibold">Tenant Chat</h2>
              </div>
              <span className="border border-line bg-panel px-2 py-1 text-xs font-semibold text-slate-700">
                {tenant.label}
              </span>
            </div>
            <div className="mb-3 h-80 overflow-y-auto border border-line bg-panel p-3">
              {messages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`mb-3 max-w-[86%] border px-3 py-2 text-sm ${
                    message.role === "user" ? "ml-auto border-ink bg-white" : "border-line bg-white"
                  }`}
                >
                  <div className="mb-1 text-xs font-semibold uppercase text-slate-500">{message.role}</div>
                  <p className="whitespace-pre-wrap leading-6">{message.content}</p>
                </div>
              ))}
            </div>
            <form onSubmit={ask} className="flex gap-2">
              <input
                value={query}
                onChange={(event) =>
                  updateWorkspace(tenant.id, (current) => ({ ...current, query: event.target.value }))
                }
                className="min-w-0 flex-1 border border-line px-3 py-2 text-sm"
                aria-label="Prompt"
                placeholder="Ask against the active tenant corpus."
              />
              <button className="flex items-center gap-2 bg-ink px-4 py-2 text-sm font-semibold text-white" disabled={busy}>
                <Send size={16} /> Send
              </button>
            </form>
          </section>
          )}
        </section>

        <aside className="space-y-4">
          <section className="grid gap-3 sm:grid-cols-2">
            <div className={`border p-4 ${cacheClass}`}>
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold">Cache Status</span>
                <Activity size={18} />
              </div>
              <div className="mt-4 text-3xl font-bold">{telemetry.cache_status}</div>
            </div>
            <div className="border border-line bg-white p-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold">Savings</span>
                <DollarSign size={18} />
              </div>
              <div className="mt-4 text-3xl font-bold">${telemetry.currency_saved.toFixed(6)}</div>
            </div>
            <Metric label="System Processing" value={`${telemetry.latency_ms.toFixed(2)} ms`} />
            <Metric label="LLM Engine" value={`${(telemetry.llm_engine_ms ?? 0).toFixed(2)} ms`} />
            <Metric label="Database Lookup" value={`${telemetry.database_lookup_ms.toFixed(2)} ms`} />
            <Metric label="Tokens Used" value={String(telemetry.tokens_used)} />
          </section>

          <section className="border border-line bg-[#101317] p-4 text-white">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">Live Structural Logs</h2>
              <span className="text-xs text-slate-300">
                {tenant.label} - {logs.length} events
              </span>
            </div>
            <div className="h-[560px] overflow-y-auto border border-slate-700 bg-black p-3 font-mono text-xs leading-5 text-emerald-200">
              {logs.map((entry, index) => (
                <pre key={index} className="mb-3 whitespace-pre-wrap border-b border-slate-800 pb-3">
                  {JSON.stringify(entry, null, 2)}
                </pre>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-line bg-white p-4">
      <div className="text-sm font-semibold text-slate-600">{label}</div>
      <div className="mt-3 text-2xl font-bold">{value}</div>
    </div>
  );
}
