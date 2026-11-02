"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, STATUS_LABEL, type QueueItem } from "@/lib/api";

const FILTERS = ["", "needs_review", "approved", "auto_approved", "extracted"];
const TYPE_LABEL: Record<string, string> = {
  nda: "NDA",
  side_letter: "Side letter",
  investor_notice: "Investor notice",
  legal_notice: "Legal notice",
  other: "Other",
};

export function Queue() {
  const [status, setStatus] = useState("needs_review");
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    api<QueueItem[]>(`/review/documents${status ? `?status=${status}` : ""}`)
      .then((rows) => live && (setItems(rows), setError("")))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [status]);

  return (
    <main className="mx-auto w-full max-w-6xl px-5 py-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-900">Review queue</h1>
          <p className="mt-0.5 text-sm text-zinc-500">
            Documents the pipeline could not approve on its own.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg bg-zinc-100 p-1" role="tablist" aria-label="Status">
          {FILTERS.map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={status === f}
              onClick={() => (setItems(null), setStatus(f))}
              className={`rounded-md px-3 py-1 text-xs font-medium ${
                status === f ? "bg-white text-zinc-900 shadow-sm" : "text-zinc-600 hover:text-zinc-900"
              }`}
            >
              {f ? STATUS_LABEL[f] : "All"}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-zinc-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-4 py-2.5 font-medium">Document</th>
              <th className="px-4 py-2.5 font-medium">Type</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">Flags</th>
              <th className="px-4 py-2.5 text-right font-medium">Received</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {items?.map((d) => (
              <tr key={d.id} className="hover:bg-zinc-50">
                <td className="px-4 py-2.5">
                  <Link href={`/documents/${d.id}`} className="font-medium text-zinc-900 hover:underline">
                    {d.filename}
                  </Link>
                </td>
                <td className="px-4 py-2.5 text-zinc-600">
                  {d.doc_type ? TYPE_LABEL[d.doc_type] : "—"}
                </td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={d.status} />
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap gap-1.5">
                    {d.deviations > 0 && (
                      <span className="rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700">
                        {d.deviations} deviation{d.deviations > 1 ? "s" : ""}
                      </span>
                    )}
                    {d.unknowns > 0 && (
                      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-700">
                        {d.unknowns} unknown
                      </span>
                    )}
                    {d.flags
                      .filter((f) => !f.startsWith("playbook_"))
                      .map((f) => (
                        <span key={f} className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-600">
                          {f.replaceAll("_", " ")}
                        </span>
                      ))}
                  </div>
                </td>
                <td className="px-4 py-2.5 text-right text-zinc-500 tabular-nums">
                  {new Date(d.created_at).toLocaleDateString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items?.length === 0 && (
          <p className="px-4 py-10 text-center text-sm text-zinc-500">Nothing here.</p>
        )}
        {items === null && !error && (
          <p className="px-4 py-10 text-center text-sm text-zinc-500">Loading…</p>
        )}
        {error && <p className="px-4 py-10 text-center text-sm text-red-700">{error}</p>}
      </div>
    </main>
  );
}
