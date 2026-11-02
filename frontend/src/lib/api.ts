// Two URLs for the same API:
// - On the server (inside Docker) Next.js reaches the API by its service name.
// - In the browser, requests come from the user's machine, so they need a public URL.

export function browserApiUrl(): string {
  // NEXT_PUBLIC_* is inlined into the JS bundle at build time.
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export type QueueItem = {
  id: string;
  filename: string;
  status: string;
  doc_type: string | null;
  doc_subtype: string | null;
  page_count: number | null;
  created_at: string;
  flags: string[];
  deviations: number;
  unknowns: number;
};

export type Box = { page: number; x0: number; y0: number; x1: number; y1: number };

export type Field = {
  name: string;
  value: string | number | boolean | null;
  page: number | null;
  quote: string | null;
  confidence: number | null;
  status: string;
  source: string | null;
  boxes: Box[];
  original_value: string | number | boolean | null;
  overridden: boolean;
};

export type RuleResult = {
  rule_id: string;
  field: string;
  outcome: "pass" | "deviation" | "unknown";
  value: unknown;
  page: number | null;
  quote: string | null;
  reason: string;
};

export type AuditEntry = {
  id: number;
  from_status: string | null;
  to_status: string;
  actor: string;
  detail: Record<string, unknown>;
  created_at: string;
};

export type ReviewDetail = QueueItem & {
  fields: Field[];
  playbook_results: RuleResult[] | null;
  review_reasons: { code: string; field: string | null; detail: string }[];
  audit: AuditEntry[];
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${browserApiUrl()}${path}`, init);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const STATUS_LABEL: Record<string, string> = {
  needs_review: "Needs review",
  auto_approved: "Auto-approved",
  approved: "Approved",
  extracted: "Extracted",
  parsed: "Parsed",
  failed: "Failed",
};

export const STATUS_STYLE: Record<string, string> = {
  needs_review: "bg-amber-50 text-amber-800 ring-amber-600/20",
  auto_approved: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  approved: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  failed: "bg-red-50 text-red-800 ring-red-600/20",
};
