import Link from "next/link";

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="flex h-12 shrink-0 items-center border-b border-zinc-200 bg-white px-5">
        <Link href="/" className="text-sm font-semibold tracking-tight text-zinc-900">
          LexIntake
          <span className="ml-2 font-normal text-zinc-500">Legal review</span>
        </Link>
      </header>
      {children}
    </>
  );
}
