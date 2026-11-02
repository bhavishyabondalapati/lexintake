import { STATUS_LABEL, STATUS_STYLE } from "@/lib/api";

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? "bg-zinc-100 text-zinc-700 ring-zinc-500/20";
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}
