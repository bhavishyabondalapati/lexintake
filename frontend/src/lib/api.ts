// Two URLs for the same API:
// - On the server (inside Docker) Next.js reaches the API by its service name.
// - In the browser, requests come from the user's machine, so they need a public URL.

export function serverApiUrl(): string {
  return process.env.API_URL_INTERNAL ?? "http://localhost:8000";
}

export function browserApiUrl(): string {
  // NEXT_PUBLIC_* is inlined into the JS bundle at build time.
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export type Health = { status: string };
