type Props = {
  label: string;
  detail: string;
  state: "ok" | "down" | "checking";
};

const dot = {
  ok: "bg-emerald-500",
  down: "bg-red-500",
  checking: "bg-zinc-300 animate-pulse",
};

const text = { ok: "Healthy", down: "Unreachable", checking: "Checking…" };

export function StatusRow({ label, detail, state }: Props) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-zinc-900">{label}</p>
        <p className="truncate font-mono text-xs text-zinc-500">{detail}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2" data-testid={`status-${label}`}>
        <span className={`size-2 rounded-full ${dot[state]}`} />
        <span className="text-sm text-zinc-700">{text[state]}</span>
      </div>
    </div>
  );
}
