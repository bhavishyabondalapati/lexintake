"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, browserApiUrl, post, type Field, type ReviewDetail } from "@/lib/api";

// pdf.js touches browser-only APIs, so it only loads in the browser.
const PdfViewer = dynamic(() => import("@/components/PdfViewer").then((m) => m.PdfViewer), {
  ssr: false,
});

const OUTCOME_STYLE = {
  pass: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  deviation: "bg-red-50 text-red-800 ring-red-600/20",
  unknown: "bg-zinc-100 text-zinc-700 ring-zinc-500/20",
};

const show = (v: unknown) => (v === null || v === undefined ? "none" : String(v));

function parseValue(text: string): string | number | boolean | null {
  const t = text.trim();
  if (t === "") return null;
  if (t === "true" || t === "false") return t === "true";
  return /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : t;
}

export function DocumentReview({ id }: { id: string }) {
  const [doc, setDoc] = useState<ReviewDetail | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState({ value: "", note: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api<ReviewDetail>(`/review/documents/${id}`);
      setDoc(d);
      setSelected((s) => s ?? d.playbook_results?.find((r) => r.outcome !== "pass")?.field ?? d.fields[0]?.name ?? null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!doc) {
    return <p className="p-6 text-sm text-zinc-500">{error || "Loading…"}</p>;
  }

  const field: Field | undefined = doc.fields.find((f) => f.name === selected);
  const canAct = doc.status === "needs_review" || doc.status === "extracted";
  const fieldsByName = new Map(doc.fields.map((f) => [f.name, f]));

  return (
    <div className="flex min-h-0 flex-1" style={{ height: "calc(100vh - 3rem)" }}>
      <section className="min-w-0 flex-1 overflow-y-auto bg-zinc-200/60 p-5" aria-label="Document">
        <PdfViewer
          url={`${browserApiUrl()}/review/documents/${id}/file`}
          highlight={field ? { page: field.page, quote: field.quote, source: field.source, boxes: field.boxes } : null}
        />
      </section>

      <aside className="w-[28rem] shrink-0 overflow-y-auto border-l border-zinc-200 bg-white" aria-label="Review">
        <div className="sticky top-0 z-10 border-b border-zinc-200 bg-white px-5 py-4">
          <Link href="/" className="text-xs text-zinc-500 hover:text-zinc-900">
            ← Queue
          </Link>
          <div className="mt-1 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold text-zinc-900">{doc.label}</h1>
              <p className="truncate text-xs text-zinc-400">{doc.filename}</p>
              <div className="mt-1 flex items-center gap-2 text-xs text-zinc-500">
                <StatusBadge status={doc.status} />
                <span>{doc.doc_type?.replace("_", " ") ?? "unclassified"}</span>
                <span>· {doc.page_count} pp</span>
              </div>
            </div>
            <button
              onClick={() => run(() => post(`/review/documents/${id}/approve`))}
              disabled={!canAct || busy}
              className="shrink-0 rounded-md bg-zinc-900 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-300"
            >
              Approve
            </button>
          </div>
          {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
        </div>

        {doc.playbook_results && (
          <section className="border-b border-zinc-100 px-5 py-4" aria-label="Playbook">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Playbook</h2>
            <ul className="mt-2 space-y-1.5">
              {doc.playbook_results.map((r) => (
                <li key={r.rule_id}>
                  <button
                    onClick={() => setSelected(r.field)}
                    className="flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-zinc-50"
                  >
                    <span
                      data-testid={`rule-${r.rule_id}`}
                      className={`mt-0.5 w-20 shrink-0 rounded px-1.5 py-0.5 text-center text-xs font-medium ring-1 ring-inset ${OUTCOME_STYLE[r.outcome]}`}
                    >
                      {r.outcome}
                    </span>
                    <span className="min-w-0 text-sm">
                      <span className="font-medium text-zinc-900">{r.rule_id.replaceAll("_", " ")}</span>
                      <span className="block text-xs text-zinc-500">{r.reason}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="border-b border-zinc-100 px-5 py-4" aria-label="Fields">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Extracted fields</h2>
          <ul className="mt-2 divide-y divide-zinc-100">
            {doc.fields.map((f) => (
              <li key={f.name} className={`py-2 ${selected === f.name ? "bg-yellow-50/70" : ""}`}>
                <div className="flex items-start justify-between gap-3 px-2">
                  <button onClick={() => setSelected(f.name)} className="min-w-0 text-left">
                    <span className="block text-xs text-zinc-500">{f.name.replaceAll("_", " ")}</span>
                    <span className="block break-words text-sm font-medium text-zinc-900">
                      {show(f.value)}
                      {f.overridden && (
                        <span className="ml-2 text-xs font-normal text-blue-700">
                          overridden (was {show(f.original_value)})
                        </span>
                      )}
                    </span>
                  </button>
                  {canAct && editing !== f.name && (
                    <button
                      onClick={() => (setEditing(f.name), setDraft({ value: f.value === null ? "" : String(f.value), note: "" }))}
                      className="shrink-0 rounded border border-zinc-200 px-2 py-0.5 text-xs text-zinc-700 hover:bg-zinc-50"
                    >
                      Override
                    </button>
                  )}
                </div>
                {f.quote && selected === f.name && (
                  <blockquote className="mx-2 mt-1.5 border-l-2 border-yellow-400 pl-2.5 text-xs italic text-zinc-600">
                    “{f.quote}” <span className="not-italic text-zinc-400">p.{f.page}</span>
                  </blockquote>
                )}
                {editing === f.name && (
                  <form
                    className="mx-2 mt-2 space-y-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void run(async () => {
                        await post(`/review/documents/${id}/fields/${f.name}/override`, {
                          value: parseValue(draft.value),
                          note: draft.note,
                        });
                        setEditing(null);
                      });
                    }}
                  >
                    <input
                      aria-label="New value"
                      value={draft.value}
                      onChange={(e) => setDraft({ ...draft, value: e.target.value })}
                      placeholder="New value (empty = clause absent)"
                      className="w-full rounded border border-zinc-300 px-2 py-1 text-sm"
                    />
                    <input
                      aria-label="Reason"
                      value={draft.note}
                      onChange={(e) => setDraft({ ...draft, note: e.target.value })}
                      placeholder="Why (goes in the audit log)"
                      className="w-full rounded border border-zinc-300 px-2 py-1 text-sm"
                    />
                    <div className="flex gap-2">
                      <button disabled={busy} className="rounded bg-zinc-900 px-2.5 py-1 text-xs font-medium text-white">
                        Save override
                      </button>
                      <button type="button" onClick={() => setEditing(null)} className="text-xs text-zinc-600">
                        Cancel
                      </button>
                    </div>
                  </form>
                )}
              </li>
            ))}
          </ul>
        </section>

        {doc.review_reasons.length > 0 && (
          <section className="border-b border-zinc-100 px-5 py-4" aria-label="Reasons">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Why it is here</h2>
            <ul className="mt-2 space-y-1 text-sm text-zinc-700">
              {doc.review_reasons.map((r, i) => (
                <li key={i}>
                  <span className="font-medium">{r.code.replaceAll("_", " ")}</span>
                  {r.field && <span className="text-zinc-500"> · {fieldsByName.get(r.field)?.name ?? r.field}</span>}
                  {r.detail && <span className="block text-xs text-zinc-500">{r.detail}</span>}
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="px-5 py-4" aria-label="Audit trail">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Audit trail</h2>
          <ol className="mt-2 space-y-1.5 text-xs text-zinc-600">
            {doc.audit.map((a) => (
              <li key={a.id}>
                <span className="tabular-nums text-zinc-400">{new Date(a.created_at).toLocaleString()}</span>{" "}
                {a.detail.event === "override" ? (
                  <>
                    <b>{a.actor}</b> overrode {String(a.detail.field)}: {show(a.detail.old)} → {show(a.detail.new)}
                  </>
                ) : (
                  <>
                    <b>{a.actor}</b> {a.from_status ?? "new"} → {a.to_status}
                  </>
                )}
              </li>
            ))}
          </ol>
        </section>
      </aside>
    </div>
  );
}
