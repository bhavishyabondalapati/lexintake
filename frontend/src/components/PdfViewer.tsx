"use client";

import type { PDFDocumentProxy } from "pdfjs-dist";
import { useEffect, useRef, useState } from "react";

import type { Box } from "@/lib/api";

const MAX_SCALE = 1.4;

export type Highlight = {
  page: number | null;
  quote: string | null;
  source: string | null; // "ocr" pages are highlighted from stored boxes, the rest by searching the text
  boxes: Box[];
};

type Rect = { left: number; top: number; width: number; height: number };

// Compare text with everything but letters and digits removed, so spacing, quotes and hyphens
// in the PDF's text layer can't break the match.
const squash = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

function PdfPage({
  pdf,
  number,
  highlight,
  scale,
}: {
  pdf: PDFDocumentProxy;
  number: number;
  highlight: Highlight | null;
  scale: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const first = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [textRects, setTextRects] = useState<Rect[]>([]);

  const ocr = highlight?.source === "ocr";
  const quote = highlight?.quote ?? null;
  const cited = highlight?.page ?? null;
  const boxes = highlight?.boxes;

  useEffect(() => {
    let cancelled = false;
    let task: { cancel: () => void } | null = null;
    (async () => {
      const page = await pdf.getPage(number);
      const viewport = page.getViewport({ scale });
      if (cancelled || !canvas.current) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.current.width = viewport.width * ratio;
      canvas.current.height = viewport.height * ratio;
      setSize({ width: viewport.width, height: viewport.height });
      const render = page.render({
        canvas: canvas.current,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      });
      task = render;
      await render.promise;
    })().catch(() => {});
    return () => {
      // A render still running at the old scale would paint over the new one.
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, number, scale]);

  // Digital pages: find the quote in the page's own text and box the text runs it covers.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!quote || ocr || (cited !== null && cited !== number)) return setTextRects([]);
      const pdfjs = await import("pdfjs-dist");
      const page = await pdf.getPage(number);
      const viewport = page.getViewport({ scale });
      const content = await page.getTextContent();
      const runs = content.items.filter((i) => "str" in i);
      let flat = "";
      const owner: number[] = [];
      runs.forEach((run, index) => {
        const s = squash(run.str);
        flat += s;
        for (let k = 0; k < s.length; k++) owner.push(index);
      });
      const needle = squash(quote);
      const at = needle ? flat.indexOf(needle) : -1;
      if (cancelled) return;
      if (at < 0) return setTextRects([]);
      const from = owner[at];
      const to = owner[at + needle.length - 1];
      setTextRects(
        runs.slice(from, to + 1).map((run) => {
          const t = pdfjs.Util.transform(viewport.transform, run.transform);
          const height = Math.hypot(t[2], t[3]);
          return { left: t[4], top: t[5] - height, width: run.width * scale, height };
        }),
      );
    })().catch(() => setTextRects([]));
    return () => {
      cancelled = true;
    };
  }, [pdf, number, quote, ocr, cited, scale]);

  const rects: Rect[] = ocr
    ? (boxes ?? [])
        .filter((b) => b.page === number)
        .map((b) => ({ left: b.x0 * scale, top: b.y0 * scale, width: (b.x1 - b.x0) * scale, height: (b.y1 - b.y0) * scale }))
    : textRects;

  useEffect(() => {
    // Scroll only the PDF pane (scrollIntoView would also move the window and the side panel).
    const mark = first.current;
    const pane = mark?.closest("section");
    if (!mark || !pane) return;
    const offset = mark.getBoundingClientRect().top - pane.getBoundingClientRect().top;
    pane.scrollTo({ top: pane.scrollTop + offset - pane.clientHeight / 3, behavior: "smooth" });
  }, [rects.length, quote]);

  return (
    <div
      className="relative mx-auto mb-4 bg-white shadow-sm ring-1 ring-zinc-200"
      style={{ width: size.width || undefined, height: size.height || undefined }}
      data-page={number}
    >
      <canvas ref={canvas} style={{ width: size.width, height: size.height }} />
      {rects.map((r, i) => (
        <div
          key={i}
          ref={i === 0 ? first : undefined}
          data-testid="highlight"
          className="pointer-events-none absolute rounded-sm bg-yellow-300/50 ring-1 ring-yellow-500/70"
          style={{ left: r.left - 2, top: r.top - 1, width: r.width + 4, height: r.height + 2 }}
        />
      ))}
    </div>
  );
}

export function PdfViewer({ url, highlight }: { url: string; highlight: Highlight | null }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [scale, setScale] = useState(1);
  const box = useRef<HTMLDivElement>(null);

  // Fit the page to the pane, so nothing is cut off on a narrow window.
  useEffect(() => {
    if (!pdf || !box.current) return;
    const el = box.current;
    let width = 612;
    void pdf.getPage(1).then((p) => {
      width = p.getViewport({ scale: 1 }).width;
      setScale(Math.min(MAX_SCALE, (el.clientWidth - 8) / width));
    });
    const watch = new ResizeObserver(() => setScale(Math.min(MAX_SCALE, (el.clientWidth - 8) / width)));
    watch.observe(el);
    return () => watch.disconnect();
  }, [pdf]);

  useEffect(() => {
    let task: { destroy: () => Promise<void> } | null = null;
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
      const loading = pdfjs.getDocument(url);
      task = loading;
      setPdf(await loading.promise);
    })().catch((e: Error) => setError(e.message));
    return () => {
      task?.destroy().catch(() => {});
    };
  }, [url]);

  if (error) return <p className="p-6 text-sm text-red-700">Could not load the PDF: {error}</p>;
  if (!pdf) return <p className="p-6 text-sm text-zinc-500">Loading PDF…</p>;
  return (
    <div ref={box}>
      {Array.from({ length: pdf.numPages }, (_, i) => (
        <PdfPage key={i} pdf={pdf} number={i + 1} highlight={highlight} scale={scale} />
      ))}
    </div>
  );
}
