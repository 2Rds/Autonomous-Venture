"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Script from "next/script";

interface StatusSnapshot {
  paused: boolean;
  repo_rev: string;
  last_pipeline_run: string | null;
  pipeline_due: boolean;
  spend_requests_raw: string;
  pushed_at: string;
}

interface DraftSummary {
  slug: string;
  title: string;
  description: string;
}

interface DraftDetail extends DraftSummary {
  body: string;
}

type AuthState = "checking" | "not-telegram" | "unauthorized" | "ok";

function useInitData(): { authState: AuthState; initData: string } {
  const [state, setState] = useState<{ authState: AuthState; initData: string }>({
    authState: "checking",
    initData: "",
  });

  useEffect(() => {
    // window.Telegram is a browser global with no subscribable "ready" event -- this is a
    // one-time bridge read on mount (never available during SSR, so it can't be a lazy
    // useState initializer either), not state this component derives from a render.
    const webApp = window.Telegram?.WebApp;
    if (!webApp || !webApp.initData) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState({ authState: "not-telegram", initData: "" });
      return;
    }
    webApp.ready();
    webApp.expand();
    setState({ authState: "ok", initData: webApp.initData });
  }, []);

  return state;
}

async function miniappFetch(path: string, initData: string, init?: RequestInit) {
  const resp = await fetch(path, {
    ...init,
    headers: {
      ...init?.headers,
      "x-telegram-init-data": initData,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data?.error ?? `request failed: ${resp.status}`);
  }
  return data;
}

export default function DashboardPage() {
  const { authState, initData } = useInitData();

  return (
    <>
      <Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive" />
      <main className="min-h-screen bg-neutral-950 text-neutral-100 px-4 py-6">
        <h1 className="text-lg font-semibold mb-4">CreatorStacked dashboard</h1>
        {authState === "checking" && <p className="text-neutral-400">Loading…</p>}
        {authState === "not-telegram" && (
          <p className="text-neutral-400">
            Open this from the bot&apos;s <code>app</code> command or menu button. It only
            works inside Telegram.
          </p>
        )}
        {authState === "ok" && <Dashboard initData={initData} />}
      </main>
    </>
  );
}

function Dashboard({ initData }: { initData: string }) {
  const [tab, setTab] = useState<"status" | "drafts" | "spend">("status");
  const [authError, setAuthError] = useState<string | null>(null);

  if (authError) {
    return <p className="text-red-400">Couldn&apos;t authenticate: {authError}</p>;
  }

  return (
    <div>
      <nav className="flex gap-2 mb-4 border-b border-neutral-800 pb-2">
        {(["status", "drafts", "spend"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1 rounded text-sm capitalize ${
              tab === t ? "bg-neutral-100 text-neutral-950" : "bg-neutral-900 text-neutral-300"
            }`}
          >
            {t}
          </button>
        ))}
      </nav>
      {tab === "status" && <StatusTab initData={initData} onAuthError={setAuthError} />}
      {tab === "drafts" && <DraftsTab initData={initData} onAuthError={setAuthError} />}
      {tab === "spend" && <SpendTab initData={initData} onAuthError={setAuthError} />}
    </div>
  );
}

function StatusTab({
  initData,
  onAuthError,
}: {
  initData: string;
  onAuthError: (msg: string) => void;
}) {
  const [status, setStatus] = useState<StatusSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    miniappFetch("/api/miniapp/status", initData)
      .then((data) => setStatus(data))
      .catch((e: Error) => {
        if (e.message.includes("unauthorized") || e.message.includes("not operator")) {
          onAuthError(e.message);
        } else {
          setError(e.message);
        }
      });
  }, [initData, onAuthError]);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!status) return <p className="text-neutral-400">Loading status…</p>;

  return (
    <dl className="space-y-2 text-sm">
      <Row label="Paused" value={status.paused ? "yes" : "no"} />
      <Row label="Repo rev" value={status.repo_rev} />
      <Row label="Last pipeline run" value={status.last_pipeline_run ?? "never"} />
      <Row label="Pipeline due" value={status.pipeline_due ? "yes" : "no"} />
      <Row label="Snapshot pushed" value={new Date(status.pushed_at).toLocaleString()} />
    </dl>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-neutral-900 pb-2">
      <dt className="text-neutral-400">{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  );
}

function DraftsTab({
  initData,
  onAuthError,
}: {
  initData: string;
  onAuthError: (msg: string) => void;
}) {
  const [drafts, setDrafts] = useState<DraftSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<DraftDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [editBody, setEditBody] = useState("");
  const [busy, setBusy] = useState(false);

  const handleError = useCallback(
    (e: Error) => {
      if (e.message.includes("unauthorized") || e.message.includes("not operator")) {
        onAuthError(e.message);
      } else {
        setError(e.message);
      }
    },
    [onAuthError]
  );

  const loadList = useCallback(() => {
    miniappFetch("/api/miniapp/drafts", initData)
      .then((data) => setDrafts(data.drafts))
      .catch(handleError);
  }, [initData, handleError]);

  useEffect(loadList, [loadList]);

  function openDraft(slug: string) {
    miniappFetch(`/api/miniapp/drafts/${slug}`, initData)
      .then((data) => {
        setExpanded(data);
        setEditBody(data.body);
        setEditing(false);
      })
      .catch(handleError);
  }

  async function approve(slug: string) {
    setBusy(true);
    try {
      await miniappFetch(`/api/miniapp/drafts/${slug}/approve`, initData, { method: "POST" });
      setExpanded(null);
      loadList();
    } catch (e) {
      handleError(e as Error);
    } finally {
      setBusy(false);
    }
  }

  async function reject(slug: string) {
    setBusy(true);
    try {
      await miniappFetch(`/api/miniapp/drafts/${slug}/reject`, initData, { method: "POST" });
      setExpanded(null);
      loadList();
    } catch (e) {
      handleError(e as Error);
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit(slug: string) {
    setBusy(true);
    try {
      await miniappFetch(`/api/miniapp/drafts/${slug}/edit`, initData, {
        method: "POST",
        body: JSON.stringify({ body: editBody }),
      });
      setEditing(false);
      openDraft(slug);
    } catch (e) {
      handleError(e as Error);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="text-red-400">{error}</p>;

  if (expanded) {
    return (
      <div>
        <button className="text-sm text-neutral-400 mb-3" onClick={() => setExpanded(null)}>
          ← back to list
        </button>
        <h2 className="font-semibold mb-1">{expanded.title}</h2>
        <p className="text-neutral-400 text-sm mb-3">{expanded.description}</p>
        {editing ? (
          <textarea
            className="w-full h-64 bg-neutral-900 text-sm p-2 rounded mb-3"
            value={editBody}
            onChange={(e) => setEditBody(e.target.value)}
          />
        ) : (
          <pre className="whitespace-pre-wrap text-sm bg-neutral-900 p-3 rounded mb-3">
            {expanded.body}
          </pre>
        )}
        <div className="flex gap-2 flex-wrap">
          {editing ? (
            <>
              <ActionButton disabled={busy} onClick={() => saveEdit(expanded.slug)}>
                Save
              </ActionButton>
              <ActionButton disabled={busy} onClick={() => setEditing(false)}>
                Cancel
              </ActionButton>
            </>
          ) : (
            <>
              <ActionButton disabled={busy} onClick={() => approve(expanded.slug)}>
                Approve
              </ActionButton>
              <ActionButton disabled={busy} onClick={() => setEditing(true)}>
                Edit
              </ActionButton>
              <ActionButton disabled={busy} danger onClick={() => reject(expanded.slug)}>
                Reject
              </ActionButton>
            </>
          )}
        </div>
      </div>
    );
  }

  if (!drafts) return <p className="text-neutral-400">Loading drafts…</p>;
  if (drafts.length === 0) return <p className="text-neutral-400">No open drafts.</p>;

  return (
    <ul className="space-y-2">
      {drafts.map((d) => (
        <li key={d.slug}>
          <button
            className="w-full text-left bg-neutral-900 rounded p-3"
            onClick={() => openDraft(d.slug)}
          >
            <div className="font-medium text-sm">{d.title}</div>
            <div className="text-neutral-400 text-xs">{d.description}</div>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ActionButton({
  children,
  onClick,
  disabled,
  danger,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-3 py-1.5 rounded text-sm disabled:opacity-50 ${
        danger ? "bg-red-900 text-red-100" : "bg-neutral-100 text-neutral-950"
      }`}
    >
      {children}
    </button>
  );
}

function SpendTab({
  initData,
  onAuthError,
}: {
  initData: string;
  onAuthError: (msg: string) => void;
}) {
  const [status, setStatus] = useState<StatusSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    miniappFetch("/api/miniapp/status", initData)
      .then((data) => setStatus(data))
      .catch((e: Error) => {
        if (e.message.includes("unauthorized") || e.message.includes("not operator")) {
          onAuthError(e.message);
        } else {
          setError(e.message);
        }
      });
  }, [initData, onAuthError]);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!status) return <p className="text-neutral-400">Loading…</p>;

  return (
    <div>
      <p className="text-neutral-400 text-xs mb-3">
        Read-only. Approving a spend-request always happens in the Link app on your phone,
        never here.
      </p>
      <pre className="whitespace-pre-wrap text-sm bg-neutral-900 p-3 rounded">
        {status.spend_requests_raw}
      </pre>
    </div>
  );
}
