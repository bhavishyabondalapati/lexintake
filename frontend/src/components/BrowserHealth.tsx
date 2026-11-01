"use client";

import { useEffect, useState } from "react";

import { StatusRow } from "@/components/StatusRow";
import { browserApiUrl, type Health } from "@/lib/api";

// Runs in the browser, so it also proves CORS is set up on the API.
export function BrowserHealth() {
  const url = `${browserApiUrl()}/health`;
  const [state, setState] = useState<"ok" | "down" | "checking">("checking");

  useEffect(() => {
    fetch(url)
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((body: Health) => setState(body.status === "ok" ? "ok" : "down"))
      .catch(() => setState("down"));
  }, [url]);

  return <StatusRow label="browser" detail={url} state={state} />;
}
