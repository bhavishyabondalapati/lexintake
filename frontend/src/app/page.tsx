import { Suspense } from "react";

import { BrowserHealth } from "@/components/BrowserHealth";
import { StatusRow } from "@/components/StatusRow";
import { serverApiUrl, type Health } from "@/lib/api";

// Fetched on every request (no "use cache"), so it streams in behind <Suspense>.
async function ServerHealth() {
  const url = `${serverApiUrl()}/health`;
  let ok = false;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    ok = res.ok && ((await res.json()) as Health).status === "ok";
  } catch {
    ok = false;
  }
  return <StatusRow label="server" detail={url} state={ok ? "ok" : "down"} />;
}

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center px-4 py-16">
      <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">LexIntake</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900">
        Legal document intake &amp; triage
      </h1>
      <p className="mt-2 text-sm text-zinc-600">
        Scaffold is running. The review dashboard arrives in Phase 6.
      </p>

      <section className="mt-8 divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white">
        <h2 className="px-4 py-2 text-xs font-medium uppercase tracking-wider text-zinc-500">
          API health
        </h2>
        <Suspense fallback={<StatusRow label="server" detail="…" state="checking" />}>
          <ServerHealth />
        </Suspense>
        <BrowserHealth />
      </section>
    </main>
  );
}
